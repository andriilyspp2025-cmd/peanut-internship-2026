from decimal import Decimal

from src.safety.limits import RiskLimits, RiskManager


def test_pre_trade_check_allows_within_limits():
    limits = RiskLimits(
        max_trade_usd=Decimal("10"),
        max_daily_loss_usd=Decimal("10"),
        max_drawdown_pct=Decimal("0.2"),
        max_trades_per_hour=5,
        max_consecutive_losses=3,
    )
    manager = RiskManager(limits)
    result = manager.pre_trade_check(
        trade_notional_usd=Decimal("5"),
        total_capital_usd=Decimal("100"),
        current_position_usd=Decimal("0"),
        open_positions=0,
        is_new_position=True,
        now=1000.0,
    )
    assert result.allowed


def test_trade_frequency_limit_blocks():
    limits = RiskLimits(max_trades_per_hour=2)
    manager = RiskManager(limits)
    manager.record_trade_attempt(now=1000.0)
    manager.record_trade_attempt(now=1001.0)

    result = manager.pre_trade_check(
        trade_notional_usd=Decimal("5"),
        total_capital_usd=Decimal("100"),
        current_position_usd=Decimal("0"),
        open_positions=0,
        is_new_position=True,
        now=1002.0,
    )
    assert not result.allowed


def test_daily_loss_limit_blocks():
    limits = RiskLimits(max_daily_loss_usd=Decimal("5"))
    manager = RiskManager(limits)
    manager.record_trade_result(
        net_pnl_usd=Decimal("-6"),
        success=True,
        total_capital_usd=Decimal("100"),
        now=1000.0,
    )

    result = manager.pre_trade_check(
        trade_notional_usd=Decimal("5"),
        total_capital_usd=Decimal("100"),
        current_position_usd=Decimal("0"),
        open_positions=0,
        is_new_position=True,
        now=1001.0,
    )
    assert not result.allowed


def test_drawdown_blocks_when_exceeded():
    limits = RiskLimits(max_drawdown_pct=Decimal("0.2"))
    manager = RiskManager(limits)

    result_ok = manager.pre_trade_check(
        trade_notional_usd=Decimal("5"),
        total_capital_usd=Decimal("100"),
        current_position_usd=Decimal("0"),
        open_positions=0,
        is_new_position=True,
        now=1000.0,
    )
    assert result_ok.allowed

    result_bad = manager.pre_trade_check(
        trade_notional_usd=Decimal("5"),
        total_capital_usd=Decimal("70"),
        current_position_usd=Decimal("0"),
        open_positions=0,
        is_new_position=True,
        now=1001.0,
    )
    assert not result_bad.allowed


def test_max_position_blocks_when_exceeded():
    limits = RiskLimits(max_position_usd=Decimal("10"))
    manager = RiskManager(limits)

    result = manager.pre_trade_check(
        trade_notional_usd=Decimal("6"),
        total_capital_usd=Decimal("100"),
        current_position_usd=Decimal("5"),
        open_positions=1,
        is_new_position=False,
        now=1000.0,
    )

    assert not result.allowed


def test_max_open_positions_blocks_new_asset():
    limits = RiskLimits(max_open_positions=2)
    manager = RiskManager(limits)

    result = manager.pre_trade_check(
        trade_notional_usd=Decimal("5"),
        total_capital_usd=Decimal("100"),
        current_position_usd=Decimal("0"),
        open_positions=2,
        is_new_position=True,
        now=1000.0,
    )

    assert not result.allowed


def test_max_open_positions_allows_existing_asset():
    limits = RiskLimits(max_open_positions=2)
    manager = RiskManager(limits)

    result = manager.pre_trade_check(
        trade_notional_usd=Decimal("5"),
        total_capital_usd=Decimal("100"),
        current_position_usd=Decimal("2"),
        open_positions=2,
        is_new_position=False,
        now=1000.0,
    )

    assert result.allowed
