from decimal import Decimal


class OrderBookAnalyzer:
    """
    Analyze order book snapshots for trading decisions.
    """

    def __init__(self, orderbook: dict):
        """
        Initialize with order book from ExchangeClient.fetch_order_book().
        """
        self.bids = orderbook["bids"]
        self.asks = orderbook["asks"]
        self.mid_price = orderbook["mid_price"]
        self.best_bid_price = orderbook["best_bid"][0]
        self.best_ask_price = orderbook["best_ask"][0]

    def walk_the_book(
        self,
        side: str,  # "buy" (walk asks) or "sell" (walk bids)
        qty: float,  # Amount of base asset
    ) -> dict:
        """
        Simulate filling `qty` against the order book.

        Returns:
        {
            'avg_price': Decimal,
            'total_cost': Decimal,     # In quote currency
            'slippage_bps': Decimal,   # vs best price
            'levels_consumed': int,    # How deep we went
            'fully_filled': bool,
            'fills': [
                {'price': Decimal, 'qty': Decimal, 'cost': Decimal},
                ...
            ]
        }

        If insufficient liquidity, fully_filled=False and fills show what IS available.
        """
        qty_dec = Decimal(str(qty))

        if side == "buy":
            order_list = self.asks
            best_price = self.best_ask_price
        elif side == "sell":
            order_list = self.bids
            best_price = self.best_bid_price
        else:
            raise ValueError("side must be 'buy' or 'sell'")

        remaining_qty = qty_dec
        total_cost = Decimal("0")
        levels_consumed = 0
        fills = []

        for price, level_qty in order_list:
            if remaining_qty <= Decimal("0"):
                break

            fill_qty = min(level_qty, remaining_qty)
            cost = fill_qty * price

            fills.append({"price": price, "qty": fill_qty, "cost": cost})
            total_cost += cost
            remaining_qty -= fill_qty
            levels_consumed += 1

        filled_qty = qty_dec - remaining_qty

        if filled_qty > Decimal("0"):
            avg_price = total_cost / filled_qty

            if side == "buy":
                slippage_bps = ((avg_price - best_price) / best_price) * Decimal(
                    "10000"
                )
            else:
                slippage_bps = ((best_price - avg_price) / best_price) * Decimal(
                    "10000"
                )
        else:
            avg_price = Decimal("0")
            slippage_bps = Decimal("0")

        return {
            "avg_price": avg_price,
            "total_cost": total_cost,
            "slippage_bps": slippage_bps,
            "levels_consumed": levels_consumed,
            "fully_filled": remaining_qty == Decimal("0"),  # True, якщо все купили
            "fills": fills,
        }

    def depth_at_bps(
        self,
        side: str,  # "bid" or "ask"
        bps: float,  # How deep (e.g., 10 = within 10 bps of best)
    ) -> Decimal:
        """
        Total quantity available within `bps` basis points of best price.
        Measures how much you can trade without moving price beyond threshold.
        """
        bps_factor = Decimal(str(bps)) / Decimal("10000")
        qty_sum = Decimal("0")

        if side == "ask":
            threshold = self.best_ask_price * (Decimal("1") + bps_factor)
            for price, level_qty in self.asks:
                if price <= threshold:
                    qty_sum += level_qty
                else:
                    break

        elif side == "bid":
            threshold = self.best_bid_price * (Decimal("1") - bps_factor)
            for price, level_qty in self.bids:
                if price >= threshold:
                    qty_sum += level_qty
                else:
                    break
        else:
            raise ValueError("side must be 'ask' or 'bid'")

        return qty_sum

    def imbalance(self, levels: int = 10) -> float:
        """
        Order book imbalance ratio.
        Returns [-1.0, +1.0] where:
          +1.0 = all bids (buy pressure)
          -1.0 = all asks (sell pressure)
        """
        bid_qty = sum((qty for _, qty in self.bids[:levels]), Decimal("0"))
        ask_qty = sum((qty for _, qty in self.asks[:levels]), Decimal("0"))

        total_qty = bid_qty + ask_qty
        if total_qty == Decimal("0"):
            return 0.0

        imbalance_ratio = (bid_qty - ask_qty) / total_qty
        return float(imbalance_ratio)

    def effective_spread(self, qty: float) -> Decimal:
        """
        Effective spread for a round-trip of size `qty`.
        = (avg_ask_fill - avg_bid_fill) / mid_price * 10000 (bps)

        This is the TRUE cost of immediacy for your trade size.
        Different from quoted spread which only considers best levels.
        """
        qty_dec = Decimal(str(qty))

        buy_fill = self.walk_the_book("buy", qty_dec)
        sell_fill = self.walk_the_book("sell", qty_dec)

        if not buy_fill["fully_filled"] or not sell_fill["fully_filled"]:
            return Decimal("0")

        avg_ask_fill = buy_fill["avg_price"]
        avg_bid_fill = sell_fill["avg_price"]

        spread_bps = ((avg_ask_fill - avg_bid_fill) / self.mid_price) * Decimal("10000")
        return spread_bps
