import argparse
import asyncio
import csv
import os
import logging
import time
from decimal import Decimal
from datetime import datetime
from pathlib import Path
import tempfile

from src.exchange.client import ExchangeClient
from src.inventory.tracker import InventoryTracker, Venue
from src.inventory.pnl import PnLEngine, ArbRecord, TradeLeg
from src.strategy.fees import FeeStructure
from src.strategy.generator import SignalGenerator
from src.strategy.scorer import SignalScorer
from src.executor.engine import (
    ExecutionContext,
    Executor,
    ExecutorConfig,
    ExecutorState,
)
from src.config.config import config as app_config
from src.config.logger import setup_logger
from src.chain.client import ChainClient
from src.pricing.engine import PricingEngine
from src.core.types import Address, Token
from src.safety.alerts import TelegramAlert
from src.safety.killswitch import activate_kill_switch, is_kill_switch_active
from src.safety.limits import RiskLimits, RiskManager
from src.safety.validator import PreTradeValidator, ValidatorConfig

logger = logging.getLogger("ArbBot")

STABLE_ASSETS = {"USD", "USDT", "USDC", "DAI"}

PAIRS_CONFIG = {
    "GMX/USDT": {
        "v3_pool": app_config.GMX_USDC_POOL_V3,
        "v2_pool": "",
        "fee_tier": 10000,
        "dex_quote": "USDC",
        "enabled": False,
    },
    "ETH/USDT": {
        "v3_pool": app_config.ETH_USDC_POOL_V3,
        "v2_pool": "",
        "fee_tier": 500,
        "dex_quote": "USDC",
        "enabled": False,
    },
    "ARB/USDT": {
        "v3_pool": app_config.ARB_USDC_POOL_V3,
        "v2_pool": "",
        "fee_tier": 500,
        "dex_quote": "USDC",
        "enabled": False,
    },
    "CHIP/USDT": {
        "v3_pool": "0x49340Dbb8Fb5ECE2F9B594e77Ab774E65725e9D8",
        "v2_pool": "",
        "fee_tier": 100,
        "dex_quote": "USDC",
        "cex_symbol": "CHIP/USDT",
        "enabled": True,
    },
    # НОВИЙ — пул з fee 500, менша ліквідність але більший спред
    "CHIP/USDT#500": {
        "v3_pool": "0xe0a59cfc2e4081c2b7402c71ebcaa22c2b7992da",
        "v2_pool": "",
        "fee_tier": 500,
        "dex_quote": "USDC",
        "cex_symbol": "CHIP/USDT",
        "enabled": True,
    },
    "ESP/USDC": {
        "v3_pool": "0x15eb51a325cbce6c1cc8202a6f8a76224c5b7540",
        "v2_pool": "",
        "fee_tier": 100,
        "dex_quote": "USDC",
        "cex_symbol": "ESP/USDT",
        "enabled": True,
    },
    "ZRO/USDC": {
        "v3_pool": "0xeb1f77a0eca759c226d442f9ae5249121a555129",
        "v2_pool": "",
        "fee_tier": 10000,
        "dex_quote": "USDC",
        "cex_symbol": "ZRO/USDT",
        "enabled": False,
    },
}


def execution_to_arb_record(ctx) -> ArbRecord:
    """Bridge between Week 4's ExecutionContext and Week 3's ArbRecord."""
    signal = ctx.signal
    quote_asset = signal.pair.split("#")[0].split("/")[1]

    buy_leg = TradeLeg(
        id=f"{signal.signal_id}_buy",
        timestamp=datetime.fromtimestamp(ctx.started_at),
        venue=Venue.BINANCE if ctx.leg1_venue == "cex" else Venue.WALLET,
        symbol=signal.pair,
        side="buy",
        amount=ctx.leg1_fill_size or Decimal("0"),
        price=ctx.leg1_fill_price or Decimal("0"),
        fee=Decimal("0"),
        fee_asset=quote_asset,
    )

    sell_leg = TradeLeg(
        id=f"{signal.signal_id}_sell",
        timestamp=datetime.fromtimestamp(ctx.finished_at or ctx.started_at),
        venue=Venue.WALLET if ctx.leg2_venue == "dex" else Venue.BINANCE,
        symbol=signal.pair,
        side="sell",
        amount=ctx.leg2_fill_size or Decimal("0"),
        price=ctx.leg2_fill_price or Decimal("0"),
        fee=Decimal("0"),
        fee_asset=quote_asset,
    )

    return ArbRecord(
        id=signal.signal_id,
        timestamp=datetime.fromtimestamp(ctx.started_at),
        buy_leg=buy_leg,
        sell_leg=sell_leg,
    )


class ArbBot:
    @staticmethod
    def _is_valid_pool_address(addr: str) -> bool:
        if not addr:
            return False
        addr_str = str(addr).strip()
        if len(addr_str) != 42 or not addr_str.startswith("0x"):
            return False
        return all(c in "0123456789abcdefABCDEF" for c in addr_str[2:])

    def __init__(self, config: dict):
        self.is_test_mode = config.get("simulation", True)
        self.verbose = config.get("verbose", False)
        self.dry_run = config.get("dry_run", False)

        self.exchange = ExchangeClient(config)

        # Use HTTP_RPC_ENDPOINTS from config (with automatic rotation)
        # Synchronized with WSS_RPC_ENDPOINTS for dual failover support
        http_rpcs = app_config.HTTP_RPC_ENDPOINTS or [app_config.RPC_URL]
        if not http_rpcs:
            raise RuntimeError(
                "❌ Немає коректних HTTP RPC URL. Перевір .env (QN_HTTP_1, QN_HTTP_2, ...)"
            )

        logger.info(
            f"🔄 Initializing ChainClient with {len(http_rpcs)} HTTP + {len(app_config.WSS_RPC_ENDPOINTS or [])} WSS endpoints (sync rotation)"
        )
        self.chain_client = ChainClient(
            http_rpcs, simulation_mode=config.get("simulation", True)
        )

        self.pricing_engine = PricingEngine(
            chain_client=self.chain_client,
            fork_url=app_config.FORK_URL,
            rpc_router=self.chain_client.router,  # Pass router for WSS sync
        )

        self.inventory = InventoryTracker([Venue.BINANCE, Venue.WALLET])
        self.pnl_engine = PnLEngine()
        fee_config = config.get("fee_config", {})
        self.fees = FeeStructure.from_config(fee_config)

        risk_config = config.get("risk_config", {})
        self.risk_manager = RiskManager(RiskLimits.from_config(risk_config))
        self.pre_trade_validator = PreTradeValidator(
            ValidatorConfig.from_config(risk_config)
        )
        self.alerts = TelegramAlert.from_env()

        self.generator = SignalGenerator(
            self.exchange,
            self.pricing_engine,
            self.inventory,
            self.fees,
            config.get("signal_config", {}),
        )
        self.scorer = SignalScorer()

        addr_str = os.getenv("ADDRESS")
        self.wallet_address = Address(addr_str) if addr_str else None

        exec_config = ExecutorConfig(
            simulation_mode=config.get("simulation", True),
            use_flashbots=False,
            dex_slippage_pct=app_config.DEX_SLIPPAGE_PCT,
            dex_emergency_slippage_pct=app_config.DEX_EMERGENCY_SLIPPAGE_PCT,
        )
        self.executor = Executor(
            self.exchange,
            self.pricing_engine,
            self.inventory,
            exec_config,
            token_map=config.get("signal_config", {}).get("token_map", {}),
            wallet_address=self.wallet_address,
        )

        self.enable_v2_pools = app_config.ENABLE_V2_POOLS
        self.pairs_config: dict[str, dict] = {}
        self.dex_pools: list[Address] = []
        self.dex_quote_map: dict[str, str] = config.get("signal_config", {}).get(
            "dex_quote_map", {}
        )

        pairs_env = os.getenv("PAIRS", "").strip()
        pairs_filter = (
            {pair.strip() for pair in pairs_env.split(",") if pair.strip()}
            if pairs_env
            else None
        )

        for name, cfg in PAIRS_CONFIG.items():
            if pairs_filter is not None and name not in pairs_filter:
                logger.info("Pair %s disabled by PAIRS env", name)
                continue
            if not cfg.get("enabled", True):
                logger.info("Pool %s disabled in config", name)
                continue

            pair_cfg = dict(cfg)
            self.pairs_config[name] = pair_cfg

            v3_addr = cfg.get("v3_pool", "")
            if v3_addr and not self._is_valid_pool_address(str(v3_addr).strip()):
                logger.warning("Invalid V3 pool address for %s: %r", name, v3_addr)

            if not self.enable_v2_pools:
                continue

            raw_addr = cfg.get("v2_pool") or ""
            if not raw_addr:
                logger.warning("Skipping V2 pool %s: missing address", name)
                continue

            addr_str = str(raw_addr).strip()
            if not self._is_valid_pool_address(addr_str):
                logger.warning(
                    "Skipping V2 pool %s: invalid address %r", name, raw_addr
                )
                continue

            try:
                pool_addr = Address(addr_str)
            except (ValueError, TypeError) as exc:
                logger.warning(
                    "Skipping V2 pool %s: invalid address %r (%s)",
                    name,
                    raw_addr,
                    exc,
                )
                continue

            pair_cfg["v2_pool"] = addr_str
            self.dex_pools.append(pool_addr)

        if self.enable_v2_pools:
            if not self.dex_pools:
                logger.warning("V2 pools enabled but none valid; routing disabled.")
            else:
                logger.info("Active V2 pools for monitoring: %s", len(self.dex_pools))
        else:
            logger.info("V2 pools disabled; using V3-only pricing.")

        max_trade_usd = risk_config.get("max_trade_usd", app_config.MAX_TRADE_USD)
        self.trade_size_usd = Decimal(str(max_trade_usd))
        self.pairs = list(self.pairs_config.keys())

        self.running = False
        self._tick_lock = asyncio.Lock()
        self.recent_errors: list[float] = []

    def _heartbeat_path(self) -> Path:
        override = os.getenv("HEARTBEAT_FILE")
        if override:
            return Path(override)
        if os.name == "nt":
            return Path(tempfile.gettempdir()) / "arb_bot_heartbeat"
        return Path("/tmp/arb_bot_heartbeat")

    def _emit_heartbeat(self) -> None:
        try:
            path = self._heartbeat_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(time.time()), encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to write heartbeat: %s", exc)

    def _register_error(self, message: str) -> bool:
        now = time.time()
        cutoff = now - 3600.0
        self.recent_errors.append(now)
        self.recent_errors = [ts for ts in self.recent_errors if ts >= cutoff]

        if len(self.recent_errors) > 50:
            logger.critical("%s", message)
            activate_kill_switch("Too many network/API errors (50+/hr)")
            self.alerts.send("Kill switch activated: Too many network/API errors")
            self.stop()
            return True
        return False

    def _is_stable_asset(self, asset: str) -> bool:
        return asset.upper() in STABLE_ASSETS

    def _price_asset_usd(self, asset: str) -> Decimal | None:
        for pair in self.pairs:
            try:
                base, quote = pair.split("/")
            except ValueError:
                continue
            if base != asset or not self._is_stable_asset(quote):
                continue
            try:
                order_book = self.exchange.fetch_order_book(pair)
            except Exception as exc:
                logger.warning("Failed to fetch order book for %s: %s", pair, exc)
                return None
            best_bid = order_book["best_bid"][0]
            best_ask = order_book["best_ask"][0]
            return (best_bid + best_ask) / Decimal("2")
        return None

    def _position_state(self, signal) -> tuple[Decimal, int, bool]:
        snapshot = self.inventory.snapshot()
        totals = snapshot.get("totals", {})
        clean_pair = signal.pair.split("#")[0]
        base, _ = clean_pair.split("/")
        min_open_usd = self.risk_manager.limits.min_open_position_usd

        price_cache: dict[str, Decimal] = {}
        open_assets: set[str] = set()

        for asset, amount in totals.items():
            if self._is_stable_asset(asset):
                continue
            price = price_cache.get(asset)
            if price is None:
                if asset == base:
                    price = signal.cex_price
                else:
                    price = self._price_asset_usd(asset)
                if price is None:
                    if self.verbose:
                        logger.info(
                            "Open position check skipped unpriced asset: %s", asset
                        )
                    continue
                price_cache[asset] = price
            asset_usd = amount * price
            if abs(asset_usd) >= min_open_usd:
                open_assets.add(asset)

        if self._is_stable_asset(base):
            current_position_usd = totals.get(base, Decimal("0"))
            is_new_position = False
        else:
            current_position_usd = totals.get(base, Decimal("0")) * signal.cex_price
            is_new_position = base not in open_assets

        return current_position_usd, len(open_assets), is_new_position

    def _active_clean_pairs(self) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        for pair_name in self.pairs:
            clean_pair = pair_name.split("#")[0]
            if "/" not in clean_pair:
                continue
            base, quote = clean_pair.split("/")
            pairs.append((base, quote))
        return pairs

    def _active_base_assets(self) -> set[str]:
        return {
            base
            for base, _ in self._active_clean_pairs()
            if not self._is_stable_asset(base)
        }

    def _active_cex_assets(self) -> set[str]:
        assets: set[str] = set()
        for pair_name, cfg in self.pairs_config.items():
            cex_symbol = cfg.get("cex_symbol", pair_name)
            clean_symbol = str(cex_symbol).split("#")[0]
            if "/" not in clean_symbol:
                continue
            base, quote = clean_symbol.split("/")
            assets.add(base)
            assets.add(quote)
        return assets

    def _active_pair_assets(self) -> set[str]:
        assets: set[str] = set()
        for base, quote in self._active_clean_pairs():
            assets.add(base)
            assets.add(quote)
        return assets

    def _active_dex_pair_assets(self) -> set[str]:
        """
        Get active assets for DEX, using dex_quote_map to map CEX symbols to DEX symbols.
        E.g., for CHIP/USDT pair: returns {CHIP, USDC} not {CHIP, USDT}
        """
        assets: set[str] = set()
        for pair_name in self.pairs:
            clean_pair = pair_name.split("#")[0]
            if "/" not in clean_pair:
                continue
            base, quote = clean_pair.split("/")
            assets.add(base)
            # Use dex_quote_map to get DEX symbol (e.g., USDC instead of USDT)
            dex_quote = self.dex_quote_map.get(clean_pair, quote)
            assets.add(dex_quote)
        return assets

    async def _sync_balances(self):
        """Sync balances from both CEX and on-chain wallet."""
        try:
            # CEX Balances
            cex_balances = await asyncio.to_thread(self.exchange.fetch_balance)
            tracked_cex_assets = sorted(self._active_cex_assets())
            cex_parts: list[str] = []
            for asset in tracked_cex_assets:
                entry = cex_balances.get(asset, {})
                if isinstance(entry, dict):
                    free_balance = entry.get("free", "0")
                else:
                    free_balance = "0"
                cex_parts.append(f"{asset}: {free_balance}")
            if cex_parts:
                logger.info(
                    "CEX Balances (active pair assets) | %s", " | ".join(cex_parts)
                )
            else:
                logger.info(
                    "CEX Balances (active pair assets) | no active assets configured"
                )
            self.inventory.update_from_cex(Venue.BINANCE, cex_balances)

            # DEX Balances
            if self.is_test_mode:
                # TEST MODE (Fake money for simulating transactions)
                tracked_dex_assets = sorted(self._active_dex_pair_assets())
                wallet_balances: dict[str, Decimal] = {}
                for asset in tracked_dex_assets:
                    if self._is_stable_asset(asset):
                        wallet_balances[asset] = Decimal("10000.0")
                    else:
                        wallet_balances[asset] = Decimal("1.0")
                if wallet_balances:
                    log_line = "DEX Balances (TEST MODE, using USDC not USDT)"
                    for key in sorted(wallet_balances.keys()):
                        log_line += f" | {key}: {wallet_balances[key]}"
                    logger.info(log_line)
                else:
                    logger.info(
                        "DEX Balances (TEST MODE, using USDC not USDT) | no active assets configured"
                    )
                self.inventory.update_from_wallet(
                    Venue.WALLET,
                    wallet_balances,
                )
            elif self.wallet_address and self.pricing_engine is not None:
                tracked_dex_assets = sorted(self._active_dex_pair_assets())
                wallet_balances: dict[str, Decimal] = {}
                eth_decimal: Decimal | None = None

                for asset in tracked_dex_assets:
                    try:
                        if asset == "ETH":
                            if eth_decimal is None:
                                eth_wallet_bal = await asyncio.to_thread(
                                    self.chain_client.get_balance, self.wallet_address
                                )
                                eth_decimal = eth_wallet_bal.human
                            wallet_balances["ETH"] = eth_decimal
                            continue

                        token_cfg = self.generator.token_map.get(asset)
                        if not token_cfg:
                            logger.warning("Token %s not in token_map", asset)
                            wallet_balances[asset] = Decimal("0")
                            continue

                        token_addr = token_cfg.get("address")
                        token_decimals = int(token_cfg.get("decimals", 18))
                        if not token_addr:
                            logger.warning("Token %s has no address", asset)
                            wallet_balances[asset] = Decimal("0")
                            continue

                        try:
                            token = Token(
                                address=Address(str(token_addr)),
                                symbol=asset,
                                decimals=token_decimals,
                            )
                            token_balance = await asyncio.to_thread(
                                self.chain_client.get_balance,
                                self.wallet_address,
                                token,
                            )
                            wallet_balances[asset] = token_balance.human
                            logger.debug(
                                "Fetched %s balance: %s", asset, token_balance.human
                            )
                        except Exception as e:
                            prev_balance = self.inventory.get_available(
                                Venue.WALLET, asset
                            )
                            logger.warning(
                                "Failed to fetch %s balance from %s: %s",
                                asset,
                                token_addr,
                                e,
                            )
                            if prev_balance > Decimal("0"):
                                wallet_balances[asset] = prev_balance
                                logger.warning(
                                    "Using last known %s wallet balance due to RPC failure: %s",
                                    asset,
                                    prev_balance,
                                )
                            else:
                                wallet_balances[asset] = Decimal("0")
                    except Exception as e:
                        logger.error("Error processing token %s: %s", asset, e)

                if wallet_balances:
                    log_line = "DEX Balances (PROD, using USDC not USDT)"
                    for key in sorted(wallet_balances.keys()):
                        log_line += f" | {key}: {wallet_balances[key]}"
                    logger.info(log_line)
                else:
                    logger.info(
                        "DEX Balances (PROD, using USDC not USDT) | no active assets configured"
                    )

                self.inventory.update_from_wallet(
                    Venue.WALLET,
                    wallet_balances,
                )
        except Exception as e:
            logger.error(f"Error syncing balances: {e}")

    async def _verify_balances(self) -> None:
        if self.is_test_mode:
            return

        tokens = {pair.split("/")[0] for pair in self.pairs if "/" in pair}

        try:
            raw_balances = await asyncio.to_thread(self.exchange.exchange.fetch_balance)
        except Exception as exc:
            logger.error("Balance verification failed: %s", exc)
            return

        for token in tokens:
            if self._is_stable_asset(token):
                continue
            entry = raw_balances.get(token, {})
            if isinstance(entry, dict):
                real_free = Decimal(str(entry.get("free", 0) or 0))
            else:
                real_free = Decimal("0")

            internal_free = self.inventory.get_available(Venue.BINANCE, token)
            diff = abs(real_free - internal_free)
            if diff > Decimal("0.001"):
                logger.critical(
                    "Balance desync on %s: real=%s internal=%s",
                    token,
                    real_free,
                    internal_free,
                )
                activate_kill_switch(f"Balance Desync on {token}")
                self.alerts.send(f"Kill switch activated: Balance Desync on {token}")
                self.stop()
                return

    def _log_trade_receipt(self, ctx: ExecutionContext) -> None:
        actual_net_pnl = ctx.actual_net_pnl or Decimal("0")
        logger.info(
            "TRADE_RECEIPT | "
            f"pair={ctx.signal.pair} | "
            f"direction={ctx.signal.direction.name} | "
            f"size={ctx.signal.size:.4f} | "
            f"expected_spread={ctx.signal.spread_bps:.1f}bps | "
            f"actual_net_pnl=${actual_net_pnl:.2f} | "
            f"state={ctx.state.name} | "
            f"leg1_success={ctx.leg1_success} | "
            f"leg2_success={ctx.leg2_success}"
        )

    def _format_trade_alert(self, ctx: ExecutionContext) -> str:
        signal = ctx.signal
        dex_tx_hash = ctx.dex_tx_hash or "N/A"
        arbiscan_url = (
            f"https://arbiscan.io/tx/{ctx.dex_tx_hash}"
            if ctx.dex_tx_hash and ctx.dex_tx_hash != "simulated"
            else "N/A"
        )
        cex_order_id = ctx.cex_order_id or ctx.leg1_order_id or "N/A"
        actual_net_pnl = (
            ctx.actual_net_pnl if ctx.actual_net_pnl is not None else Decimal("0")
        )

        return (
            "Trade completed\n"
            f"Pair: {signal.pair}\n"
            f"Direction: {signal.direction.name}\n"
            f"Size: {signal.size}\n"
            f"Size USD: {signal.size * signal.cex_price}\n"
            f"CEX price: {signal.cex_price}\n"
            f"DEX price: {signal.dex_price}\n"
            f"Expected PnL: {signal.expected_net_pnl}\n"
            f"Actual PnL: {actual_net_pnl}\n"
            f"Leg1 venue: {ctx.leg1_venue} | fill size: {ctx.leg1_fill_size or 'N/A'} | fill price: {ctx.leg1_fill_price or 'N/A'}\n"
            f"Leg2 venue: {ctx.leg2_venue} | fill size: {ctx.leg2_fill_size or 'N/A'} | fill price: {ctx.leg2_fill_price or 'N/A'}\n"
            f"CEX order id: {cex_order_id}\n"
            f"DEX tx hash: {dex_tx_hash}\n"
            f"Arbiscan: {arbiscan_url}"
        )

    def _log_daily_summary(self) -> None:
        peak_capital = self.risk_manager._peak_capital_usd or Decimal("100")
        session_pnl = self.risk_manager.daily_loss_usd * Decimal("-1")
        logger.info("=== 📊 TRADING SESSION SUMMARY ===")
        logger.info(f"Current Capital: ${peak_capital:.2f}")
        logger.info(f"Session Daily PnL: ${session_pnl:.2f} (Note: tracks losses)")
        logger.info(f"Consecutive Losses: {self.risk_manager.consecutive_losses}")
        logger.info(
            "Trades in last hour: %s",
            len(self.risk_manager._trade_timestamps),
        )
        logger.info("===================================")

    def _estimate_total_capital_usd(self, signal) -> Decimal | None:
        snapshot = self.inventory.snapshot()
        totals = snapshot.get("totals", {})
        if not totals:
            return None

        clean_pair = signal.pair.split("#")[0]
        base, quote = clean_pair.split("/")
        total_usd = Decimal("0")
        unpriced_assets: list[str] = []

        for asset, amount in totals.items():
            if asset in {"USD", "USDT", "USDC"}:
                total_usd += amount
            elif asset == quote:
                total_usd += amount
            elif asset == base:
                total_usd += amount * signal.cex_price
            else:
                unpriced_assets.append(asset)

        if unpriced_assets and self.verbose:
            logger.info(
                f"Unpriced assets skipped in capital calc: {', '.join(unpriced_assets)}"
            )

        return total_usd if total_usd > Decimal("0") else None

    async def run(self):
        self.running = True

        if is_kill_switch_active():
            logger.critical("Kill switch active at startup. Exiting.")
            self.alerts.send("Kill switch active at startup. Bot stopped.")
            self.running = False
            return

        mode_name = (
            "SIMULATION (TEST)" if self.is_test_mode else "REAL BLOCKCHAIN (PROD)"
        )
        logger.info(f"Bot starting in {mode_name} mode...")
        if self.dry_run:
            logger.info("DRY RUN enabled: execution will be skipped.")
        self.alerts.send(f"Bot started in {mode_name} | dry_run={self.dry_run}")

        if (
            not self.is_test_mode
            and self.pricing_engine
            and self.enable_v2_pools
            and self.dex_pools
        ):
            logger.info("📡 Loading REAL DEX liquidity pools...")
            pools_loaded = False
            retry_count = 1
            max_retries = 6

            fallback_rpcs = [
                app_config.RPC_URL,
                "https://arb1.arbitrum.io/rpc",
                "https://rpc.ankr.com/arbitrum",
                "https://arbitrum.llamarpc.com",
                "https://arbitrum-one.publicnode.com",
            ]
            fallback_rpcs = [
                str(url).strip()
                for url in fallback_rpcs
                if url and str(url).strip().startswith("http")
            ]
            seen: set[str] = set()
            fallback_rpcs = [
                url for url in fallback_rpcs if not (url in seen or seen.add(url))
            ]

            while not pools_loaded and self.running:
                try:
                    await asyncio.sleep(2)
                    self.pricing_engine.load_pools(self.dex_pools)
                    pools_loaded = True
                    pool_count = len(self.pricing_engine.pools)
                    logger.info(
                        "✅ Successfully loaded %s pools from MAINNET.",
                        pool_count,
                    )
                    if pool_count == 0 or self.pricing_engine.router is None:
                        logger.warning("No valid V2 pools loaded; V2 routing disabled.")
                except Exception as e:
                    if retry_count >= max_retries:
                        logger.critical(
                            f"❌ Failed to load pools after {max_retries} attempts. Stopping bot to protect funds."
                        )
                        self.running = False
                        return

                    # RPC ROTATION
                    next_rpc = fallback_rpcs[retry_count % len(fallback_rpcs)]
                    logger.warning(f"⚠️ RPC Error on attempt {retry_count}: {e}")
                    logger.info(f"🔄 Switching to alternative RPC: {next_rpc}")

                    self.chain_client = ChainClient(
                        [next_rpc], simulation_mode=self.is_test_mode
                    )
                    self.pricing_engine = PricingEngine(
                        chain_client=self.chain_client,
                        fork_url=app_config.FORK_URL,
                        ws_url=None,
                    )

                    self.generator.pricing = self.pricing_engine
                    self.executor.pricing = self.pricing_engine

                    logger.info("⏳ Waiting 3 seconds before hitting new RPC...")
                    await asyncio.sleep(3)
                    retry_count += 1

        elif self.is_test_mode:
            logger.info("Test mode active. Using math formula for prices.")
            self.pricing_engine = None
            self.generator.pricing = None

        if self.running:
            await self._sync_balances()

        wss_active = False

        if not self.is_test_mode and self.pricing_engine:
            fallback_wss: list[str] = list(app_config.WSS_RPC_ENDPOINTS or [])

            # Fallback to single WSS_URL if no endpoints configured
            if not fallback_wss:
                configured_wss = getattr(app_config, "WSS_URL", None)
                if configured_wss:
                    configured_wss = str(configured_wss).strip()
                    if configured_wss.startswith("ws"):
                        fallback_wss.append(configured_wss)

            # Final fallback to public endpoint if nothing configured
            if not fallback_wss:
                fallback_wss = ["wss://arbitrum-one-rpc.publicnode.com"]
                logger.info(
                    "No valid WSS endpoints configured; using public fallback WSS."
                )

            from src.pricing.mempool import MempoolMonitor

            for ws_url in fallback_wss:
                if not self.running:
                    break

                try:
                    logger.info(f"🔌 Спроба підключення до WebSocket: {ws_url}")

                    async def on_price_update(pool_addr):
                        try:
                            await self._tick()
                        except Exception as e:
                            logger.error(f"WS Tick error: {e}")

                    monitor = MempoolMonitor(
                        ws_url,
                        http_url=app_config.RPC_URL,
                        callback=on_price_update,
                    )
                    self.pricing_engine.monitor = monitor

                    await asyncio.wait_for(
                        self.pricing_engine.monitor.connect(), timeout=10.0
                    )
                    block = await asyncio.wait_for(
                        self.pricing_engine.monitor.w3.eth.block_number,
                        timeout=5.0,
                    )
                    logger.info(f"   WSS підключено, поточний блок: {block}")

                    pool_addrs = [
                        p.checksum if hasattr(p, "checksum") else str(p)
                        for p in self.dex_pools
                    ]
                    remaining_wss = fallback_wss[fallback_wss.index(ws_url) :]

                    def make_task_error_handler(remaining):
                        def handler(task):
                            if task.cancelled():
                                return
                            exc = task.exception()
                            if exc:
                                logger.error(f"❌ WSS task впав: {exc}")
                                logger.warning(
                                    "🔄 WSS відключився, бот продовжує на HTTP polling"
                                )

                        return handler

                    task = asyncio.create_task(
                        self.pricing_engine.monitor.start_price_feed(pool_addrs)
                    )
                    task.add_done_callback(make_task_error_handler(remaining_wss))

                    wss_active = True
                    logger.info(f"✅ WSS моніторинг запущено: {ws_url}")
                    break

                except asyncio.TimeoutError:
                    logger.error(f"❌ WebSocket {ws_url} — timeout підключення (10s)")
                    logger.warning("🔄 Перемикаємось на наступний WSS вузол...")
                    wss_active = False
                    await asyncio.sleep(1)
                except Exception as e:
                    logger.error(f"❌ WebSocket {ws_url} впав або не підключився: {e}")
                    logger.warning("🔄 Перемикаємось на наступний WSS вузол...")
                    wss_active = False
                    await asyncio.sleep(1)

        if (
            not wss_active or self.is_test_mode or not self.pricing_engine
        ) and self.running:
            if not self.is_test_mode and self.pricing_engine:
                logger.critical("⚠️ Всі WebSocket з'єднання провалилися!")
            logger.info("Starting reliable HTTP Polling loop (1 req/sec)...")
            while self.running:
                try:
                    await self._tick()

                    await asyncio.sleep(1.0)
                except Exception as e:
                    logger.error(f"HTTP Tick error: {e}")
                    if self._register_error("Too many network/API errors (50+/hr)"):
                        break
                    await asyncio.sleep(5.0)
        elif wss_active and self.running:
            logger.info("👀 Основний цикл запущено (WSS + HTTP Backup)...")
            while self.running:
                try:
                    await self._tick()
                except Exception as e:
                    logger.error(f"Tick error: {e}")
                    if self._register_error("Too many network/API errors (50+/hr)"):
                        break
                await asyncio.sleep(2.5)

    async def _tick(self):
        if not self.running:
            return

        if self._tick_lock.locked():
            return

        async with self._tick_lock:
            self._emit_heartbeat()
            if self.executor.circuit_breaker.is_open():
                logger.info("Circuit breaker open")
                return

            if is_kill_switch_active():
                logger.critical("Kill switch active. Stopping bot.")
                self.alerts.send("Kill switch active. Bot stopped.")
                self.stop()
                return

            for pair, cfg in self.pairs_config.items():
                try:
                    loop = asyncio.get_running_loop()
                    signal = await asyncio.wait_for(
                        loop.run_in_executor(
                            None,
                            self.generator.generate,
                            pair,
                            self.trade_size_usd,
                        ),
                        timeout=10.0,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        f"⏳ Timeout waiting for price feed on {pair}. Skipping tick."
                    )
                    await self._log_trade_to_csv(None, "TIMEOUT", pair=pair)
                    await asyncio.sleep(0.3)
                    continue
                except Exception as e:
                    logger.error(f"❌ Error generating signal for {pair}: {e}")
                    self._register_error(str(e))
                    await self._log_trade_to_csv(None, "ERROR", pair=pair)
                    await asyncio.sleep(0.3)
                    continue

                if signal is None:
                    if self.verbose:
                        logger.info(
                            f"🔕 {pair}: No profitable spread found (Ignored by Generator)"
                        )
                    await self._log_trade_to_csv(None, "SKIPPED", pair=pair)

                    await asyncio.sleep(0.3)
                    continue

                signal.score = self.scorer.score(signal, self.inventory.get_skews())

                if signal.score < app_config.MIN_SCORE_THRESHOLD:
                    logger.info(
                        "SIGNAL | %s | %s | Spread: %.1f bps | "
                        "[REJECTED - SCORE] Score: %.1f",
                        pair,
                        signal.direction.name,
                        signal.spread_bps,
                        signal.score,
                    )
                    await self._log_trade_to_csv(signal, "REJECTED_SCORE")
                    await asyncio.sleep(0.3)
                    continue

                valid, reason = self.pre_trade_validator.validate(signal)
                if not valid:
                    logger.info(
                        "SIGNAL | %s | %s | Spread: %.1f bps | "
                        "[REJECTED - VALIDATOR] Reason: %s",
                        pair,
                        signal.direction.name,
                        signal.spread_bps,
                        reason,
                    )
                    await self._log_trade_to_csv(signal, "REJECTED_VALIDATOR")
                    await asyncio.sleep(0.3)
                    continue

                trade_notional_usd = signal.size * signal.cex_price
                total_capital_usd = self._estimate_total_capital_usd(signal)
                (
                    current_position_usd,
                    open_positions,
                    is_new_position,
                ) = self._position_state(signal)
                risk_result = self.risk_manager.pre_trade_check(
                    trade_notional_usd,
                    total_capital_usd,
                    current_position_usd=current_position_usd,
                    open_positions=open_positions,
                    is_new_position=is_new_position,
                )
                if not risk_result.allowed:
                    logger.info(
                        "SIGNAL | %s | %s | Spread: %.1f bps | "
                        "[REJECTED - RISK] Reason: %s",
                        pair,
                        signal.direction.name,
                        signal.spread_bps,
                        risk_result.reason,
                    )
                    await self._log_trade_to_csv(signal, "REJECTED_RISK")
                    if risk_result.hard_stop:
                        activate_kill_switch(risk_result.reason)
                        self.alerts.send(f"Kill switch activated: {risk_result.reason}")
                        self.stop()
                        return
                    await asyncio.sleep(0.3)
                    continue

                logger.info(
                    "SIGNAL | %s | %s | Spread: %.1f bps | "
                    "[ACCEPTED] Executing Size: %.4f, Expected PnL: $%.2f",
                    pair,
                    signal.direction.name,
                    signal.spread_bps,
                    signal.size,
                    signal.expected_net_pnl,
                )

                self.risk_manager.record_trade_attempt()

                if self.dry_run:
                    logger.info(
                        "DRY RUN: signal accepted but execution skipped "
                        f"pair={pair} notional_usd={trade_notional_usd}"
                    )
                    ctx = ExecutionContext(
                        signal=signal,
                        state=ExecutorState.DONE,
                        leg1_venue="cex",
                        leg2_venue="dex",
                        leg1_fill_price=signal.cex_price,
                        leg1_fill_size=signal.size,
                        leg2_fill_price=signal.dex_price,
                        leg2_fill_size=signal.size,
                        leg1_success=True,
                        leg2_success=True,
                        actual_net_pnl=signal.expected_net_pnl,
                        finished_at=time.time(),
                    )
                else:
                    ctx = await self.executor.execute(signal)

                self.scorer.record_result(pair, ctx.state == ExecutorState.DONE)
                self._log_trade_receipt(ctx)

                if ctx.state == ExecutorState.DONE and ctx.actual_net_pnl is not None:
                    arb_record = execution_to_arb_record(ctx)
                    self.pnl_engine.record(arb_record)
                    logger.info(f"SUCCESS: PnL=${ctx.actual_net_pnl:.2f}")
                    self.risk_manager.record_trade_result(
                        ctx.actual_net_pnl,
                        success=True,
                        total_capital_usd=total_capital_usd,
                    )
                    self.alerts.send(self._format_trade_alert(ctx))
                    await self._log_trade_to_csv(signal, "EXECUTED", ctx=ctx)
                    await self._log_executed_trade(ctx)
                else:
                    logger.warning(f"FAILED: {ctx.error}")
                    self.risk_manager.record_trade_result(
                        Decimal("0"),
                        success=False,
                        total_capital_usd=total_capital_usd,
                    )
                    if ctx.error:
                        self.alerts.send(f"Trade failed: {pair} error={ctx.error}")
                    await self._log_trade_to_csv(signal, "FAILED")

                await self._sync_balances()
                await self._verify_balances()

                await asyncio.sleep(0.3)

    async def _log_trade_to_csv(
        self, signal, status: str, pair: str = None, ctx=None
    ) -> None:
        """Автожурнал: записує кожен тік до trades_journal.csv для аудиту."""
        csv_path = Path("trades_journal.csv")
        header = [
            "timestamp",
            "pair",
            "status",
            "direction",
            "size_usd",
            "cex_price",
            "dex_price",
            "spread_bps",
            "net_pnl",
            "cex_order_id",
            "arbiscan_url",
        ]
        if not csv_path.exists():
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(header)

        eval_data = {}
        if signal is None and pair is not None:
            eval_data = getattr(self.generator, "last_eval", {}).get(pair, {})

        direction = (
            signal.direction.name if signal else str(eval_data.get("direction", "NONE"))
        )
        size_usd = (
            signal.size * signal.cex_price
            if signal
            else eval_data.get("size_usd", Decimal("0"))
        )
        cex_price = (
            signal.cex_price if signal else eval_data.get("cex_price", Decimal("0"))
        )
        dex_price = (
            signal.dex_price if signal else eval_data.get("dex_price", Decimal("0"))
        )
        spread_bps = (
            signal.spread_bps if signal else eval_data.get("spread_bps", Decimal("0"))
        )

        # Для EXECUTED: беремо реальний PnL з ctx, не очікуваний із сигналу
        if ctx is not None and ctx.actual_net_pnl is not None:
            net_pnl = ctx.actual_net_pnl
        elif signal:
            net_pnl = signal.expected_net_pnl
        else:
            net_pnl = eval_data.get("net_pnl", Decimal("0"))

        dex_tx = getattr(ctx, "dex_tx_hash", None) if ctx else None
        arbiscan_url = (
            f"https://arbiscan.io/tx/{dex_tx}"
            if dex_tx and dex_tx != "simulated"
            else "N/A"
        )
        cex_order_id = (
            (
                getattr(ctx, "cex_order_id", None)
                or getattr(self.executor, "cex_order_id", "N/A")
            )
            if signal
            else "N/A"
        )

        row = [
            datetime.now().isoformat(),
            pair or (signal.pair if signal else "N/A"),
            status,
            direction,
            str(size_usd),
            str(cex_price),
            str(dex_price),
            str(spread_bps),
            str(net_pnl),
            str(cex_order_id),
            arbiscan_url,
        ]

        try:
            with open(csv_path, "a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(row)
        except Exception as exc:
            logger.warning("Failed to write to trades_journal.csv: %s", exc)

    async def _log_executed_trade(self, ctx) -> None:
        """Окремий журнал тільки успішних угод → executed_trades.json.

        Записується атомарно одразу після підтвердження DEX транзакції.
        Не залежить від того, чи буде бот зупинений після.
        """
        import json as _json

        journal_path = Path("executed_trades.json")
        signal = ctx.signal
        dex_tx = ctx.dex_tx_hash or ""

        record = {
            "timestamp": datetime.now().isoformat(),
            "pair": signal.pair,
            "direction": signal.direction.name,
            "size": str(signal.size),
            "size_usd": str(signal.size * signal.cex_price),
            "cex_price": str(signal.cex_price),
            "dex_price": str(signal.dex_price),
            "spread_bps": str(signal.spread_bps),
            "expected_pnl": str(signal.expected_net_pnl),
            "actual_pnl": str(ctx.actual_net_pnl),
            "leg1_fill_price": str(ctx.leg1_fill_price),
            "leg1_fill_size": str(ctx.leg1_fill_size),
            "leg2_fill_price": str(ctx.leg2_fill_price),
            "leg2_fill_size": str(ctx.leg2_fill_size),
            "cex_order_id": ctx.cex_order_id or "N/A",
            "dex_tx_hash": dex_tx,
            "arbiscan_url": (
                f"https://arbiscan.io/tx/{dex_tx}"
                if dex_tx and dex_tx != "simulated"
                else "N/A"
            ),
            "execution_ms": (
                int((ctx.finished_at - ctx.started_at) * 1000)
                if ctx.finished_at
                else None
            ),
        }

        try:
            if journal_path.exists():
                with open(journal_path, "r", encoding="utf-8") as f:
                    trades = _json.load(f)
            else:
                trades = []
            trades.append(record)
            tmp_path = journal_path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                _json.dump(trades, f, indent=2, ensure_ascii=False)
            tmp_path.replace(journal_path)
            logger.info(
                "✅ Executed trade logged → executed_trades.json | "
                f"pair={signal.pair} pnl={ctx.actual_net_pnl} tx={dex_tx[:10] if dex_tx else 'N/A'}..."
            )
        except Exception as exc:
            logger.warning("Failed to write executed_trades.json: %s", exc)

    def stop(self):
        self.running = False


if __name__ == "__main__":
    print(" BOT ENTRY POINT REACHED")
    import signal
    import sys

    parser = argparse.ArgumentParser(description="Arbitrage Bot - Peanut Internship")
    parser.add_argument(
        "--mode",
        type=str,
        choices=["test", "prod"],
        default="test",
        help="Run mode: 'test' (simulation with fake DEX funds) or 'prod' (real trading)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run full checks but skip trade execution",
    )
    # 2. Checkbox to display all signals, even the rejected ones
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print all evaluated signals, even rejected ones",
    )
    args = parser.parse_args()

    log_prefix = "bot_dry_run" if args.dry_run else "bot"
    log_path = setup_logger(
        log_dir=app_config.LOG_DIR,
        filename_prefix=log_prefix,
        level=app_config.LOG_LEVEL,
    )

    is_test_mode = args.mode == "test"

    logger.info(
        f" INITIALIZING BOT IN {'TEST (SIMULATION)' if is_test_mode else 'PRODUCTION'} MODE ⚙️"
    )
    logger.info(f"Logging to {log_path}")
    if args.verbose:
        logger.info(" VERBOSE MODE ENABLED: Showing all rejected signals.")

    ARBITRUM_TOKENS = {
        "ETH": {
            "address": app_config.WETH_ADDRESS,
            "decimals": 18,
        },
        "ARB": {
            "address": "0x912CE59144191C1204E64559FE8253a0e49E6548",
            "decimals": 18,
        },
        "GMX": {
            "address": "0xfc5A1A6EB076a2C7aD06eD22C90d7E710E35ad0a",
            "decimals": 18,
        },
        "CHIP": {
            "address": "0x0C1c1C109FE34733fca54b82d7B46B75CFb71F6e",
            "decimals": 18,
        },
        "ESP": {
            "address": "0x3b8db18e69d6686ad9371a423afe3dd1065c94f1",
            "decimals": 18,
        },
        "ZRO": {
            "address": "0x6985884C4392D348587B19cb9eAAf157F13271cd",
            "decimals": 18,
        },
        "USDT": {
            "address": "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
            "decimals": 6,
        },
        "USDC": {
            "address": app_config.USDC_ADDRESS,
            "decimals": 6,
        },
    }

    bot_config = app_config.binance_config
    bot_config.update(
        {
            # Пари тепер керуються через PAIRS_CONFIG у цьому файлі
            "simulation": is_test_mode,
            "dry_run": args.dry_run,
            "verbose": args.verbose,
            "signal_config": {
                "min_spread_bps": app_config.MIN_SPREAD_BPS,
                "min_profit_usd": app_config.MIN_PROFIT_USD,
                "gas_buffer_bps": app_config.GAS_BUFFER_BPS,
                "gas_units_v3_swap": app_config.GAS_UNITS_V3_SWAP,
                "token_map": ARBITRUM_TOKENS,
                "dex_quote_map": {
                    "GMX/USDT": "USDC",
                    "ETH/USDT": "USDC",
                    "ARB/USDT": "USDC",
                    "CHIP/USDT": "USDC",
                    "CHIP/USDT#500": "USDC",
                    "ESP/USDC": "USDC",
                    "ZRO/USDC": "USDC",
                },
                "verbose": args.verbose,
            },
            "risk_config": {
                "max_trade_usd": str(app_config.MAX_TRADE_USD),
                "max_position_usd": str(app_config.MAX_POSITION_USD),
                "max_daily_loss_usd": str(app_config.MAX_DAILY_LOSS_USD),
                "max_drawdown_pct": str(app_config.MAX_DRAWDOWN_PCT),
                "max_trades_per_hour": app_config.MAX_TRADES_PER_HOUR,
                "max_consecutive_losses": app_config.MAX_CONSECUTIVE_LOSSES,
                "max_open_positions": app_config.MAX_OPEN_POSITIONS,
                "min_open_position_usd": str(app_config.MIN_OPEN_POSITION_USD),
                "max_signal_age_seconds": str(app_config.MAX_SIGNAL_AGE_SECONDS),
                "min_spread_bps": str(app_config.MIN_SPREAD_BPS),
                "max_spread_bps": str(app_config.MAX_SPREAD_BPS),
            },
        }
    )

    bot = ArbBot(bot_config)

    def handle_sigterm(signum, frame):
        logger.info("Received SIGTERM from Docker. Shutting down gracefully...")
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_sigterm)

    try:
        asyncio.run(bot.run())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped by user / system signal.")
    except Exception as e:
        logger.critical(f"Bot crashed with error: {e}")
    finally:
        try:
            bot._log_daily_summary()
        except Exception as exc:
            logger.error("Failed to log daily summary: %s", exc)
        logger.info("Sending stop alert to Telegram...")
        bot.alerts.send("Peanut Arb Bot has stopped working / Shut down.")
        try:
            hb_path = bot._heartbeat_path()
            if hb_path.exists():
                hb_path.unlink()
                logger.info("Heartbeat file cleaned up successfully.")
        except Exception as e:
            logger.error(f"Could not delete heartbeat file: {e}")
