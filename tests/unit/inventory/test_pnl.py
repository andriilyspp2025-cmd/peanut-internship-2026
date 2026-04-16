import pytest
from decimal import Decimal
from datetime import datetime

from src.inventory.tracker import Venue
from src.inventory.pnl import TradeLeg, ArbRecord, PnLEngine


@pytest.fixture
def sample_trade():
    buy = TradeLeg(
        id="1",
        timestamp=datetime.now(),
        venue=Venue.BINANCE,
        symbol="ETH/USDT",
        side="buy",
        amount=Decimal("1.0"),
        price=Decimal("2000.0"),
        fee=Decimal("2.0"),
        fee_asset="USDT",
    )
    sell = TradeLeg(
        id="2",
        timestamp=datetime.now(),
        venue=Venue.WALLET,
        symbol="ETH/USDT",
        side="sell",
        amount=Decimal("1.0"),
        price=Decimal("2010.0"),
        fee=Decimal("3.0"),
        fee_asset="USDT",
    )
    return ArbRecord(
        id="arb-1",
        timestamp=datetime.now(),
        buy_leg=buy,
        sell_leg=sell,
        gas_cost_usd=Decimal("5.0"),
    )


def test_gross_pnl_calculation(sample_trade):
    # Sell 1 @ 2010 = 2010 Rev
    # Buy 1 @ 2000 = 2000 Cost
    # Gross = 10
    assert sample_trade.gross_pnl == Decimal("10.0")


def test_net_pnl_includes_all_fees(sample_trade):
    # Gross = 10
    # Fees: 2.0 + 3.0 + 5.0 (gas) = 10.0
    # Net = 0
    assert sample_trade.total_fees == Decimal("10.0")
    assert sample_trade.net_pnl == Decimal("0.0")


def test_pnl_bps_calculation(sample_trade):
    # Notional = 1.0 * 2000.0 = 2000.0
    # Net PnL = 0.0
    assert sample_trade.net_pnl_bps == Decimal("0.0")

    # Increase sell price to 2020 -> Gross = 20, Net = 10
    sample_trade.sell_leg.price = Decimal("2020.0")
    assert sample_trade.net_pnl == Decimal("10.0")

    # Bps = (10.0 / 2000.0) * 10000 = 50.0
    assert sample_trade.net_pnl_bps == Decimal("50.0")


def test_summary_with_no_trades():
    engine = PnLEngine()
    summary = engine.summary()
    assert summary["total_trades"] == 0
    assert summary["win_rate"] == 0.0
    assert summary["total_pnl_usd"] == Decimal("0")


def test_summary_aggregation():
    engine = PnLEngine()

    buy1 = TradeLeg(
        "1",
        datetime.now(),
        Venue.BINANCE,
        "ETH/USDT",
        "buy",
        Decimal("1.0"),
        Decimal("2000.0"),
        Decimal("2.0"),
        "USDT",
    )
    sell1 = TradeLeg(
        "2",
        datetime.now(),
        Venue.WALLET,
        "ETH/USDT",
        "sell",
        Decimal("1.0"),
        Decimal("2020.0"),
        Decimal("3.0"),
        "USDT",
    )
    trade1 = ArbRecord("t1", datetime.now(), buy1, sell1, Decimal("5.0"))

    buy2 = TradeLeg(
        "3",
        datetime.now(),
        Venue.WALLET,
        "ETH/USDT",
        "buy",
        Decimal("0.5"),
        Decimal("2000.0"),
        Decimal("1.0"),
        "USDT",
    )
    sell2 = TradeLeg(
        "4",
        datetime.now(),
        Venue.BINANCE,
        "ETH/USDT",
        "sell",
        Decimal("0.5"),
        Decimal("1980.0"),
        Decimal("1.5"),
        "USDT",
    )
    trade2 = ArbRecord("t2", datetime.now(), buy2, sell2, Decimal("5.0"))

    engine.record(trade1)
    engine.record(trade2)

    summary = engine.summary()

    assert summary["total_trades"] == 2

    # Trade 1: Gross=20, Fees=10, Net=10. Notional=2000
    # Trade 2: Gross=(0.5*1980 - 0.5*2000)=-10, Fees=7.5, Net=-17.5. Notional=1000
    # Total Net = 10 + (-17.5) = -7.5
    # Total Fees = 17.5
    # Total Notional = 3000

    assert summary["total_pnl_usd"] == Decimal("-7.5")
    assert summary["total_fees_usd"] == Decimal("17.5")
    assert summary["total_notional"] == Decimal("3000.0")
    assert summary["win_rate"] == 0.5
    assert summary["best_trade_pnl"] == Decimal("10.0")
    assert summary["worst_trade_pnl"] == Decimal("-17.5")
