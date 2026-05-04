from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime
import time
from typing import Optional

from .killswitch import safety_check


def _to_decimal(value: object, default: str) -> Decimal:
    if value is None:
        return Decimal(default)
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _normalize_drawdown(value: Decimal) -> Decimal:
    if value > Decimal("1"):
        return value / Decimal("100")
    return value


@dataclass(frozen=True)
class RiskLimits:
    max_trade_usd: Decimal = Decimal("10")
    max_daily_loss_usd: Decimal = Decimal("15")
    max_drawdown_pct: Decimal = Decimal("0.15")
    max_trades_per_hour: int = 20
    max_consecutive_losses: int = 3

    @classmethod
    def from_config(cls, config: dict | None) -> "RiskLimits":
        config = config or {}
        drawdown = _normalize_drawdown(
            _to_decimal(config.get("max_drawdown_pct"), "0.15")
        )
        return cls(
            max_trade_usd=_to_decimal(config.get("max_trade_usd"), "10"),
            max_daily_loss_usd=_to_decimal(config.get("max_daily_loss_usd"), "15"),
            max_drawdown_pct=drawdown,
            max_trades_per_hour=int(config.get("max_trades_per_hour", 20)),
            max_consecutive_losses=int(config.get("max_consecutive_losses", 3)),
        )


@dataclass(frozen=True)
class RiskCheckResult:
    allowed: bool
    reason: str
    hard_stop: bool = False


class RiskManager:
    def __init__(self, limits: RiskLimits):
        self.limits = limits
        self._trade_timestamps: list[float] = []
        self._daily_loss_usd = Decimal("0")
        self._consecutive_losses = 0
        self._day = datetime.utcnow().date()
        self._peak_capital_usd: Optional[Decimal] = None

    @property
    def daily_loss_usd(self) -> Decimal:
        return self._daily_loss_usd

    @property
    def consecutive_losses(self) -> int:
        return self._consecutive_losses

    def _now(self, now: float | None) -> float:
        return now if now is not None else time.time()

    def _rollover_day(self, now_ts: float) -> None:
        day = datetime.utcfromtimestamp(now_ts).date()
        if day != self._day:
            self._day = day
            self._daily_loss_usd = Decimal("0")
            self._consecutive_losses = 0
            self._trade_timestamps = []

    def _cleanup_trades(self, now_ts: float) -> None:
        cutoff = now_ts - 3600.0
        self._trade_timestamps = [t for t in self._trade_timestamps if t >= cutoff]

    def trades_last_hour(self, now: float | None = None) -> int:
        now_ts = self._now(now)
        self._cleanup_trades(now_ts)
        return len(self._trade_timestamps)

    def record_trade_attempt(self, now: float | None = None) -> None:
        now_ts = self._now(now)
        self._rollover_day(now_ts)
        self._trade_timestamps.append(now_ts)
        self._cleanup_trades(now_ts)

    def record_trade_result(
        self,
        net_pnl_usd: Decimal | None,
        success: bool,
        total_capital_usd: Decimal | None = None,
        now: float | None = None,
    ) -> None:
        now_ts = self._now(now)
        self._rollover_day(now_ts)

        if total_capital_usd is not None:
            if (
                self._peak_capital_usd is None
                or total_capital_usd > self._peak_capital_usd
            ):
                self._peak_capital_usd = total_capital_usd

        if not success:
            self._consecutive_losses += 1
            return

        if net_pnl_usd is None:
            return

        if net_pnl_usd < Decimal("0"):
            self._daily_loss_usd += abs(net_pnl_usd)
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

    def pre_trade_check(
        self,
        trade_notional_usd: Decimal,
        total_capital_usd: Decimal | None,
        now: float | None = None,
    ) -> RiskCheckResult:
        now_ts = self._now(now)
        self._rollover_day(now_ts)
        self._cleanup_trades(now_ts)

        if total_capital_usd is None:
            return RiskCheckResult(False, "Total capital unknown")
        if trade_notional_usd <= Decimal("0"):
            return RiskCheckResult(False, "Trade notional is non-positive")

        safety = safety_check(
            trade_notional_usd,
            self._daily_loss_usd,
            total_capital_usd,
            len(self._trade_timestamps),
        )
        if not safety.allowed:
            return RiskCheckResult(False, safety.reason, True)

        if trade_notional_usd > self.limits.max_trade_usd:
            return RiskCheckResult(
                False,
                f"Trade notional exceeds max_trade_usd: {trade_notional_usd}",
            )
        if self._daily_loss_usd > self.limits.max_daily_loss_usd:
            return RiskCheckResult(
                False,
                f"Daily loss exceeds max_daily_loss_usd: {self._daily_loss_usd}",
            )
        if len(self._trade_timestamps) >= self.limits.max_trades_per_hour:
            return RiskCheckResult(False, "Trade frequency limit reached")
        if self._consecutive_losses >= self.limits.max_consecutive_losses:
            return RiskCheckResult(False, "Consecutive loss limit reached")

        if self._peak_capital_usd is None or total_capital_usd > self._peak_capital_usd:
            self._peak_capital_usd = total_capital_usd

        if self._peak_capital_usd and self._peak_capital_usd > Decimal("0"):
            drawdown = (
                self._peak_capital_usd - total_capital_usd
            ) / self._peak_capital_usd
            if drawdown > self.limits.max_drawdown_pct:
                return RiskCheckResult(
                    False,
                    f"Drawdown exceeds max_drawdown_pct: {drawdown}",
                )

        return RiskCheckResult(True, "OK")
