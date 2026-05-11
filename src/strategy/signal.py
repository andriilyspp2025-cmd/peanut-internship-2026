from decimal import Decimal
from dataclasses import dataclass
from enum import Enum
import time
import uuid

from src.config.config import config


class Direction(Enum):
    BUY_CEX_SELL_DEX = "buy_cex_sell_dex"
    BUY_DEX_SELL_CEX = "buy_dex_sell_cex"


@dataclass
class Signal:
    """A validated arbitrage opportunity ready for execution."""

    signal_id: str
    pair: str
    direction: Direction

    cex_price: Decimal
    dex_price: Decimal
    spread_bps: Decimal
    size: Decimal

    expected_gross_pnl: Decimal
    expected_fees: Decimal
    expected_net_pnl: Decimal

    score: Decimal
    timestamp: float
    expiry: float

    inventory_ok: bool
    within_limits: bool

    def validation_issues(self) -> list[str]:
        """Return a list of reasons why the signal is not valid."""
        issues: list[str] = []

        if time.time() >= self.expiry:
            issues.append("expired signal")
        if not self.inventory_ok:
            issues.append("inventory_ok=False")
        if not self.within_limits:
            issues.append("within_limits=False")
        if self.expected_net_pnl < config.MIN_PROFIT_USD:
            issues.append(
                f"expected_net_pnl={self.expected_net_pnl} < min_profit_usd={config.MIN_PROFIT_USD}"
            )
        if self.score <= 0:
            issues.append(f"score={self.score} <= 0")

        return issues

    @classmethod
    def create(cls, pair: str, direction: Direction, **kwargs) -> "Signal":
        return cls(
            signal_id=f"{pair.replace('/', '')}_{uuid.uuid4().hex[:8]}",
            pair=pair,
            direction=direction,
            timestamp=time.time(),
            **kwargs,
        )

    def is_valid(self) -> bool:
        return not self.validation_issues()

    def age_seconds(self) -> Decimal:
        return Decimal(str(time.time())) - Decimal(str(self.timestamp))
