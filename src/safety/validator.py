from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import time

from src.strategy.signal import Signal


def _to_decimal(value: object, default: str) -> Decimal:
    if value is None:
        return Decimal(default)
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


@dataclass(frozen=True)
class ValidatorConfig:
    max_signal_age_seconds: Decimal = Decimal("5")
    min_spread_bps: Decimal = Decimal("0")
    max_spread_bps: Decimal = Decimal("1000")

    @classmethod
    def from_config(cls, config: dict | None) -> "ValidatorConfig":
        config = config or {}
        return cls(
            max_signal_age_seconds=_to_decimal(
                config.get("max_signal_age_seconds"), "5"
            ),
            min_spread_bps=_to_decimal(config.get("min_spread_bps"), "0"),
            max_spread_bps=_to_decimal(config.get("max_spread_bps"), "1000"),
        )


class PreTradeValidator:
    def __init__(self, config: ValidatorConfig):
        self.config = config

    def validate(self, signal: Signal | None) -> tuple[bool, str]:
        if signal is None:
            return False, "Signal missing"
        if time.time() > signal.expiry:
            return False, "Signal expired"
        if signal.age_seconds() > self.config.max_signal_age_seconds:
            return False, "Signal stale"
        if signal.cex_price <= Decimal("0") or signal.dex_price <= Decimal("0"):
            return False, "Non-positive prices"
        if signal.spread_bps < self.config.min_spread_bps:
            return False, "Spread below minimum"
        if signal.spread_bps > self.config.max_spread_bps:
            return False, "Spread above maximum"
        return True, "OK"
