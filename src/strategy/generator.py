import time
import logging
import traceback
from decimal import Decimal
from typing import Optional, Tuple

from src.inventory.tracker import Venue
from src.core.types import Token, Address
from src.exchange.client import ExchangeClient

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

        self.min_spread_bps = config.get("min_spread_bps", Decimal("50"))
        self.min_profit_usd = config.get("min_profit_usd", Decimal("5.0"))
        self.max_position_usd = config.get("max_position_usd", Decimal("10_000"))
        self.signal_ttl = float(config.get("signal_ttl_seconds", 5.0))
        self.cooldown = float(config.get("cooldown_seconds", 2.0))

        # Inject token map from configuration
        self.token_map = config.get("token_map", {})

        self.last_signal_time: dict[str, float] = {}

    def generate(self, pair: str, size: Decimal) -> Optional[Signal]:
        """
        Attempt to generate a signal for the given pair and size.
        Returns Signal if opportunity found and validated, None otherwise.
        """
        if self._in_cooldown(pair):
            return None

        prices = self._fetch_prices(pair, size)
        if prices is None:
            return None

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
            return None

        # Economics
        trade_value = size * cex_price
        gross_pnl = (spread / Decimal("10000")) * trade_value
        fees = (self.fees.total_fee_bps(trade_value) / Decimal("10000")) * trade_value
        net_pnl = gross_pnl - fees

        if net_pnl < self.min_profit_usd:
            return None

        # Validation
        inventory_ok = self._check_inventory(pair, direction, size, cex_price)
        within_limits = trade_value <= self.max_position_usd

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

    def _fetch_prices(self, pair: str, size: Decimal) -> Optional[dict]:
        """
        Fetch CEX order book and DEX price from PricingEngine (Week 2).
        If PricingEngine is not available, falls back to simulated DEX prices.
        """
        try:
            ob = self.exchange.fetch_order_book(pair)
            cex_bid = Decimal(str(ob["bids"][0][0]))
            cex_ask = Decimal(str(ob["asks"][0][0]))

            if self.pricing is not None:
                token_base, token_quote = self._pair_to_tokens(pair)  # (ETH, USDT)
                gas_price = 1

                # 1. DEX SELL (Ми продаємо size ETH, отримуємо X USDT)
                dex_sell_quote = self.pricing.get_quote(
                    token_base,
                    token_quote,
                    int(size * 10**token_base.decimals),
                    gas_price,
                )
                usdt_received = Decimal(str(dex_sell_quote.expected_output)) / Decimal(
                    10**token_quote.decimals
                )
                dex_sell = usdt_received / size if size > 0 else Decimal("0")

                # 2. DEX BUY (Ми віддаємо USDT, отримуємо size ETH)
                # Оскільки AMM рахує "вперед", ми віддаємо приблизну суму (size * cex_ask) USDT і рахуємо ефективну ціну
                usdt_to_spend = size * cex_ask
                dex_buy_quote = self.pricing.get_quote(
                    token_quote,
                    token_base,
                    int(usdt_to_spend * 10**token_quote.decimals),
                    gas_price,
                )
                eth_received = Decimal(str(dex_buy_quote.expected_output)) / Decimal(
                    10**token_base.decimals
                )

                if eth_received > Decimal("0"):
                    dex_buy = usdt_to_spend / eth_received  # Ефективна ціна
                else:
                    dex_buy = Decimal("999999")  # Немає ліквідності
            else:
                mid = (cex_bid + cex_ask) / Decimal("2")
                dex_buy = mid * Decimal("1.005")
                dex_sell = mid * Decimal("1.008")

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
            }
        except Exception as e:
            logger.error(
                f"Error fetching prices for {pair}: {e}\n{traceback.format_exc()}"
            )
            return None

    def _check_inventory(
        self, pair: str, direction: Direction, size: Decimal, price: Decimal
    ) -> bool:
        base, quote = pair.split("/")
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
                Venue.WALLET, quote
            ) >= size * price * Decimal(
                "1.01"
            )

    def _pair_to_tokens(self, pair: str) -> Tuple[Token, Token]:
        """
        Resolves a pair string (e.g., 'ETH/USDT') into fully initialized Token objects.
        Validates token support and handles string normalization against the config-provided token_map.
        """
        try:
            clean_pair = pair.strip().upper()
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
