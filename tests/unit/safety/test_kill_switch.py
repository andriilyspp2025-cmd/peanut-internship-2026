from decimal import Decimal

from src.safety.killswitch import (
    ABSOLUTE_MAX_TRADE_USD,
    activate_kill_switch,
    clear_kill_switch,
    is_kill_switch_active,
    safety_check,
)


def test_kill_switch_activate_and_clear(tmp_path, monkeypatch):
    kill_path = tmp_path / "arb_bot_kill"
    monkeypatch.setenv("KILL_SWITCH_FILE", str(kill_path))

    clear_kill_switch()
    assert not is_kill_switch_active()

    activate_kill_switch("test")
    assert is_kill_switch_active()

    clear_kill_switch()
    assert not is_kill_switch_active()


def test_safety_check_absolute_trade_limit():
    result = safety_check(
        ABSOLUTE_MAX_TRADE_USD + Decimal("1"),
        Decimal("0"),
        Decimal("100"),
        0,
    )
    assert not result.allowed
