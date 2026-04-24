import asyncio
import time
from decimal import Decimal
from unittest.mock import MagicMock
import pytest

from src.strategy.signal import Signal, Direction
from src.executor.engine import ExecutorState
from src.strategy.generator import SignalGenerator
from src.strategy.scorer import SignalScorer
from src.strategy.fees import FeeStructure
from src.executor.engine import Executor, ExecutorConfig
from src.executor.recovery import CircuitBreaker, ReplayProtection


@pytest.fixture(autouse=True)
def patch_time(monkeypatch):
    # Optional: Mock time if you want totally predictable scores,
    # but the tests handle current time well
    pass


def make_signal(
    pair="ETH/USDT",
    direction=Direction.BUY_CEX_SELL_DEX,
    spread_bps=Decimal("80.0"),
    net_pnl=Decimal("10.0"),
    score=Decimal("75.0"),
    inventory_ok=True,
    within_limits=True,
    ttl=Decimal("5.0"),
    size=Decimal("0.1"),
    cex_price=Decimal("2000.0"),
    dex_price=Decimal("2016.0"),
):
    now = time.time()
    return Signal.create(
        pair=pair,
        direction=direction,
        cex_price=cex_price,
        dex_price=dex_price,
        spread_bps=spread_bps,
        size=size,
        expected_gross_pnl=net_pnl + Decimal("1.0"),
        expected_fees=Decimal("1.0"),
        expected_net_pnl=net_pnl,
        score=score,
        expiry=now + float(ttl),
        inventory_ok=inventory_ok,
        within_limits=within_limits,
    )


def make_exchange(bid=2000.0, ask=Decimal("2001.0")):
    ex = MagicMock()
    ex.fetch_order_book.return_value = {
        "bids": [[bid, Decimal("10.0")]],
        "asks": [[ask, Decimal("10.0")]],
    }
    return ex


def make_inventory(enough=True):
    inv = MagicMock()
    inv.get_available.return_value = 1_000_000.0 if enough else Decimal("0.0")
    inv.get_skews.return_value = []
    return inv


# --- Part 1 — Signal Generator ---


def test_generate_signal_profitable():
    ex = make_exchange(
        bid=2000.0, ask=Decimal("2001.0")
    )  # spread_a ≈ Decimal('77.5') bps
    inv = make_inventory(enough=True)
    gen = SignalGenerator(
        exchange_client=ex,
        pricing_module=None,  # використовує DEX-stub
        inventory_tracker=inv,
        fee_structure=FeeStructure(),
        config={
            "min_spread_bps": 50,
            "min_profit_usd": Decimal("5.0"),
            "max_position_usd": 100_000,
            "cooldown_seconds": 0,
        },
    )

    signal = gen.generate("ETH/USDT", size=Decimal("5.0"))

    assert signal is not None
    assert signal.expected_net_pnl > 0
    assert signal.spread_bps > 50
    assert signal.direction == Direction.BUY_CEX_SELL_DEX
    assert signal.inventory_ok is True
    assert signal.within_limits is True
    assert signal.pair == "ETH/USDT"
    assert signal.size == Decimal("5.0")
    assert signal.expiry > time.time()


def test_generate_signal_no_opportunity():
    ex = make_exchange(bid=2000.0, ask=Decimal("2001.0"))
    inv = make_inventory(enough=True)
    gen = SignalGenerator(ex, None, inv, FeeStructure(), {"min_spread_bps": 50})

    def flat_prices(pair, size):
        return {
            "cex_bid": Decimal("2000.0"),
            "cex_ask": Decimal("2001.0"),
            "dex_buy": Decimal("2001.0"),
            "dex_sell": Decimal("2000.0"),
        }

    gen._fetch_prices = flat_prices
    result = gen.generate("ETH/USDT", Decimal("0.1"))
    assert result is None


def test_cooldown_prevents_rapid_signals():
    ex = make_exchange(bid=2000.0, ask=Decimal("2001.0"))
    inv = make_inventory(enough=True)
    gen = SignalGenerator(
        ex,
        None,
        inv,
        FeeStructure(),
        {
            "min_spread_bps": 10,
            "min_profit_usd": Decimal("1.0"),
            "max_position_usd": 100_000,
            "cooldown_seconds": 60,
        },
    )
    gen._fetch_prices = lambda p, s: {
        "cex_bid": Decimal("2000.0"),
        "cex_ask": Decimal("2001.0"),
        "dex_buy": Decimal("2001.0"),
        "dex_sell": Decimal("2050.0"),
    }

    s1 = gen.generate("ETH/USDT", Decimal("5.0"))
    s2 = gen.generate("ETH/USDT", Decimal("5.0"))

    assert s1 is not None, "Перший виклик має повернути Signal"
    assert s2 is None, "Другий виклик у cooldown має повернути None"
    assert "ETH/USDT" in gen.last_signal_time


def test_direction_selection():
    ex = make_exchange(bid=2050.0, ask=Decimal("2051.0"))
    inv = make_inventory(enough=True)
    gen = SignalGenerator(
        ex,
        None,
        inv,
        FeeStructure(),
        {
            "min_spread_bps": 30,
            "min_profit_usd": Decimal("1.0"),
            "max_position_usd": 100_000,
        },
    )
    gen._fetch_prices = lambda p, s: {
        "cex_bid": Decimal("2050.0"),
        "cex_ask": Decimal("2051.0"),
        "dex_buy": Decimal("2000.0"),
        "dex_sell": Decimal("1990.0"),
    }

    signal = gen.generate("ETH/USDT", Decimal("5.0"))

    assert signal is not None
    assert signal.direction == Direction.BUY_DEX_SELL_CEX
    assert signal.spread_bps > 200
    assert signal.cex_price == Decimal("2050.0")
    assert signal.dex_price == Decimal("2000.0")


# --- Part 2 — Signal Scorer ---


def test_score_high_spread():
    scorer = SignalScorer()
    signal = make_signal(spread_bps=Decimal("100.0"))

    score = scorer.score(signal, inventory_state=[])

    assert score == Decimal("78.0")
    assert score > 70


def test_score_inventory_penalty():
    scorer = SignalScorer()
    signal = make_signal(spread_bps=Decimal("80.0"))

    skews_ok = [{"asset": "ETH", "needs_rebalance": False, "max_deviation_pct": 5}]
    skews_bad = [{"asset": "ETH", "needs_rebalance": True, "max_deviation_pct": 35}]

    score_ok = scorer.score(signal, skews_ok)
    score_bad = scorer.score(signal, skews_bad)

    assert score_ok > score_bad
    assert abs((score_ok - score_bad) - Decimal("8.0")) < Decimal("0.001")

    signal_old = make_signal(score=Decimal("80.0"), ttl=Decimal("10.0"))
    signal_old.timestamp = time.time() - 30.0
    signal_old.expiry = signal_old.timestamp + 10.0
    assert scorer.apply_decay(signal_old) == Decimal("0.0")


# --- Part 3 — Executor State Machine ---


@pytest.mark.asyncio
async def test_execute_success():
    signal = make_signal(
        score=Decimal("80.0"),
        net_pnl=Decimal("10.0"),
        inventory_ok=True,
        within_limits=True,
    )
    executor = Executor(
        exchange_client=None,
        pricing_module=None,
        inventory_tracker=None,
        config=ExecutorConfig(simulation_mode=True, use_flashbots=False),
    )

    ctx = await executor.execute(signal)

    assert ctx.state == ExecutorState.DONE
    assert ctx.actual_net_pnl is not None
    assert ctx.leg1_fill_price is not None
    assert ctx.leg1_fill_size is not None
    assert ctx.leg2_fill_price is not None
    assert ctx.leg2_fill_size is not None
    assert ctx.finished_at is not None
    assert ctx.error is None
    assert ctx.leg1_venue == "cex"
    assert ctx.leg2_venue == "dex"


@pytest.mark.asyncio
async def test_execute_cex_timeout():
    signal = make_signal(score=Decimal("80.0"))
    executor = Executor(
        None,
        None,
        None,
        ExecutorConfig(simulation_mode=True, use_flashbots=False, leg1_timeout=0.01),
    )

    async def slow_cex(sig, size=None):
        await asyncio.sleep(10)
        return {"success": True, "price": Decimal("2000.0"), "filled": Decimal("0.1")}

    executor._execute_cex_leg = slow_cex

    ctx = await executor.execute(signal)

    assert ctx.state == ExecutorState.FAILED
    assert ctx.error is not None
    assert "timeout" in ctx.error.lower()
    assert ctx.leg1_fill_price is None
    assert ctx.leg2_fill_price is None
    assert len(executor.circuit_breaker.failures) == 1


@pytest.mark.asyncio
async def test_execute_dex_failure_unwinds():
    signal = make_signal(score=Decimal("80.0"))
    executor = Executor(
        None, None, None, ExecutorConfig(simulation_mode=True, use_flashbots=False)
    )

    unwind_call_count = []

    async def failing_dex(sig, size):
        return {"success": False, "error": "slippage exceeded"}

    async def tracking_unwind(ctx):
        unwind_call_count.append(ctx.state)
        # default unwind simulated properly
        ctx.state = ExecutorState.FAILED
        ctx.error = "Unwound due to leg 2 failure"

    executor._execute_dex_leg = failing_dex
    executor._unwind = tracking_unwind

    ctx = await executor.execute(signal)

    assert ctx.state == ExecutorState.FAILED
    assert len(unwind_call_count) == 1
    assert unwind_call_count[0] == ExecutorState.UNWINDING
    assert ctx.leg1_fill_price is not None
    assert ctx.leg2_fill_price is None
    assert "unwound" in ctx.error.lower() or "failed" in ctx.error.lower()


@pytest.mark.asyncio
async def test_partial_fill_rejected():
    signal = make_signal(
        score=Decimal("80.0"), size=Decimal("1.0"), net_pnl=Decimal("10.0")
    )
    executor = Executor(
        None,
        None,
        None,
        ExecutorConfig(
            simulation_mode=True, use_flashbots=False, min_fill_ratio=Decimal("0.8")
        ),
    )

    async def partial_cex(sig, size=None):
        return {"success": True, "price": Decimal("2000.0"), "filled": Decimal("0.5")}

    executor._execute_cex_leg = partial_cex

    ctx = await executor.execute(signal)

    assert ctx.state == ExecutorState.FAILED
    assert "partial" in ctx.error.lower() or "threshold" in ctx.error.lower()
    assert ctx.leg2_fill_price is None
    assert ctx.leg2_venue == ""


@pytest.mark.asyncio
async def test_circuit_breaker_blocks():
    signal = make_signal(score=Decimal("80.0"))
    executor = Executor(None, None, None, ExecutorConfig(simulation_mode=True))
    executor.circuit_breaker.trip()

    cex_called = []

    async def spy_cex(sig, size=None):
        cex_called.append(True)
        return {"success": True, "price": Decimal("2000.0"), "filled": Decimal("0.1")}

    executor._execute_cex_leg = spy_cex

    ctx = await executor.execute(signal)

    assert ctx.state == ExecutorState.FAILED
    assert "circuit breaker" in ctx.error.lower()
    assert len(cex_called) == 0


@pytest.mark.asyncio
async def test_replay_protection():
    signal = make_signal(score=Decimal("80.0"))
    executor = Executor(
        None, None, None, ExecutorConfig(simulation_mode=True, use_flashbots=False)
    )

    ctx1 = await executor.execute(signal)
    ctx2 = await executor.execute(signal)

    assert ctx1.state == ExecutorState.DONE, f"Перший: {ctx1.state} / {ctx1.error}"
    assert ctx2.state == ExecutorState.FAILED, f"Другий: {ctx2.state}"
    assert "duplicate" in ctx2.error.lower()
    assert signal.signal_id in executor.replay_protection.executed


# --- Part 4 — Failure Handler ---


def test_circuit_breaker_trips():
    cb = CircuitBreaker()

    cb.record_failure()
    is_open_1 = cb.is_open()

    cb.record_failure()
    is_open_2 = cb.is_open()

    cb.record_failure()
    is_open_3 = cb.is_open()

    assert is_open_1 is False, "1 failure: ще не відкрито"
    assert is_open_2 is False, "2 failures: ще не відкрито"
    assert is_open_3 is True, "3 failures: ВІДКРИТО"
    assert cb.tripped_at is not None
    assert cb.time_until_reset() > 0


def test_circuit_breaker_resets():
    cb = CircuitBreaker()
    cb.cooldown = 600
    cb.tripped_at = time.time() - 700
    cb.failures = [time.time() - 700, time.time() - 701, time.time() - 702]

    result = cb.is_open()

    assert result is False
    assert cb.tripped_at is None, "tripped_at має скинутися"
    assert cb.failures == [], "failures мають очиститися"
    assert cb.time_until_reset() == 0


def test_replay_blocks_duplicate():
    rp = ReplayProtection(ttl_seconds=60)
    signal = make_signal()

    before = rp.is_duplicate(signal)
    rp.mark_executed(signal)
    after = rp.is_duplicate(signal)

    assert before is False
    assert after is True
    assert signal.signal_id in rp.executed


def test_replay_allows_new():
    rp = ReplayProtection()
    s1 = make_signal(pair="ETH/USDT")
    s2 = make_signal(pair="BTC/USDT")

    rp.mark_executed(s1)
    result = rp.is_duplicate(s2)

    assert result is False
    assert s1.signal_id in rp.executed
    assert s2.signal_id not in rp.executed


# --- Додаткові тести (10 штук) ---


def test_fee_breakeven_is_zero_profit():
    fees = FeeStructure(
        cex_taker_bps=Decimal("10"),
        dex_swap_bps=Decimal("30"),
        gas_cost_usd=Decimal("5.0"),
    )
    value = Decimal("2000.0")  # $2000 trade

    be = fees.breakeven_spread_bps(value)  # = 10 + 30 + (5/2000)*10_000 = 65 bps
    profit = fees.net_profit_usd(be, value)

    assert abs(profit) < Decimal("0.01")
    assert (
        fees.net_profit_usd(be + Decimal("1"), value) > 0
    )  # 1 bps вище беззбитковості
    assert fees.net_profit_usd(be - Decimal("1"), value) < 0  # 1 bps нижче → збиток


def test_signal_is_valid_each_flag():
    base = dict(
        spread_bps=Decimal("80"),
        net_pnl=Decimal("10"),
        score=Decimal("75.0"),
        inventory_ok=True,
        within_limits=True,
        ttl=5,
    )

    assert make_signal(**base).is_valid() is True

    s = make_signal(**{**base, "ttl": -1})
    assert s.is_valid() is False, "ttl=-1 → expiry у минулому"

    s = make_signal(**{**base, "inventory_ok": False})
    assert s.is_valid() is False

    s = make_signal(**{**base, "within_limits": False})
    assert s.is_valid() is False

    s = make_signal(**{**base, "net_pnl": Decimal("0")})
    assert s.is_valid() is False, "net_pnl=0 не є > 0"

    s = make_signal(**{**base, "net_pnl": Decimal("-5")})
    assert s.is_valid() is False

    s = make_signal(**{**base, "score": Decimal("0.0")})
    assert s.is_valid() is False, "score=0 не є > 0"


def test_scorer_history_below_three_results():
    scorer = SignalScorer()

    assert scorer._score_history("ETH/USDT") == Decimal("50.0")

    scorer.record_result("ETH/USDT", True)
    assert scorer._score_history("ETH/USDT") == Decimal("50.0")

    scorer.record_result("ETH/USDT", False)
    assert scorer._score_history("ETH/USDT") == Decimal("50.0")

    scorer.record_result("ETH/USDT", True)
    assert scorer._score_history("ETH/USDT") > Decimal("50.0")


def test_scorer_history_penalizes_losing_pair():
    scorer = SignalScorer()
    signal = make_signal(spread_bps=Decimal("70.0"))

    for _ in range(5):
        scorer.record_result("ETH/USDT", False)
    score_losing = scorer.score(signal, [])

    scorer.recent_results = []
    for _ in range(5):
        scorer.record_result("ETH/USDT", True)
    score_winning = scorer.score(signal, [])

    assert score_winning > score_losing
    assert (score_winning - score_losing) == pytest.approx(20.0, abs=Decimal("0.1"))


@pytest.mark.asyncio
async def test_execute_dex_first_leg_order():
    call_order = []

    signal = make_signal(score=Decimal("80.0"))
    executor = Executor(
        None, None, None, ExecutorConfig(simulation_mode=True, use_flashbots=True)
    )

    async def spy_dex(sig, size):
        call_order.append("dex")
        return {"success": True, "price": Decimal("2015.0"), "filled": size}

    async def spy_cex(sig, size=None):
        call_order.append("cex")
        return {"success": True, "price": Decimal("2001.0"), "filled": size or sig.size}

    executor._execute_dex_leg = spy_dex
    executor._execute_cex_leg = spy_cex

    ctx = await executor.execute(signal)

    assert ctx.state == ExecutorState.DONE
    assert call_order == ["dex", "cex"]
    assert ctx.leg1_venue == "dex"
    assert ctx.leg2_venue == "cex"


def test_circuit_breaker_window_excludes_old_failures():
    cb = CircuitBreaker()
    cb.config.window_seconds = 10

    old_time = time.time() - 100
    cb.failures = [old_time, old_time]

    cb.record_failure()

    assert cb.is_open() is False, "1 failure у вікні < threshold=3"
    assert len(cb.failures) == 1, "Старі мають бути видалені при record_failure()"


def test_replay_ttl_expiry_allows_reuse():
    rp = ReplayProtection(ttl_seconds=1)
    signal = make_signal()

    rp.mark_executed(signal)
    assert rp.is_duplicate(signal) is True

    rp.executed[signal.signal_id] = time.time() - 10

    assert rp.is_duplicate(signal) is False
    assert signal.signal_id not in rp.executed


def test_generate_insufficient_inventory_marks_flag():
    ex = make_exchange(bid=2000.0, ask=Decimal("2001.0"))
    inv = make_inventory(enough=False)
    gen = SignalGenerator(
        ex,
        None,
        inv,
        FeeStructure(),
        {
            "min_spread_bps": 10,
            "min_profit_usd": Decimal("1.0"),
            "max_position_usd": 100_000,
        },
    )
    gen._fetch_prices = lambda p, s: {
        "cex_bid": Decimal("2000.0"),
        "cex_ask": Decimal("2001.0"),
        "dex_buy": Decimal("2001.0"),
        "dex_sell": Decimal("2050.0"),
    }

    signal = gen.generate("ETH/USDT", Decimal("5.0"))

    assert signal is not None, "Сигнал повертається навіть при 0 балансі"
    assert signal.inventory_ok is False, "Прапор має бути False"
    assert signal.is_valid() is False, "is_valid() = False бо inventory_ok=False"


def test_generate_exceeds_max_position():
    ex = make_exchange(bid=2000.0, ask=Decimal("2001.0"))
    inv = make_inventory(enough=True)
    gen = SignalGenerator(
        ex,
        None,
        inv,
        FeeStructure(),
        {
            "min_spread_bps": 10,
            "min_profit_usd": Decimal("1.0"),
            "max_position_usd": 100,
        },
    )
    gen._fetch_prices = lambda p, s: {
        "cex_bid": Decimal("2000.0"),
        "cex_ask": Decimal("2001.0"),
        "dex_buy": Decimal("2001.0"),
        "dex_sell": Decimal("2050.0"),
    }

    signal = gen.generate("ETH/USDT", Decimal("5.0"))

    if signal is not None:
        assert signal.within_limits is False
        assert signal.is_valid() is False


def test_exchange_error_returns_none():
    ex = MagicMock()
    ex.fetch_order_book.side_effect = ConnectionError("Exchange unreachable")

    inv = make_inventory()
    gen = SignalGenerator(ex, None, inv, FeeStructure(), {})

    result = gen.generate("ETH/USDT", Decimal("0.1"))

    assert result is None
    ex.fetch_order_book.assert_called_once_with("ETH/USDT")
