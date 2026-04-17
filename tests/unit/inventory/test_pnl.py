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
        fee_usd=Decimal("2.0"),
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
        fee_usd=Decimal("3.0"),
    )
    return ArbRecord("arb-1", datetime.now(), buy, sell, Decimal("5.0"))


def test_gross_pnl_calculation(sample_trade):
    assert sample_trade.gross_pnl == Decimal("10.0")


def test_net_pnl_includes_all_fees(sample_trade):
    assert sample_trade.total_fees == Decimal("10.0")
    assert sample_trade.net_pnl == Decimal("0.0")


def test_pnl_bps_calculation(sample_trade):
    assert sample_trade.net_pnl_bps == Decimal("0.0")
    sample_trade.sell_leg.price = Decimal("2020.0")
    assert sample_trade.net_pnl == Decimal("10.0")
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
        Decimal("2.0"),
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
        Decimal("3.0"),
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
        Decimal("1.0"),
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
        Decimal("1.5"),
    )
    trade2 = ArbRecord("t2", datetime.now(), buy2, sell2, Decimal("5.0"))

    engine.record(trade1)
    engine.record(trade2)
    summary = engine.summary()

    assert summary["total_trades"] == 2
    assert summary["total_pnl_usd"] == Decimal("-7.5")
    assert summary["total_fees_usd"] == Decimal("17.5")
    assert summary["total_notional"] == Decimal("3000.0")
    assert summary["win_rate"] == 0.5
