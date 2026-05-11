import time
import logging
import traceback
from decimal import Decimal, InvalidOperation
from typing import Optional, Tuple

from src.inventory.tracker import Venue
from src.core.types import Token, Address
from src.exchange.client import ExchangeClient
from src.pricing.v3_quoter import UniswapV3Pricer
from src.pricing.quoter_cache import wrap_quoter, GasPriceCache

from .fees import FeeStructure
from .signal import Signal, Direction

logger = logging.getLogger("Strategy.SignalGenerator")


class SignalGenerator:
    def __init__(
        self,
        exchange_client: ExchangeClient,
        pricing_module,
        inventory_tracker,
        fee_structure: FeeStructure,
        config: dict,
    ):
        self.exchange = exchange_client
        self.pricing = pricing_module
        self.inventory = inventory_tracker
        self.fees = fee_structure

        self.min_spread_bps = Decimal(str(config.get("min_spread_bps", "50")))
        self.min_profit_usd = Decimal(str(config.get("min_profit_usd", "5.0")))
        self.max_position_usd = Decimal(str(config.get("max_position_usd", "10000")))
        self.signal_ttl = float(config.get("signal_ttl_seconds", 5.0))
        self.cooldown = float(config.get("cooldown_seconds", 2.0))
        self.verbose = config.get("verbose", False)

        self.gas_units_v3_swap = int(config.get("gas_units_v3_swap", 220000))
        self.gas_buffer_bps = Decimal(str(config.get("gas_buffer_bps", "20")))
        self.eth_price_cache_seconds = float(
            config.get("eth_price_cache_seconds", 30.0)
        )
        self._eth_usd_cache: tuple[Decimal, float] | None = None

        # Inject token map from configuration
        self.token_map = config.get("token_map", {})
        self.dex_quote_map = config.get("dex_quote_map", {})

        self.last_eval: dict[str, dict[str, Decimal | str | bool]] = {}
        self.last_signal_time: dict[str, float] = {}

        # === OPTIMIZATION: Cached DEX quoter and gas price ===
        # These are lazily initialized on first use
        self._cached_quoter = None  # Wrapped UniswapV3Pricer with 300ms TTL
        self._gas_price_cache = None  # GasPriceCache with 15s TTL

        # Read cache TTL from config or use defaults
        from src.config.config import config as app_config

        self._quoter_ttl_ms = config.get(
            "rpc_cache_ttl_ms", app_config.RPC_CACHE_TTL_MS
        )
        self._gas_cache_ttl_s = config.get(
            "gas_cache_ttl_seconds", app_config.GAS_CACHE_TTL_SECONDS
        )

        logger.info(
            f"💾 SignalGenerator initialized with RPC caching: "
            f"quoter_ttl={self._quoter_ttl_ms}ms, gas_ttl={self._gas_cache_ttl_s}s"
        )

    def generate(
        self, pair: str, trade_size_usd: Decimal, dex_quote_token: str = "USDC"
    ) -> Optional[Signal]:
        """
        Attempt to generate a signal for the given pair and USD trade size.
        Returns Signal if opportunity found and validated, None otherwise.
        dex_quote_token: override the DEX quote token (e.g. 'USDC' for all pairs).
        """
        now = time.time()
        last_time = self.last_signal_time.get(pair, 0.0)
        if now - last_time < self.cooldown:
            remaining = max(0.0, self.cooldown - (now - last_time))
            logger.debug(
                "🔕 %s відхилено: cooldown %.2fs (залишилось %.2fs)",
                pair,
                self.cooldown,
                remaining,
            )
            return None

        try:
            trade_size_usd = Decimal(str(trade_size_usd))
        except (InvalidOperation, TypeError) as exc:
            logger.warning("Invalid trade_size_usd for %s: %s", pair, exc)
            return None

        if trade_size_usd <= Decimal("0"):
            logger.debug(
                "🔕 %s skipped: non-positive trade_size_usd=%s", pair, trade_size_usd
            )
            return None

        prices = self._fetch_prices(pair, trade_size_usd)
        if prices is None:
            logger.debug("🔕 %s відхилено: немає цін з CEX/DEX", pair)
            return None

        size = prices["size"]

        # Calculate spreads both directions
        spread_a = (
            (prices["dex_sell"] - prices["cex_ask"])
            / prices["cex_ask"]
            * Decimal("10000")
        )
        spread_b = (
            (prices["cex_bid"] - prices["dex_buy"])
            / prices["dex_buy"]
            * Decimal("10000")
        )

        # Pick better direction
        if spread_a > spread_b and spread_a >= self.min_spread_bps:
            direction = Direction.BUY_CEX_SELL_DEX
            spread, cex_price, dex_price = (
                spread_a,
                prices["cex_ask"],
                prices["dex_sell"],
            )
        elif spread_b >= self.min_spread_bps:
            direction = Direction.BUY_DEX_SELL_CEX
            spread, cex_price, dex_price = (
                spread_b,
                prices["cex_bid"],
                prices["dex_buy"],
            )
        else:
            best_spread = max(spread_a, spread_b)
            if spread_a >= spread_b:
                direction = Direction.BUY_CEX_SELL_DEX
                cex_price = prices["cex_ask"]
                dex_price = prices["dex_sell"]
            else:
                direction = Direction.BUY_DEX_SELL_CEX
                cex_price = prices["cex_bid"]
                dex_price = prices["dex_buy"]

            trade_value = size * cex_price
            gross_pnl = (best_spread / Decimal("10000")) * trade_value
            gas_cost_usd = self._estimate_gas_cost_usd()
            fee_bps = self.fees.total_fee_bps(trade_value, gas_cost_usd=gas_cost_usd)
            total_fees = (fee_bps / Decimal("10000")) * trade_value
            net_pnl = gross_pnl - total_fees
            inventory_ok = self._check_inventory(pair, direction, size, cex_price)

            self.last_eval[pair] = {
                "direction": direction.name,
                "size_usd": trade_value,
                "cex_price": cex_price,
                "dex_price": dex_price,
                "spread_bps": best_spread,
                "gross_pnl": gross_pnl,
                "fees": total_fees,
                "gas_cost_usd": gas_cost_usd,
                "net_pnl": net_pnl,
                "inventory_ok": inventory_ok,
            }

            logger.debug(
                "🔕 %s skipped: spread=%sbps | gross=$%s | fees=$%s | "
                "gas=$%s | net=$%s (min $%s) | inventory_ok=%s",
                pair,
                f"{best_spread:.1f}",
                f"{gross_pnl:.4f}",
                f"{total_fees:.4f}",
                f"{gas_cost_usd:.4f}",
                f"{net_pnl:.4f}",
                f"{self.min_profit_usd:.4f}",
                inventory_ok,
            )
            return None

        # Economics
        trade_value = size * cex_price
        gross_pnl = (spread / Decimal("10000")) * trade_value
        gas_cost_usd = self._estimate_gas_cost_usd()
        fee_bps = self.fees.total_fee_bps(trade_value, gas_cost_usd=gas_cost_usd)
        fees = (fee_bps / Decimal("10000")) * trade_value
        net_pnl = gross_pnl - fees

        inventory_ok = self._check_inventory(pair, direction, size, cex_price)
        within_limits = trade_value <= self.max_position_usd

        self.last_eval[pair] = {
            "direction": direction.name,
            "size_usd": trade_value,
            "cex_price": cex_price,
            "dex_price": dex_price,
            "spread_bps": spread,
            "gross_pnl": gross_pnl,
            "fees": fees,
            "gas_cost_usd": gas_cost_usd,
            "net_pnl": net_pnl,
            "inventory_ok": inventory_ok,
        }

        profit_floor = self.min_profit_usd
        if net_pnl < profit_floor:
            logger.debug(
                "🔕 %s skipped: spread=%sbps | gross=$%s | fees=$%s | "
                "gas=$%s | net=$%s (min $%s) | inventory_ok=%s",
                pair,
                f"{spread:.1f}",
                f"{gross_pnl:.4f}",
                f"{fees:.4f}",
                f"{gas_cost_usd:.4f}",
                f"{net_pnl:.4f}",
                f"{profit_floor:.4f}",
                inventory_ok,
            )
            return None

        signal = Signal.create(
            pair=pair,
            direction=direction,
            cex_price=cex_price,
            dex_price=dex_price,
            spread_bps=spread,
            size=size,
            expected_gross_pnl=gross_pnl,
            expected_fees=fees,
            expected_net_pnl=net_pnl,
            score=0.0,
            expiry=time.time() + self.signal_ttl,
            inventory_ok=inventory_ok,
            within_limits=within_limits,
        )

        self.last_signal_time[pair] = time.time()
        return signal

    def _in_cooldown(self, pair: str) -> bool:
        return time.time() - self.last_signal_time.get(pair, 0.0) < self.cooldown

    def _get_cached_quoter(self):
        """
        Lazily initialize and return cached UniswapV3Pricer.

        This reduces eth_call overhead by caching quotes for 300ms (configurable).
        Multiple calls to get_amount_out() within 300ms will reuse cached results.
        """
        if self._cached_quoter is None:
            if self.pricing is None or not hasattr(self.pricing, "client"):
                logger.warning(
                    "⚠️  Pricing module not available; quoter caching disabled"
                )
                # Create quoter without caching (fallback)
                from web3 import Web3

                return UniswapV3Pricer(Web3())

            # Create quoter and wrap with cache
            base_quoter = UniswapV3Pricer(self.pricing.client.w3)
            self._cached_quoter = wrap_quoter(base_quoter, ttl_ms=self._quoter_ttl_ms)
            logger.info(f"✅ Cached quoter initialized (TTL={self._quoter_ttl_ms}ms)")

        return self._cached_quoter

    def _get_gas_price(self):
        """
        Get gas price with caching (15s TTL).

        Reduces eth_feehistory calls from ~4K/day to ~6K/day.
        """
        if self._gas_price_cache is None:
            if self.pricing is None or not hasattr(self.pricing, "client"):
                logger.warning("⚠️  Pricing module not available; gas caching disabled")
                return self.pricing.client.get_gas_price() if self.pricing else None

            self._gas_price_cache = GasPriceCache(
                self.pricing.client, ttl_seconds=self._gas_cache_ttl_s
            )
            logger.info(
                f"✅ Gas price cache initialized (TTL={self._gas_cache_ttl_s}s)"
            )

        return self._gas_price_cache.get_gas_price()

    def _get_eth_usd_price(self) -> Decimal:
        now = time.time()
        if (
            self._eth_usd_cache
            and now - self._eth_usd_cache[1] < self.eth_price_cache_seconds
        ):
            return self._eth_usd_cache[0]

        ob = self.exchange.fetch_order_book("ETH/USDT")
        bid = Decimal(str(ob["bids"][0][0]))
        ask = Decimal(str(ob["asks"][0][0]))
        mid = (bid + ask) / Decimal("2")
        self._eth_usd_cache = (mid, now)
        return mid

    def _estimate_gas_cost_usd(self) -> Decimal:
        if self.pricing is None or not hasattr(self.pricing, "client"):
            return self.fees.gas_cost_usd

        try:
            if self.gas_units_v3_swap <= 0:
                raise ValueError("gas_units_v3_swap must be positive")

            eth_usd = self._get_eth_usd_price()

            # Use cached gas price to reduce eth_feehistory calls
            gas_price = self._get_gas_price()
            if gas_price:
                max_fee = gas_price.get_max_fee(priority="medium")
                gas_cost_wei = self.gas_units_v3_swap * max_fee
                gas_cost_eth = Decimal(gas_cost_wei) / Decimal("1000000000000000000")
                buffer_multiplier = (Decimal("10000") + self.gas_buffer_bps) / Decimal(
                    "10000"
                )
                return gas_cost_eth * eth_usd * buffer_multiplier
            else:
                # Fallback
                return self.pricing.client.estimate_gas_cost_usd(
                    self.gas_units_v3_swap, eth_usd, buffer_bps=self.gas_buffer_bps
                )
        except Exception as exc:
            logger.warning(
                "Gas estimate failed; using default gas_cost_usd=%s (%s)",
                self.fees.gas_cost_usd,
                exc,
            )
            return self.fees.gas_cost_usd

    def _fetch_prices(self, pair: str, trade_size_usd: Decimal) -> Optional[dict]:
        """
        Fetch CEX order book and DEX price from PricingEngine (Week 2).
        If PricingEngine is not available, falls back to simulated DEX prices.
        """
        try:
            # Determine CEX symbol (internal pair may include a pool suffix like '#500')
            try:
                from scripts.arb_bot import PAIRS_CONFIG as _PAIRS_CONFIG

                cex_symbol = _PAIRS_CONFIG.get(pair, {}).get("cex_symbol", pair)
            except Exception:
                # Fallback: strip suffix like '#500' if present
                cex_symbol = pair.split("#")[0]

            ob = self.exchange.fetch_order_book(cex_symbol)
            cex_bid = Decimal(str(ob["bids"][0][0]))
            cex_ask = Decimal(str(ob["asks"][0][0]))

            if cex_ask <= Decimal("0"):
                raise ValueError("CEX ask price must be positive")

            size = trade_size_usd / cex_ask

            if self.pricing is not None:
                token_base, token_quote = self._pair_to_tokens(pair)
                dex_quote_symbol = self.dex_quote_map.get(pair, token_quote.symbol)

                if dex_quote_symbol not in self.token_map:
                    raise KeyError(
                        f"DEX quote token '{dex_quote_symbol}' is not configured for pair '{pair}'"
                    )

                token_dex_quote = Token(
                    address=Address(self.token_map[dex_quote_symbol]["address"]),
                    symbol=dex_quote_symbol,
                    decimals=self.token_map[dex_quote_symbol]["decimals"],
                )

                # Use CACHED quoter to avoid eth_call overhead
                # This single instance caches results for 300ms
                v3_pricer = self._get_cached_quoter()

                # Per-pair fee tier з PAIRS_CONFIG (GMX=10000, ETH=500, ARB=1000)
                try:
                    from scripts.arb_bot import PAIRS_CONFIG as _PAIRS_CONFIG

                    pool_fee_tier = _PAIRS_CONFIG.get(pair, {}).get("fee_tier", 10000)
                except Exception:
                    pool_fee_tier = 10000  # fallback: 1% (GMX default)

                # 1. DEX SELL (віддаємо GMX, отримуємо DEX quote, наприклад USDC)
                amount_in_base_wei = int(size * (Decimal(10) ** token_base.decimals))
                amount_out_quote_wei = v3_pricer.get_amount_out(
                    token_base, token_dex_quote, amount_in_base_wei, pool_fee_tier
                )

                if amount_out_quote_wei > 0:
                    quote_received = Decimal(amount_out_quote_wei) / Decimal(
                        10**token_dex_quote.decimals
                    )
                    dex_sell = quote_received / size
                else:
                    dex_sell = Decimal("0")

                # 2. DEX BUY (віддаємо DEX quote, отримуємо GMX)
                amount_in_quote_wei = int(
                    trade_size_usd * (Decimal(10) ** token_dex_quote.decimals)
                )
                amount_out_base_wei = v3_pricer.get_amount_out(
                    token_dex_quote, token_base, amount_in_quote_wei, pool_fee_tier
                )

                if amount_out_base_wei > 0:
                    base_received = Decimal(amount_out_base_wei) / Decimal(
                        10**token_base.decimals
                    )
                    dex_buy = trade_size_usd / base_received
                else:
                    dex_buy = Decimal("999999")
            else:
                mid = (cex_bid + cex_ask) / Decimal("2")
                dex_buy = mid * Decimal("1.005")
                dex_sell = mid * Decimal("1.008")

            if self.verbose:
                logger.info(
                    f"📊 MARKET {pair} | "
                    f"CEX (Bid: {cex_bid:.2f}, Ask: {cex_ask:.2f}) | "
                    f"DEX (Sell: {dex_sell:.2f}, Buy: {dex_buy:.2f})"
                )

            return {
                "cex_bid": cex_bid,
                "cex_ask": cex_ask,
                "dex_buy": dex_buy,
                "dex_sell": dex_sell,
                "size": size,
            }
        except Exception as e:
            logger.error(
                f"Error fetching prices for {pair}: {e}\n{traceback.format_exc()}"
            )
            logger.debug("🔕 %s відхилено: помилка отримання цін", pair)
            return None

    def _check_inventory(
        self, pair: str, direction: Direction, size: Decimal, price: Decimal
    ) -> bool:
        # Resolve token symbols safely (handles internal suffixes like '#500')
        token_base, token_quote = self._pair_to_tokens(pair)
        base = token_base.symbol
        quote = token_quote.symbol
        dex_quote = self.dex_quote_map.get(pair, quote)

        if direction == Direction.BUY_CEX_SELL_DEX:
            return (
                self.inventory.get_available(Venue.BINANCE, quote)
                >= size * price * Decimal("1.01")
                and self.inventory.get_available(Venue.WALLET, base) >= size
            )
        else:
            return self.inventory.get_available(
                Venue.BINANCE, base
            ) >= size and self.inventory.get_available(
                Venue.WALLET, dex_quote
            ) >= size * price * Decimal(
                "1.01"
            )

    def _pair_to_tokens(self, pair: str) -> Tuple[Token, Token]:
        """
        Resolves a pair string (e.g., 'ETH/USDT') into fully initialized Token objects.
        Validates token support and handles string normalization against the config-provided token_map.
        """
        try:
            # Strip pool suffixes like '#500' before resolving tokens
            clean_pair = pair.strip().upper().split("#")[0]
            symbols = clean_pair.split("/")

            if len(symbols) != 2:
                raise ValueError(
                    f"Invalid pair format: '{pair}'. Expected 'BASE/QUOTE'"
                )

            base_sym, quote_sym = symbols

            for sym in [base_sym, quote_sym]:
                if sym not in self.token_map:
                    logger.error(
                        f"Token '{sym}' is not supported in the configured token_map"
                    )
                    raise KeyError(f"Unsupported token symbol: {sym}")

            token_in = Token(
                address=Address(self.token_map[base_sym]["address"]),
                symbol=base_sym,
                decimals=self.token_map[base_sym]["decimals"],
            )

            token_out = Token(
                address=Address(self.token_map[quote_sym]["address"]),
                symbol=quote_sym,
                decimals=self.token_map[quote_sym]["decimals"],
            )

            return token_in, token_out

        except Exception as e:
            logger.critical(f"Failed to resolve tokens for pair '{pair}': {str(e)}")
            raise
