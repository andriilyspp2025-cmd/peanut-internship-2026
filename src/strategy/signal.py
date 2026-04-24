from decimal import Decimal
from dataclasses import dataclass
from enum import Enum
import time
import uuid


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
        return (
            time.time() < self.expiry
            and self.inventory_ok
            and self.within_limits
            and self.expected_net_pnl > 0
            and self.score > 0
        )

    def age_seconds(self) -> Decimal:
        return Decimal(str(time.time())) - Decimal(str(self.timestamp))
