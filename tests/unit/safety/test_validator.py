import time
from decimal import Decimal

from src.safety.validator import PreTradeValidator, ValidatorConfig
from src.strategy.signal import Direction, Signal


def _make_signal() -> Signal:
    return Signal.create(
        pair="ETH/USDT",
        direction=Direction.BUY_CEX_SELL_DEX,
        cex_price=Decimal("2000"),
        dex_price=Decimal("2010"),
        spread_bps=Decimal("50"),
        size=Decimal("1"),
        expected_gross_pnl=Decimal("10"),
        expected_fees=Decimal("1"),
        expected_net_pnl=Decimal("9"),
        score=Decimal("1"),
        expiry=time.time() + 10,
        inventory_ok=True,
        within_limits=True,
    )


def test_validator_accepts_valid_signal():
    validator = PreTradeValidator(
        ValidatorConfig(
            max_signal_age_seconds=Decimal("5"),
            min_spread_bps=Decimal("10"),
            max_spread_bps=Decimal("500"),
        )
    )
    signal = _make_signal()
    ok, _ = validator.validate(signal)
    assert ok


def test_validator_rejects_stale_signal():
    validator = PreTradeValidator(ValidatorConfig(max_signal_age_seconds=Decimal("1")))
    signal = _make_signal()
    signal.timestamp = time.time() - 10
    ok, reason = validator.validate(signal)
    assert not ok
    assert "stale" in reason.lower()


def test_validator_rejects_expired_signal():
    validator = PreTradeValidator(ValidatorConfig())
    signal = _make_signal()
    signal.expiry = time.time() - 1
    ok, reason = validator.validate(signal)
    assert not ok
    assert "expired" in reason.lower()
