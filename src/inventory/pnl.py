import csv
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import matplotlib.pyplot as plt

from src.inventory.tracker import Venue


@dataclass
class TradeLeg:
    """Single execution leg."""

    id: str
    timestamp: datetime
    venue: Venue
    symbol: str  # "ETH/USDT"
    side: str  # "buy" or "sell"
    amount: Decimal  # Base asset qty
    price: Decimal  # Execution price
    fee: Decimal
    fee_asset: str
    fee_usd: Decimal = Decimal("0")  # Fee converted to USD


@dataclass
class ArbRecord:
    """Complete arb trade with both legs."""

    id: str
    timestamp: datetime
    buy_leg: TradeLeg
    sell_leg: TradeLeg
    gas_cost_usd: Decimal = Decimal("0")

    @property
    def gross_pnl(self) -> Decimal:
        """Price difference revenue."""
        revenue = self.sell_leg.amount * self.sell_leg.price
        cost = self.buy_leg.amount * self.buy_leg.price
        return revenue - cost

    @property
    def total_fees(self) -> Decimal:
        """All fees: both legs + gas."""
        return self.buy_leg.fee_usd + self.sell_leg.fee_usd + self.gas_cost_usd

    @property
    def net_pnl(self) -> Decimal:
        """Gross - fees."""
        return self.gross_pnl - self.total_fees

    @property
    def net_pnl_bps(self) -> Decimal:
        """Net PnL in basis points of notional."""
        notional = self.notional
        if notional == Decimal("0"):
            return Decimal("0")
        return (self.net_pnl / notional) * Decimal("10000")

    @property
    def notional(self) -> Decimal:
        """Trade size in quote currency."""
        return self.buy_leg.amount * self.buy_leg.price


class PnLEngine:
    """
    Tracks all arb trades and produces PnL reports.
    """

    def __init__(self):
        self.trades: list[ArbRecord] = []

    def record(self, trade: ArbRecord):
        """Record a completed arb trade."""
        self.trades.append(trade)

    def summary(self) -> dict:
        """
        Aggregate PnL summary.
        """
        if not self.trades:
            return {
                "total_trades": 0,
                "total_pnl_usd": Decimal("0"),
                "total_fees_usd": Decimal("0"),
                "avg_pnl_per_trade": Decimal("0"),
                "avg_pnl_bps": Decimal("0"),
                "win_rate": 0.0,
                "best_trade_pnl": Decimal("0"),
                "worst_trade_pnl": Decimal("0"),
                "total_notional": Decimal("0"),
                "sharpe_estimate": 0.0,
                "pnl_by_hour": {},
            }

        total_pnl = sum((trade.net_pnl for trade in self.trades), Decimal("0"))
        total_fees = sum((trade.total_fees for trade in self.trades), Decimal("0"))
        total_notional = sum((trade.notional for trade in self.trades), Decimal("0"))

        avg_pnl_per_trade = total_pnl / Decimal(len(self.trades))
        avg_pnl_bps = (
            (total_pnl / total_notional * Decimal("10000"))
            if total_notional > Decimal("0")
            else Decimal("0")
        )

        profitable_trades = sum(
            1 for trade in self.trades if trade.net_pnl > Decimal("0")
        )
        win_rate = float(profitable_trades) / len(self.trades)

        best_trade_pnl = max(trade.net_pnl for trade in self.trades)
        worst_trade_pnl = min(trade.net_pnl for trade in self.trades)

        pnl_by_hour = {}
        for trade in self.trades:
            hour = trade.timestamp.replace(minute=0, second=0, microsecond=0)
            pnl_by_hour[hour] = pnl_by_hour.get(hour, Decimal("0")) + trade.net_pnl

        pnl_values = [float(trade.net_pnl) for trade in self.trades]
        avg_pnl_float = float(avg_pnl_per_trade)

        if len(pnl_values) > 1:
            variance = sum((p - avg_pnl_float) ** 2 for p in pnl_values) / len(
                pnl_values
            )
            pnl_stddev = variance**0.5
            sharpe_estimate = (avg_pnl_float / pnl_stddev) if pnl_stddev > 0 else 0.0
        else:
            sharpe_estimate = 0.0

        return {
            "total_trades": len(self.trades),
            "total_pnl_usd": total_pnl,
            "total_fees_usd": total_fees,
            "avg_pnl_per_trade": avg_pnl_per_trade,
            "avg_pnl_bps": avg_pnl_bps,
            "win_rate": win_rate,
            "best_trade_pnl": best_trade_pnl,
            "worst_trade_pnl": worst_trade_pnl,
            "total_notional": total_notional,
            "sharpe_estimate": sharpe_estimate,
            "pnl_by_hour": pnl_by_hour,
        }

    def recent(self, n: int = 10) -> list[dict]:
        """
        Last N trades as summary dicts.
        For display in CLI dashboard.
        """
        return [self._trade_summary(trade) for trade in self.trades[-n:]]

    def _trade_summary(self, trade: ArbRecord) -> dict:
        """Helper method to format trade data for CSV and CLI."""
        return {
            "id": trade.id,
            "timestamp": trade.timestamp.isoformat(),
            "buy_venue": trade.buy_leg.venue.value,
            "buy_symbol": trade.buy_leg.symbol,
            "buy_side": trade.buy_leg.side,
            "buy_amount": str(trade.buy_leg.amount),
            "buy_price": str(trade.buy_leg.price),
            "buy_fee": str(trade.buy_leg.fee),
            "buy_fee_asset": trade.buy_leg.fee_asset,
            "sell_venue": trade.sell_leg.venue.value,
            "sell_symbol": trade.sell_leg.symbol,
            "sell_side": trade.sell_leg.side,
            "sell_amount": str(trade.sell_leg.amount),
            "sell_price": str(trade.sell_leg.price),
            "sell_fee": str(trade.sell_leg.fee),
            "sell_fee_asset": trade.sell_leg.fee_asset,
            "sell_fee_usd": str(trade.sell_leg.fee_usd),
            "gas_cost_usd": str(trade.gas_cost_usd),
            "gross_pnl_usd": str(trade.gross_pnl),
            "total_fees_usd": str(trade.total_fees),
            "net_pnl_usd": str(trade.net_pnl),
            "net_pnl_bps": str(trade.net_pnl_bps),
            "buy_fee_usd": str(trade.buy_leg.fee_usd),
        }

    def export_csv(self, filepath: str):
        """Export all trades to CSV for analysis."""
        with open(filepath, "w", newline="") as csvfile:
            fieldnames = [
                "id",
                "timestamp",
                "buy_venue",
                "buy_symbol",
                "buy_side",
                "buy_amount",
                "buy_price",
                "buy_fee",
                "buy_fee_asset",
                "buy_fee_usd",
                "sell_venue",
                "sell_symbol",
                "sell_side",
                "sell_amount",
                "sell_price",
                "sell_fee",
                "sell_fee_asset",
                "sell_fee_usd",
                "gas_cost_usd",
                "gross_pnl_usd",
                "total_fees_usd",
                "net_pnl_usd",
                "net_pnl_bps",
            ]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for trade in self.trades:
                writer.writerow(self._trade_summary(trade))

    def export_chart(self, filepath: str = "pnl_chart.png"):
        """Stretch Goal: Export cumulative PnL chart."""
        if not self.trades:
            return

        times = [t.timestamp for t in self.trades]

        # Рахуємо накопичувальний (cumulative) PnL
        cumulative_pnl = []
        current = Decimal("0")
        for t in self.trades:
            current += t.net_pnl
            cumulative_pnl.append(float(current))

        plt.figure(figsize=(10, 5))
        plt.plot(
            times, cumulative_pnl, marker="o", linestyle="-", color="green", linewidth=2
        )
        plt.title("Cumulative Net PnL Over Time")
        plt.xlabel("Time")
        plt.ylabel("Net PnL (USD)")
        plt.grid(True, linestyle="--", alpha=0.7)
        plt.xticks(rotation=45)
        plt.tight_layout()
        plt.savefig(filepath)
        plt.close()


JOURNAL_FIELDS = [
    "timestamp",
    "pair",
    "direction",
    "trade_size_usd",
    "cex_price",
    "dex_price",
    "spread_bps",
    "gas_cost_usd",
    "net_pnl_usd",
    "status",
    "arbiscan_url",
    "cex_order_id",
]


def save_to_journal(
    ctx,
    gas_cost_usd: Decimal,
    filepath: str = "logs/trades_journal.csv",
) -> None:
    """Append a structured execution record to the trading journal CSV."""
    timestamp = datetime.fromtimestamp(ctx.finished_at or ctx.started_at).isoformat()
    trade_size_usd = ctx.signal.size * ctx.signal.cex_price
    net_pnl = ctx.actual_net_pnl if ctx.actual_net_pnl is not None else Decimal("0")
    arbiscan_url = (
        f"https://arbiscan.io/tx/{ctx.dex_tx_hash}" if ctx.dex_tx_hash else ""
    )
    cex_order_id = ctx.cex_order_id or ctx.leg1_order_id or ""

    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = path.exists()

    row = {
        "timestamp": timestamp,
        "pair": ctx.signal.pair,
        "direction": ctx.signal.direction.name,
        "trade_size_usd": str(trade_size_usd),
        "cex_price": str(ctx.signal.cex_price),
        "dex_price": str(ctx.signal.dex_price),
        "spread_bps": str(ctx.signal.spread_bps),
        "gas_cost_usd": str(gas_cost_usd),
        "net_pnl_usd": str(net_pnl),
        "status": ctx.state.name,
        "arbiscan_url": arbiscan_url,
        "cex_order_id": cex_order_id,
    }

    with path.open("a", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=JOURNAL_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)
