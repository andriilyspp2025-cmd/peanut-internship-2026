from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime
from pathlib import Path
import os
import tempfile

ABSOLUTE_MAX_TRADE_USD = Decimal("25")
ABSOLUTE_MAX_DAILY_LOSS = Decimal("20")
ABSOLUTE_MIN_CAPITAL = Decimal("50")
ABSOLUTE_MAX_TRADES_PER_HOUR = 30

DEFAULT_KILL_SWITCH_FILE = "/tmp/arb_bot_kill"


def _kill_switch_path() -> Path:
    override = os.getenv("KILL_SWITCH_FILE")
    if override:
        return Path(override)
    if os.name == "nt":
        return Path(tempfile.gettempdir()) / "arb_bot_kill"
    return Path(DEFAULT_KILL_SWITCH_FILE)


def is_kill_switch_active() -> bool:
    return _kill_switch_path().exists()


def activate_kill_switch(reason: str | None = None) -> None:
    path = _kill_switch_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().isoformat()
    message = f"{timestamp} | {reason or 'manual'}\n"
    path.write_text(message, encoding="utf-8")


def clear_kill_switch() -> None:
    path = _kill_switch_path()
    if path.exists():
        path.unlink()


@dataclass(frozen=True)
class SafetyCheckResult:
    allowed: bool
    reason: str


def safety_check(
    trade_usd: Decimal,
    daily_loss: Decimal,
    total_capital: Decimal,
    trades_last_hour: int,
) -> SafetyCheckResult:
    if trade_usd > ABSOLUTE_MAX_TRADE_USD:
        return SafetyCheckResult(
            False,
            f"ABSOLUTE_MAX_TRADE_USD exceeded: {trade_usd} > {ABSOLUTE_MAX_TRADE_USD}",
        )
    if daily_loss > ABSOLUTE_MAX_DAILY_LOSS:
        return SafetyCheckResult(
            False,
            f"ABSOLUTE_MAX_DAILY_LOSS exceeded: {daily_loss} > {ABSOLUTE_MAX_DAILY_LOSS}",
        )
    if total_capital < ABSOLUTE_MIN_CAPITAL:
        return SafetyCheckResult(
            False,
            f"ABSOLUTE_MIN_CAPITAL breached: {total_capital} < {ABSOLUTE_MIN_CAPITAL}",
        )
    if trades_last_hour >= ABSOLUTE_MAX_TRADES_PER_HOUR:
        return SafetyCheckResult(
            False,
            "ABSOLUTE_MAX_TRADES_PER_HOUR exceeded",
        )
    return SafetyCheckResult(True, "OK")
