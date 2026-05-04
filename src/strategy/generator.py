import time
import logging
import traceback
from decimal import Decimal
from typing import Optional, Tuple

from src.inventory.tracker import Venue
from src.core.types import Token, Address
from src.exchange.client import ExchangeClient
from src.pricing.v3_quoter import UniswapV3Pricer

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
        self.verbose = config.get("verbose", False)

        # Inject token map from configuration
        self.token_map = config.get("token_map", {})
        self.dex_quote_map = config.get("dex_quote_map", {})

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

                v3_pricer = UniswapV3Pricer(self.pricing.client.w3)

                # Використовуємо 1% Fee-пул для GMX/USDC на Arbitrum.
                pool_fee_tier = 10000

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
                    (size * cex_ask) * (Decimal(10) ** token_dex_quote.decimals)
                )
                amount_out_base_wei = v3_pricer.get_amount_out(
                    token_dex_quote, token_base, amount_in_quote_wei, pool_fee_tier
                )

                if amount_out_base_wei > 0:
                    base_received = Decimal(amount_out_base_wei) / Decimal(
                        10**token_base.decimals
                    )
                    dex_buy = (size * cex_ask) / base_received
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
