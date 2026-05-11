import asyncio
import time
import logging
from enum import Enum, auto
from typing import Optional
from decimal import Decimal
from dataclasses import dataclass, field, replace

from src.strategy.signal import Direction
from src.strategy.signal import Signal
from src.core.types import Address
from src.executor.recovery import CircuitBreaker, ReplayProtection

logger = logging.getLogger("Executor")


class ExecutorState(Enum):
    IDLE = auto()
    VALIDATING = auto()
    LEG1_PENDING = auto()
    LEG1_FILLED = auto()
    LEG2_PENDING = auto()
    DONE = auto()
    FAILED = auto()
    UNWINDING = auto()


@dataclass
class ExecutionContext:
    signal: Signal
    state: ExecutorState = ExecutorState.IDLE

    leg1_venue: str = ""
    leg1_order_id: Optional[str] = None
    leg1_fill_price: Optional[Decimal] = None
    leg1_fill_size: Optional[Decimal] = None
    leg1_success: bool = False

    leg2_venue: str = ""
    leg2_tx_hash: Optional[str] = None
    leg2_fill_price: Optional[Decimal] = None
    leg2_fill_size: Optional[Decimal] = None
    leg2_success: bool = False

    cex_order_id: Optional[str] = None
    dex_tx_hash: Optional[str] = None

    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    actual_net_pnl: Optional[Decimal] = None
    error: Optional[str] = None


@dataclass
class ExecutorConfig:
    leg1_timeout: float = 5.0
    leg2_timeout: float = 60.0
    min_fill_ratio: Decimal = Decimal("0.8")
    use_flashbots: bool = True
    simulation_mode: bool = True
    cex_slippage_bps: Decimal = Decimal("50")
    dex_slippage_pct: Decimal = Decimal("0.97")  # 3% tolerance for low-liquidity tokens
    dex_emergency_slippage_pct: Decimal = Decimal(
        "0.05"
    )  # 5% price move tolerance for emergency unwind
    default_arbitrum_gas_usd: Decimal = Decimal("0.10")


class Executor:
    """Execute arbitrage trades across CEX and DEX."""

    def __init__(
        self,
        exchange_client,
        pricing_module,
        inventory_tracker,
        config: Optional[ExecutorConfig] = None,
        token_map: dict[str, dict] | None = None,
        wallet_address: Address | None = None,
    ):
        self.exchange = exchange_client
        self.pricing = pricing_module
        self.inventory = inventory_tracker
        self.config = config or ExecutorConfig()
        self.token_map = token_map or {}
        self.wallet_address = wallet_address

        self.circuit_breaker = CircuitBreaker()
        self.replay_protection = ReplayProtection()

    @staticmethod
    def _normalized_pair(pair: str) -> str:
        return pair.split("#", 1)[0]

    def _cex_symbol_for_pair(self, pair: str) -> str:
        try:
            from scripts.arb_bot import PAIRS_CONFIG as _PAIRS_CONFIG

            return _PAIRS_CONFIG.get(pair, {}).get(
                "cex_symbol", self._normalized_pair(pair)
            )
        except Exception:
            return self._normalized_pair(pair)

    async def execute(self, signal: Signal) -> ExecutionContext:
        ctx = ExecutionContext(signal=signal)

        # Pre-flight checks
        if self.circuit_breaker.is_open():
            ctx.state = ExecutorState.FAILED
            ctx.error = "Circuit breaker open"
            return ctx

        if self.replay_protection.is_duplicate(signal):
            ctx.state = ExecutorState.FAILED
            ctx.error = "Duplicate signal"
            return ctx

        ctx.state = ExecutorState.VALIDATING
        if not signal.is_valid():
            ctx.state = ExecutorState.FAILED
            issues = signal.validation_issues()
            ctx.error = (
                "Signal invalid: " + "; ".join(issues) if issues else "Signal invalid"
            )
            logger.warning(
                "Signal rejected | pair=%s | id=%s | issues=%s | spread=%s | net_pnl=%s | score=%s | inventory_ok=%s | within_limits=%s",
                signal.pair,
                signal.signal_id,
                ", ".join(issues) if issues else "unknown",
                signal.spread_bps,
                signal.expected_net_pnl,
                signal.score,
                signal.inventory_ok,
                signal.within_limits,
            )
            return ctx

        # Execute based on leg order strategy
        if self.config.use_flashbots:
            ctx = await self._execute_dex_first(ctx)
        else:
            ctx = await self._execute_cex_first(ctx)

        # Record result
        self.replay_protection.mark_executed(signal)
        if ctx.state == ExecutorState.DONE:
            self.circuit_breaker.record_success()
        else:
            self.circuit_breaker.record_failure()

        ctx.finished_at = time.time()
        return ctx

    async def _execute_cex_first(self, ctx: ExecutionContext) -> ExecutionContext:
        """CEX leg first (default for non-Flashbots)."""
        signal = ctx.signal

        # Leg 1: CEX
        ctx.state = ExecutorState.LEG1_PENDING
        ctx.leg1_venue = "cex"

        try:
            leg1 = await asyncio.wait_for(
                self._execute_cex_leg(signal), timeout=self.config.leg1_timeout
            )
        except asyncio.TimeoutError:
            ctx.state = ExecutorState.FAILED
            ctx.error = "CEX timeout"
            return ctx

        if not leg1["success"]:
            ctx.state = ExecutorState.FAILED
            ctx.error = leg1.get("error", "CEX rejected")
            return ctx

        if leg1["filled"] / signal.size < self.config.min_fill_ratio:
            ctx.state = ExecutorState.FAILED
            ctx.error = "Partial fill below threshold"
            return ctx

        ctx.leg1_fill_price = leg1["price"]
        ctx.leg1_fill_size = leg1["filled"]
        ctx.leg1_success = True
        ctx.leg1_order_id = leg1.get("order_id")
        ctx.cex_order_id = leg1.get("order_id")
        ctx.state = ExecutorState.LEG1_FILLED

        # Leg 2: DEX
        ctx.state = ExecutorState.LEG2_PENDING
        ctx.leg2_venue = "dex"

        try:
            leg2 = await asyncio.wait_for(
                self._execute_dex_leg(signal, ctx.leg1_fill_size),
                timeout=self.config.leg2_timeout,
            )
        except asyncio.TimeoutError:
            ctx.state = ExecutorState.UNWINDING
            await self._unwind(ctx)
            ctx.state = ExecutorState.FAILED
            ctx.error = "DEX timeout - unwound"
            return ctx

        if not leg2["success"]:
            ctx.state = ExecutorState.UNWINDING
            await self._unwind(ctx)
            ctx.state = ExecutorState.FAILED
            ctx.error = "DEX failed - unwound"
            return ctx

        if leg2["filled"] / ctx.leg1_fill_size < self.config.min_fill_ratio:
            ctx.state = ExecutorState.UNWINDING
            await self._unwind(ctx)
            ctx.state = ExecutorState.FAILED
            ctx.error = "Leg2 partial fill below threshold - unwound"
            return ctx

        ctx.leg2_fill_price = leg2["price"]
        ctx.leg2_fill_size = leg2["filled"]
        ctx.leg2_success = True
        ctx.leg2_tx_hash = leg2.get("tx_hash")
        ctx.dex_tx_hash = leg2.get("tx_hash")
        ctx.actual_net_pnl = self._calculate_pnl(ctx)
        ctx.state = ExecutorState.DONE
        return ctx

    async def _execute_dex_first(self, ctx: ExecutionContext) -> ExecutionContext:
        """DEX leg first (when using Flashbots - failed tx = no cost)."""
        signal = ctx.signal

        # Leg 1: DEX
        ctx.state = ExecutorState.LEG1_PENDING
        ctx.leg1_venue = "dex"

        try:
            leg1 = await asyncio.wait_for(
                self._execute_dex_leg(signal, signal.size),
                timeout=self.config.leg2_timeout,
            )
        except asyncio.TimeoutError:
            ctx.state = ExecutorState.FAILED
            ctx.error = "DEX timeout"
            return ctx

        if not leg1["success"]:
            ctx.state = ExecutorState.FAILED
            ctx.error = "DEX failed (no cost via Flashbots)"
            return ctx

        if leg1["filled"] / signal.size < self.config.min_fill_ratio:
            ctx.state = ExecutorState.FAILED
            ctx.error = "Partial DEX fill below threshold"
            return ctx

        ctx.leg1_fill_price = leg1["price"]
        ctx.leg1_fill_size = leg1["filled"]
        ctx.leg1_success = True
        ctx.leg2_tx_hash = leg1.get("tx_hash")
        ctx.dex_tx_hash = leg1.get("tx_hash")
        ctx.state = ExecutorState.LEG1_FILLED

        # Leg 2: CEX
        ctx.state = ExecutorState.LEG2_PENDING
        ctx.leg2_venue = "cex"

        try:
            leg2 = await asyncio.wait_for(
                self._execute_cex_leg(signal, ctx.leg1_fill_size),
                timeout=self.config.leg1_timeout,
            )
        except asyncio.TimeoutError:
            ctx.state = ExecutorState.UNWINDING
            await self._unwind(ctx)
            ctx.state = ExecutorState.FAILED
            ctx.error = "CEX timeout after DEX - unwound"
            return ctx

        if not leg2["success"]:
            ctx.state = ExecutorState.UNWINDING
            await self._unwind(ctx)
            ctx.state = ExecutorState.FAILED
            ctx.error = "CEX failed after DEX - unwound"
            return ctx

        if leg2["filled"] / ctx.leg1_fill_size < self.config.min_fill_ratio:
            ctx.state = ExecutorState.UNWINDING
            await self._unwind(ctx)
            ctx.state = ExecutorState.FAILED
            ctx.error = "Leg2 partial fill below threshold - unwound"
            return ctx

        ctx.leg2_fill_price = leg2["price"]
        ctx.leg2_fill_size = leg2["filled"]
        ctx.leg2_success = True
        ctx.cex_order_id = leg2.get("order_id")
        ctx.actual_net_pnl = self._calculate_pnl(ctx)
        ctx.state = ExecutorState.DONE
        return ctx

    async def _execute_cex_leg(self, signal: Signal, size: Decimal = None) -> dict:
        actual_size = size or signal.size
        if self.config.simulation_mode:
            await asyncio.sleep(0.1)
            return {
                "success": True,
                "price": signal.cex_price * Decimal("1.0001"),
                "filled": actual_size,
                "order_id": "simulated",
            }
        # Real execution via ExchangeClient (Week 3 API)
        side = "buy" if signal.direction == Direction.BUY_CEX_SELL_DEX else "sell"

        slippage_multiplier = self.config.cex_slippage_bps / Decimal("10000")
        safe_price = (
            signal.cex_price * (Decimal("1") + slippage_multiplier)
            if side == "buy"
            else signal.cex_price * (Decimal("1") - slippage_multiplier)
        )
        result = self.exchange.create_limit_ioc_order(
            symbol=self._cex_symbol_for_pair(signal.pair),
            side=side,
            amount=actual_size,
            price=safe_price,
        )
        return {
            "success": result["status"] == "filled",
            "price": Decimal(str(result["avg_fill_price"])),
            "filled": Decimal(str(result["amount_filled"])),
            "order_id": result.get("id", ""),
            "error": result["status"],
        }

    async def _execute_dex_leg(self, signal: Signal, size: Decimal) -> dict:
        V3_ROUTER = "0xE592427A0AEce92De3Edee1F18E0157C05861564"
        V3_ROUTER_ABI = [
            {
                "inputs": [
                    {
                        "components": [
                            {
                                "internalType": "address",
                                "name": "tokenIn",
                                "type": "address",
                            },
                            {
                                "internalType": "address",
                                "name": "tokenOut",
                                "type": "address",
                            },
                            {"internalType": "uint24", "name": "fee", "type": "uint24"},
                            {
                                "internalType": "address",
                                "name": "recipient",
                                "type": "address",
                            },
                            {
                                "internalType": "uint256",
                                "name": "deadline",
                                "type": "uint256",
                            },
                            {
                                "internalType": "uint256",
                                "name": "amountIn",
                                "type": "uint256",
                            },
                            {
                                "internalType": "uint256",
                                "name": "amountOutMinimum",
                                "type": "uint256",
                            },
                            {
                                "internalType": "uint160",
                                "name": "sqrtPriceLimitX96",
                                "type": "uint160",
                            },
                        ],
                        "internalType": "struct ISwapRouter.ExactInputSingleParams",
                        "name": "params",
                        "type": "tuple",
                    }
                ],
                "name": "exactInputSingle",
                "outputs": [
                    {"internalType": "uint256", "name": "amountOut", "type": "uint256"}
                ],
                "stateMutability": "payable",
                "type": "function",
            }
        ]

        if self.config.simulation_mode:
            await asyncio.sleep(0.5)
            if signal.direction == Direction.BUY_CEX_SELL_DEX:
                price = signal.dex_price * Decimal("0.9998")
            else:
                price = signal.dex_price * Decimal("1.0002")
            return {
                "success": True,
                "price": price,
                "filled": size,
                "tx_hash": "simulated",
            }

        logger.info(
            f"🚀 EXECUTING DEX LEG: {signal.direction.name} {size} {signal.pair}"
        )

        try:
            if not self.token_map:
                raise ValueError("Executor missing token_map for DEX execution")
            if self.wallet_address is None:
                raise ValueError("Executor missing wallet address for DEX execution")

            clean_pair = self._normalized_pair(signal.pair)
            base_sym, quote_sym = clean_pair.split("/")

            try:
                from scripts.arb_bot import PAIRS_CONFIG as _PAIRS_CONFIG

                pair_cfg = _PAIRS_CONFIG.get(signal.pair, {})
                fee_tier = pair_cfg.get("fee_tier", 10000)
                dex_quote_sym = pair_cfg.get("dex_quote", quote_sym)
            except Exception:
                fee_tier = 10000
                dex_quote_sym = quote_sym

            if dex_quote_sym not in self.token_map:
                raise KeyError(
                    f"DEX quote token '{dex_quote_sym}' not in token_map for pair '{signal.pair}'"
                )

            token_in_addr = self.pricing.client.w3.to_checksum_address(
                self.token_map[base_sym]["address"]
            )
            token_dex_quote_addr = self.pricing.client.w3.to_checksum_address(
                self.token_map[dex_quote_sym]["address"]
            )

            if signal.direction == Direction.BUY_CEX_SELL_DEX:
                t_in, t_out = token_in_addr, token_dex_quote_addr
                decimals_in = self.token_map[base_sym]["decimals"]
                decimals_out = self.token_map[dex_quote_sym]["decimals"]
                amount_in_dec = size
                expected_out_dec = size * signal.dex_price
            else:
                t_in, t_out = token_dex_quote_addr, token_in_addr
                decimals_in = self.token_map[dex_quote_sym]["decimals"]
                decimals_out = self.token_map[base_sym]["decimals"]
                amount_in_dec = size * signal.dex_price
                expected_out_dec = size

            amount_in_wei = int(amount_in_dec * (Decimal(10) ** decimals_in))
            min_out_wei = int(
                (expected_out_dec * self.config.dex_slippage_pct)
                * (Decimal(10) ** decimals_out)
            )

            params = {
                "tokenIn": t_in,
                "tokenOut": t_out,
                "fee": fee_tier,
                "recipient": self.wallet_address.checksum,
                "deadline": int(time.time()) + 120,
                "amountIn": amount_in_wei,
                "amountOutMinimum": min_out_wei,
                "sqrtPriceLimitX96": 0,
            }

            router_contract = self.pricing.client.w3.eth.contract(
                address=self.pricing.client.w3.to_checksum_address(V3_ROUTER),
                abi=V3_ROUTER_ABI,
            )
            tx_data = router_contract.encode_abi(
                abi_element_identifier="exactInputSingle", args=[params]
            )

            from src.chain.builder import TransactionBuilder
            from src.core.types import Address, TokenAmount
            from src.core.wallet import WalletManager

            def send_tx():
                wallet = WalletManager.from_env()
                builder = (
                    TransactionBuilder(self.pricing.client, wallet)
                    .to(Address(V3_ROUTER))
                    .value(TokenAmount(raw=0, decimals=18))
                    .data(bytes.fromhex(tx_data[2:]))
                    .with_gas_estimate()
                    .with_gas_price("high")
                )
                return builder.send_and_wait(timeout=self.config.leg2_timeout)

            receipt = await asyncio.to_thread(send_tx)

            return {
                "success": receipt.status,
                "price": signal.dex_price,
                "filled": size if receipt.status else Decimal("0"),
                "tx_hash": receipt.tx_hash,
            }

        except Exception as e:
            logger.error(f"DEX Execution failed: {e}")
            return {"success": False, "error": str(e)}

    async def _unwind(self, ctx: ExecutionContext):
        """Market sell/buy to flatten stuck position."""
        if self.config.simulation_mode:
            await asyncio.sleep(0.1)
            logger.warning(
                f"[SIMULATION] Unwound {ctx.leg1_fill_size} {ctx.signal.pair} on {ctx.leg1_venue}"
            )
            return

        if not ctx.leg1_fill_size or ctx.leg1_fill_size == Decimal("0"):
            return  # Nothing to unwind
        signal = ctx.signal
        logger.critical(
            f"UNWINDING: Executing market order to flatten {ctx.leg1_fill_size} of {signal.pair}"
        )

        if ctx.leg1_venue == "cex":
            unwind_side = (
                "sell" if signal.direction == Direction.BUY_CEX_SELL_DEX else "buy"
            )
            cex_symbol = self._cex_symbol_for_pair(signal.pair)

            max_retries = 3
            for attempt in range(max_retries):
                try:
                    self.exchange.exchange.create_market_order(
                        symbol=cex_symbol,
                        side=unwind_side,
                        amount=str(ctx.leg1_fill_size),
                    )
                    logger.info("Unwind successful. Emergency flat position taken.")
                    break
                except Exception as e:
                    if attempt == max_retries - 1:
                        logger.error(
                            f"FATAL: Unwind failed after {max_retries} attempts! Manual intervention required. Error: {e}"
                        )
                    else:
                        logger.warning(
                            f"Unwind attempt {attempt + 1} failed: {e}. Retrying..."
                        )
                        # Cannot await sleep in non-async if it's sync, but _unwind is async so we can
                        # wait, but I'll use asyncio.sleep instead
                        await asyncio.sleep(1.0)

        elif ctx.leg1_venue == "dex":
            if not ctx.leg1_fill_size or ctx.leg1_fill_size == Decimal("0"):
                return

            signal = ctx.signal
            logger.critical(
                "UNWINDING DEX: attempting to reverse %s %s",
                ctx.leg1_fill_size,
                signal.pair,
            )

            emergency_direction = (
                Direction.BUY_CEX_SELL_DEX
                if signal.direction == Direction.BUY_DEX_SELL_CEX
                else Direction.BUY_DEX_SELL_CEX
            )
            if emergency_direction == Direction.BUY_DEX_SELL_CEX:
                emergency_dex_price = signal.dex_price * (
                    Decimal("1") + self.config.dex_emergency_slippage_pct
                )
            else:
                emergency_dex_price = signal.dex_price * (
                    Decimal("1") - self.config.dex_emergency_slippage_pct
                )

            emergency_signal = replace(
                signal,
                direction=emergency_direction,
                dex_price=emergency_dex_price,
                size=ctx.leg1_fill_size,
            )

            try:
                result = await self._execute_dex_leg(
                    emergency_signal, ctx.leg1_fill_size
                )
                if result.get("success"):
                    logger.info("DEX unwind successful. Emergency flat position taken.")
                else:
                    logger.error("DEX unwind failed: %s", result.get("error"))
            except Exception as exc:
                logger.critical("DEX unwind exception: %s", exc)

    def _calculate_pnl(self, ctx: ExecutionContext) -> Decimal:
        signal = ctx.signal
        if signal.direction == Direction.BUY_CEX_SELL_DEX:
            gross = (ctx.leg2_fill_price - ctx.leg1_fill_price) * ctx.leg1_fill_size
        else:
            gross = (ctx.leg1_fill_price - ctx.leg2_fill_price) * ctx.leg1_fill_size

        cex_fee = (
            ctx.leg1_fill_size * ctx.leg1_fill_price * Decimal("0.001")
        )  # 10 bps taker
        dex_fee = ctx.leg2_fill_size * ctx.leg2_fill_price * Decimal("0.003")  # 30 bps
        gas_fee = self.config.default_arbitrum_gas_usd
        return gross - cex_fee - dex_fee - gas_fee
