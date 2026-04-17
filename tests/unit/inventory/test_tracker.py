import pytest
from decimal import Decimal

from src.inventory.tracker import InventoryTracker, Venue


@pytest.fixture
def tracker():
    return InventoryTracker([Venue.BINANCE, Venue.WALLET])


def test_snapshot_aggregates_across_venues(tracker):
    tracker.update_from_cex(
        Venue.BINANCE,
        {
            "ETH": {"free": 1.5, "locked": 0.5, "total": 2.0},
            "USDT": {"free": 1000.0, "locked": 0.0, "total": 1000.0},
        },
    )

    tracker.update_from_wallet(Venue.WALLET, {"ETH": 3.0, "USDT": 500.0})

    snapshot = tracker.snapshot()

    assert "timestamp" in snapshot
    assert snapshot["totals"]["ETH"] == Decimal("5.0")
    assert snapshot["totals"]["USDT"] == Decimal("1500.0")

    binance_eth = snapshot["venues"][Venue.BINANCE]["ETH"]
    assert binance_eth["free"] == Decimal("1.5")
    assert binance_eth["locked"] == Decimal("0.5")
    assert binance_eth["total"] == Decimal("2.0")

    wallet_eth = snapshot["venues"][Venue.WALLET]["ETH"]
    assert wallet_eth["free"] == Decimal("3.0")
    assert wallet_eth["locked"] == Decimal("0.0")
    assert wallet_eth["total"] == Decimal("3.0")


def test_can_execute_passes_when_sufficient(tracker):
    tracker.update_from_cex(
        Venue.BINANCE, {"USDT": {"free": 2000.0, "locked": 0, "total": 2000.0}}
    )
    tracker.update_from_wallet(Venue.WALLET, {"ETH": 5.0})

    result = tracker.can_execute(
        buy_venue=Venue.BINANCE,
        buy_asset="USDT",
        buy_amount=Decimal("1500.0"),
        sell_venue=Venue.WALLET,
        sell_asset="ETH",
        sell_amount=Decimal("1.0"),
    )

    assert result["can_execute"] is True
    assert result["reason"] is None


def test_can_execute_fails_insufficient_buy(tracker):
    tracker.update_from_cex(
        Venue.BINANCE, {"USDT": {"free": 1000.0, "locked": 0, "total": 1000.0}}
    )
    tracker.update_from_wallet(Venue.WALLET, {"ETH": 5.0})

    result = tracker.can_execute(
        buy_venue=Venue.BINANCE,
        buy_asset="USDT",
        buy_amount=Decimal("1500.0"),
        sell_venue=Venue.WALLET,
        sell_asset="ETH",
        sell_amount=Decimal("1.0"),
    )

    assert result["can_execute"] is False
    assert result["reason"] == f"Not enough USDT on {Venue.BINANCE} (needed 1500.0)"


def test_record_trade_updates_balances(tracker):
    tracker.update_from_cex(
        Venue.BINANCE,
        {
            "ETH": {"free": 1.0, "locked": 0.0, "total": 1.0},
            "USDT": {"free": 3000.0, "locked": 0.0, "total": 3000.0},
        },
    )

    # Simulate buying 2 ETH for 4000 USDT (with 5 USDT fee taken from USDT)
    tracker.record_trade(
        venue=Venue.BINANCE,
        side="buy",
        base_asset="ETH",
        quote_asset="USDT",
        base_amount=Decimal("2.0"),
        quote_amount=Decimal("4000.0"),
        fee=Decimal("5.0"),
        fee_asset="USDT",
    )

    snapshot = tracker.snapshot()
    binance_balances = snapshot["venues"][Venue.BINANCE]

    # ETH increases by 2.0 -> 3.0
    assert binance_balances["ETH"]["free"] == Decimal("3.0")
    # USDT decreases by 4000.0 quote amount + 5.0 fee: 3000 - 4000 - 5 = -1005
    assert binance_balances["USDT"]["free"] == Decimal("-1005.0")
