import logging
import pytest
from decimal import Decimal

from src.config.logger import setup_logger
from src.executor.recovery import CircuitBreaker
from src.safety.alerts import TelegramAlert
from src.safety.killswitch import (
    ABSOLUTE_MAX_TRADE_USD,
    activate_kill_switch,
    clear_kill_switch,
    is_kill_switch_active,
    safety_check,
)
from src.safety.limits import RiskLimits, RiskManager


@pytest.fixture
def logger() -> logging.Logger:
    setup_logger(filename_prefix="test_risk_controls", level="INFO")
    clear_kill_switch()
    return logging.getLogger("TestRiskControls")


def test_circuit_breaker(logger: logging.Logger) -> None:
    breaker = CircuitBreaker()
    for _ in range(3):
        breaker.record_failure()
    logger.info("Circuit breaker open: %s", breaker.is_open())


def test_daily_loss(logger: logging.Logger) -> None:
    limits = RiskLimits(max_daily_loss_usd=Decimal("5"))
    manager = RiskManager(limits)
    manager.record_trade_result(
        net_pnl_usd=Decimal("-6"),
        success=True,
        total_capital_usd=Decimal("100"),
    )
    result = manager.pre_trade_check(
        trade_notional_usd=Decimal("1"),
        total_capital_usd=Decimal("100"),
        current_position_usd=Decimal("0"),
        open_positions=0,
        is_new_position=True,
    )
    logger.info("Daily loss check allowed: %s", result.allowed)

    if not result.allowed:
        activate_kill_switch("Daily loss limit breached")


def test_trade_limits(logger: logging.Logger) -> None:
    limits = RiskLimits(max_trade_usd=Decimal("5"))
    manager = RiskManager(limits)
    result = manager.pre_trade_check(
        trade_notional_usd=Decimal("10"),
        total_capital_usd=Decimal("100"),
        current_position_usd=Decimal("0"),
        open_positions=0,
        is_new_position=True,
    )
    logger.info("Max trade limit allowed: %s", result.allowed)

    absolute = safety_check(
        ABSOLUTE_MAX_TRADE_USD + Decimal("1"),
        Decimal("0"),
        Decimal("100"),
        0,
    )
    logger.info("Absolute max trade allowed: %s", absolute.allowed)


def main() -> None:
    setup_logger(filename_prefix="test_risk_controls", level="INFO")
    logger = logging.getLogger("TestRiskControls")

    alerts = TelegramAlert.from_env()
    if alerts.enabled:
        alerts.send("Risk control stress test started")

    clear_kill_switch()
    test_circuit_breaker(logger)
    test_daily_loss(logger)
    test_trade_limits(logger)

    logger.info("Kill switch active: %s", is_kill_switch_active())
    if alerts.enabled:
        alerts.send("Risk control stress test completed")


if __name__ == "__main__":
    main()
