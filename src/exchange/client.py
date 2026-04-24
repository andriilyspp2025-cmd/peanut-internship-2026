import logging
import time
import ccxt
from decimal import Decimal
from typing import Dict

logger = logging.getLogger(__name__)


class InsufficientLiquidityError(Exception):
    """Raised when orderbook is empty or lacks liquidity."""

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

        for key in ["pairs", "dex_pools", "trade_size", "simulation", "signal_config"]:
            config.pop(key, None)

        self.exchange = ccxt.binance(config)
        try:
            self.exchange.load_markets()
            logger.info("Successfully connected to Binance Testnet")
        except (ccxt.AuthenticationError, ccxt.NetworkError) as e:
            logger.critical(f"Failed to connect to Binance Testnet: {e}")
            raise

    def _safe_fetch_order_book(self, symbol: str, limit: int = 20) -> dict | None:
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

            return raw_ob
        except ccxt.RateLimitExceeded as e:
            logger.error(f" Rate Limit Exceeded: {e}")
            return None
        except ccxt.NetworkError as e:
            logger.error(f"Network issue  (NetworkError): {e}")
            return None
        except Exception as e:
            logger.error(
                f" Unknown error occurred while fetching order book for {symbol}: {e}"
            )
            return None

    def fetch_order_book(self, symbol: str, limit: int = 20) -> dict:
        """
        Public API: Fetches L2 order book and normalizes to Decimal format.
        """
        raw_ob = self._safe_fetch_order_book(symbol, limit)
        if not raw_ob:
            raise InsufficientLiquidityError(f"Could not fetch orderbook for {symbol}")

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
        Place a LIMIT IOC (Immediate Or Cancel) order.
        Expects Decimal for amount and price to avoid float precision loss.
        """
        try:
            response = self.exchange.create_order(
                symbol=symbol,
                type="limit",
                side=side,
                amount=str(amount),
                price=str(price),
                params={"timeInForce": "IOC"},
            )
            return self._normalize_order_response(
                response, symbol, side, "limit", "IOC", amount
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
