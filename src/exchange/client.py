import asyncio
import logging
import time
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Dict

import ccxt

logger = logging.getLogger(__name__)

MIN_NOTIONAL = Decimal("5.0")
LOT_SIZE_STEP = Decimal("0.0001")
PRICE_TICK = Decimal("0.01")


def round_price_adaptive(price: Decimal, tick: Decimal) -> Decimal:
    """
    Round price to tick precision, with automatic downgrade for very low prices.
    For tokens < $0.10, use finer precision to preserve slippage buffer.
    """
    if tick <= Decimal("0"):
        raise ValueError("PRICE_TICK must be positive")

    # For very low prices, use finer tick to avoid wiping out slippage buffer
    if price > Decimal("0") and price < Decimal("0.10"):
        # Use 1/1000th of price as minimum tick, but cap at default tick
        adaptive_tick = min(tick, price / Decimal("1000"))
    else:
        adaptive_tick = tick

    ticks = (price / adaptive_tick).to_integral_value(rounding=ROUND_HALF_UP)
    return ticks * adaptive_tick


def round_quantity(qty: Decimal, step: Decimal) -> Decimal:
    if step <= Decimal("0"):
        raise ValueError("LOT_SIZE_STEP must be positive")
    steps = (qty / step).to_integral_value(rounding=ROUND_DOWN)
    return steps * step


def round_price(price: Decimal, tick: Decimal) -> Decimal:
    if tick <= Decimal("0"):
        raise ValueError("PRICE_TICK must be positive")
    ticks = (price / tick).to_integral_value(rounding=ROUND_HALF_UP)
    return ticks * tick


def get_price_tick_for_symbol(exchange_obj, symbol: str) -> Decimal:
    """
    Extract price precision (tick size) from Binance market info.
    Fallback to global PRICE_TICK if not available.
    """
    try:
        if hasattr(exchange_obj, "markets") and symbol in exchange_obj.markets:
            market = exchange_obj.markets[symbol]
            if market.get("precision") and market["precision"].get("price"):
                tick_str = str(market["precision"]["price"])
                # Handle scientific notation (e.g., '0.0001' or 'e-8')
                tick_val = Decimal(tick_str)
                if tick_val > Decimal("0"):
                    return tick_val
    except Exception as e:
        logger.warning(f"Failed to extract price tick for {symbol}: {e}")

    return PRICE_TICK


class InsufficientLiquidityError(Exception):
    """Raised when orderbook is empty or lacks liquidity."""

    pass


class OrderBookFetchError(Exception):
    """Raised when order book cannot be fetched due to network or rate limit."""

    pass


class ExchangeClient:
    """
    Wrapper around ccxt for Binance testnet.
    Handles rate limiting, error handling, and response normalization.
    """

    def __init__(self, config: dict):
        """
        Initialize with config dict containing apiKey, secret, sandbox flag.
        Must validate connection on init (fetch server time or status).
        """
        config = config.copy()
        config["enableRateLimit"] = True
        config["verbose"] = False

        options = config.get("options", {})
        options["defaultType"] = "spot"
        options["recvWindow"] = 10000
        options["adjustForTimeDifference"] = True
        config["options"] = options

        for key in ["pairs", "dex_pools", "trade_size", "simulation", "signal_config"]:
            config.pop(key, None)

        self.exchange = ccxt.binance(config)
        if config.get("sandbox"):
            self.exchange.set_sandbox_mode(True)
        self.exchange.options["adjustForTimeDifference"] = True

        try:
            self.exchange.load_time_difference()
        except Exception as e:
            logger.warning(f"Could not sync Binance time before load_markets: {e}")

        try:
            self.exchange.load_markets()
            logger.info("Successfully connected to Binance")
            # Cache symbol-specific price ticks after markets loaded
            self._price_ticks_cache = {}
        except ccxt.InvalidNonce as e:
            message = str(e)
            if "Timestamp for this request was" in message:
                logger.warning(
                    "Binance timestamp ahead error detected; syncing time and retrying."
                )
                try:
                    self.exchange.load_time_difference()
                    self.exchange.load_markets()
                    logger.info("Successfully connected to Binance after time sync")
                    self._price_ticks_cache = {}
                except Exception as retry_exception:
                    logger.critical(
                        f"Failed to connect to Binance after time sync: {retry_exception}"
                    )
                    raise
            else:
                logger.critical(f"Failed to connect to Binance: {e}")
                raise
        except (ccxt.AuthenticationError, ccxt.NetworkError) as e:
            logger.critical(f"Failed to connect to Binance: {e}")
            raise

    def get_price_tick(self, symbol: str) -> Decimal:
        """Get price tick for symbol with caching and fallback."""
        if hasattr(self, "_price_ticks_cache") and symbol in self._price_ticks_cache:
            return self._price_ticks_cache[symbol]

        tick = get_price_tick_for_symbol(self.exchange, symbol)
        if hasattr(self, "_price_ticks_cache"):
            self._price_ticks_cache[symbol] = tick
        return tick

    def _safe_fetch_order_book(self, symbol: str, limit: int = 20) -> dict:
        """
        Internal helper: Fetches raw L2 order book and handles rate limits.
        """
        try:
            raw_ob = self.exchange.fetch_order_book(symbol, limit)

            if self.exchange.last_response_headers:
                used_weight = self.exchange.last_response_headers.get(
                    "x-mbx-used-weight-1m"
                )
                if used_weight and int(used_weight) > 5000:
                    logger.warning(
                        f"Attention! The weight has reached {used_weight}/6000. 10-second pause!"
                    )
                    time.sleep(10)

            if not raw_ob:
                raise OrderBookFetchError(f"Empty order book response for {symbol}")

            return raw_ob
        except ccxt.RateLimitExceeded as e:
            logger.error(f"Rate Limit Exceeded: {e}")
            raise OrderBookFetchError(f"Rate limit exceeded for {symbol}") from e
        except ccxt.NetworkError as e:
            logger.error(f"Network issue (NetworkError): {e}")
            raise OrderBookFetchError(f"Network error for {symbol}") from e
        except Exception as e:
            logger.error(
                f"Unknown error occurred while fetching order book for {symbol}: {e}"
            )
            raise OrderBookFetchError(f"Order book fetch failed for {symbol}") from e

    def _fetch_order_book_with_retry(
        self, symbol: str, limit: int = 20, retries: int = 2
    ) -> dict:
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                return self._safe_fetch_order_book(symbol, limit)
            except OrderBookFetchError as exc:
                last_exc = exc
                backoff = 1 * (2**attempt)
                time.sleep(backoff)

        if last_exc:
            raise last_exc
        raise OrderBookFetchError(f"Order book fetch failed for {symbol}")

    async def fetch_orderbook_with_retry(
        self, symbol: str, limit: int = 20, retries: int = 2
    ) -> dict:
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                return await asyncio.to_thread(
                    self._safe_fetch_order_book, symbol, limit
                )
            except OrderBookFetchError as exc:
                last_exc = exc
                backoff = 1 * (2**attempt)
                await asyncio.sleep(backoff)

        if last_exc:
            raise last_exc
        raise OrderBookFetchError(f"Order book fetch failed for {symbol}")

    def fetch_order_book(self, symbol: str, limit: int = 20) -> dict:
        """
        Public API: Fetches L2 order book and normalizes to Decimal format.
        """
        raw_ob = self._fetch_order_book_with_retry(symbol, limit)
        if not raw_ob:
            raise OrderBookFetchError(f"Could not fetch orderbook for {symbol}")

        try:
            if not raw_ob.get("bids") or not raw_ob.get("asks"):
                raise InsufficientLiquidityError("Empty orderbook from CEX")

            # ccxt bids/asks are returned as lists of [price, qty]
            best_bid_price = Decimal(str(raw_ob["bids"][0][0]))
            best_bid_qty = Decimal(str(raw_ob["bids"][0][1]))

            best_ask_price = Decimal(str(raw_ob["asks"][0][0]))
            best_ask_qty = Decimal(str(raw_ob["asks"][0][1]))

            mid_price = (best_bid_price + best_ask_price) / Decimal("2")
            spread_bps = ((best_ask_price - best_bid_price) / mid_price) * Decimal(
                "10000"
            )

            bids = [[Decimal(str(p)), Decimal(str(q))] for p, q in raw_ob["bids"]]
            asks = [[Decimal(str(p)), Decimal(str(q))] for p, q in raw_ob["asks"]]

            return {
                "symbol": symbol,
                "timestamp": raw_ob.get("timestamp", 0),
                "bids": bids,
                "asks": asks,
                "best_bid": (best_bid_price, best_bid_qty),
                "best_ask": (best_ask_price, best_ask_qty),
                "mid_price": mid_price,
                "spread_bps": spread_bps,
            }
        except ccxt.ExchangeError as e:
            logger.error(f"Exchange error processing order book for {symbol}: {e}")
            raise

    def fetch_balance(self) -> dict[str, dict]:
        """
        Fetch account balances.
        Must filter out zero-balance assets.
        """
        try:
            raw_balances = self.exchange.fetch_balance()
            formatted_balances: Dict[str, Dict[str, Decimal]] = {}

            for key, data in raw_balances.items():
                # Filter out 'info', 'free', 'used', 'total' strings injected by ccxt
                if isinstance(data, dict) and key != "info" and "total" in data:
                    total = Decimal(str(data.get("total", 0) or 0))
                    if total > Decimal("0"):
                        free = Decimal(str(data.get("free", 0) or 0))
                        locked = Decimal(
                            str(data.get("used", 0) or data.get("locked", 0) or 0)
                        )
                        formatted_balances[key] = {
                            "free": free,
                            "locked": locked,
                            "total": total,
                        }
            return formatted_balances
        except ccxt.NetworkError as e:
            logger.error(f"Network error fetching balance: {e}")
            raise
        except ccxt.ExchangeError as e:
            logger.error(f"Exchange error fetching balance: {e}")
            raise

    def create_limit_ioc_order(
        self,
        symbol: str,
        side: str,
        amount: Decimal,
        price: Decimal,
    ) -> dict:
        """
        Place a LIMIT IOC (Immediate Or Cancel) order with adaptive price rounding.
        Expects Decimal for amount and price to avoid float precision loss.
        """
        safe_amount = round_quantity(amount, LOT_SIZE_STEP)
        # Use symbol-specific tick, with adaptive rounding for low prices
        symbol_tick = self.get_price_tick(symbol)
        safe_price = round_price_adaptive(price, symbol_tick)
        notional = safe_amount * safe_price
        if safe_amount <= Decimal("0"):
            raise ValueError("Order amount rounds to zero after LOT_SIZE_STEP")
        if notional < MIN_NOTIONAL:
            raise ValueError(
                f"Order value {notional} violates MIN_NOTIONAL of {MIN_NOTIONAL}"
            )
        try:
            response = self.exchange.create_order(
                symbol=symbol,
                type="limit",
                side=side,
                amount=str(safe_amount),
                price=str(safe_price),
                params={"timeInForce": "IOC"},
            )
            return self._normalize_order_response(
                response, symbol, side, "limit", "IOC", safe_amount
            )
        except ccxt.NetworkError as e:
            logger.error(f"Network error creating IOC order for {symbol}: {e}")
            raise
        except ccxt.ExchangeError as e:
            logger.error(f"Exchange error creating IOC order for {symbol}: {e}")
            raise

    def create_market_order(
        self,
        symbol: str,
        side: str,
        amount: Decimal,
    ) -> dict:
        """
        Place a market order.
        Expects Decimal for amount to avoid float precision loss.
        Use sparingly — LIMIT IOC is preferred for arb.
        """
        try:
            response = self.exchange.create_order(
                symbol=symbol, type="market", side=side, amount=str(amount)
            )
            return self._normalize_order_response(
                response, symbol, side, "market", "GTC", amount
            )
        except ccxt.NetworkError as e:
            logger.error(f"Network error creating market order for {symbol}: {e}")
            raise
        except ccxt.ExchangeError as e:
            logger.error(f"Exchange error creating market order for {symbol}: {e}")
            raise

    def cancel_order(self, order_id: str, symbol: str) -> dict:
        """Cancel an open order."""
        try:
            return self.exchange.cancel_order(order_id, symbol)
        except ccxt.NetworkError as e:
            logger.error(f"Network error canceling order {order_id}: {e}")
            raise
        except ccxt.ExchangeError as e:
            logger.error(f"Exchange error canceling order {order_id}: {e}")
            raise

    def fetch_order_status(self, order_id: str, symbol: str) -> dict:
        """Check current status of an order."""
        try:
            order = self.exchange.fetch_order(order_id, symbol)
            normalized = self._normalize_order_response(
                order,
                symbol,
                order.get("side", "unknown"),
                order.get("type", "unknown"),
                order.get("timeInForce", "GTC"),
                order.get("amount", 0),
            )
            return {"status": normalized["status"]}
        except ccxt.NetworkError as e:
            logger.error(f"Network error fetching status for order {order_id}: {e}")
            raise
        except ccxt.ExchangeError as e:
            logger.error(f"Exchange error fetching status for order {order_id}: {e}")
            raise

    def get_trading_fees(self, symbol: str) -> dict:
        """
        Returns fee structure:
        {'maker': Decimal('0.001'), 'taker': Decimal('0.001')}
        """
        try:
            fee_data = self.exchange.fetch_trading_fee(symbol)
            return {
                "maker": Decimal(str(fee_data.get("maker", 0))),
                "taker": Decimal(str(fee_data.get("taker", 0))),
            }
        except ccxt.NetworkError as e:
            logger.error(f"Network error fetching fees for {symbol}: {e}")
            raise
        except ccxt.ExchangeError as e:
            logger.error(f"Exchange error fetching fees for {symbol}: {e}")
            raise

    def _normalize_order_response(
        self,
        response: dict,
        symbol: str,
        side: str,
        order_type: str,
        time_in_force: str,
        amount_requested: Decimal,
    ) -> dict:
        """
        Normalize raw ccxt order response.
        """
        status_map = {
            "closed": "filled",
            "open": "partially_filled",  # Evaluated explicitly for IOC
            "canceled": "expired",
            "expired": "expired",
            "rejected": "expired",
        }

        raw_status = response.get("status", "unknown")
        status = status_map.get(raw_status, raw_status)

        amount_filled = Decimal(str(response.get("filled", 0) or 0))
        avg_fill_price = Decimal(str(response.get("average", 0) or 0))

        fee_cost = 0
        fee_asset = ""
        fee_info = response.get("fee", {})
        if fee_info:
            fee_cost = fee_info.get("cost", 0)
            fee_asset = fee_info.get("currency", "")

        fee_cost_dec = Decimal(str(fee_cost or 0))
        amount_requested_dec = Decimal(
            str(amount_requested or response.get("amount", 0) or 0)
        )

        # Edge case: If 0 fill on IOC -> expired
        if time_in_force == "IOC" and amount_filled == Decimal("0"):
            status = "expired"

        return {
            "id": str(response.get("id", "")),
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "time_in_force": time_in_force,
            "amount_requested": amount_requested_dec,
            "amount_filled": amount_filled,
            "avg_fill_price": avg_fill_price,
            "fee": fee_cost_dec,
            "fee_asset": fee_asset,
            "status": status,
            "timestamp": response.get("timestamp", 0),
        }
