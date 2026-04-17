import pytest
from decimal import Decimal
from unittest.mock import MagicMock

from src.inventory.tracker import Venue, InventoryTracker
from src.inventory.rebalancer import RebalancePlanner, TransferPlan


@pytest.fixture
def mock_tracker():
    tracker = MagicMock(spec=InventoryTracker)

    def skew(asset, target_ratio=None):
        if asset == "ETH":
            return {
                "needs_rebalance": True,
                "total": Decimal("10.0"),
                "venues": {
                    Venue.BINANCE: {"amount": Decimal("9.0")},
                    Venue.WALLET: {"amount": Decimal("1.0")},
                },
            }
        elif asset == "USDT":
            return {
                "needs_rebalance": False,
                "total": Decimal("1000.0"),
                "venues": {
                    Venue.BINANCE: {"amount": Decimal("500.0")},
                    Venue.WALLET: {"amount": Decimal("500.0")},
                },
            }
        elif asset == "USDC":
            return {
                "needs_rebalance": True,
                "total": Decimal("1000.0"),
                "venues": {
                    Venue.BINANCE: {"amount": Decimal("950.0")},
                    Venue.WALLET: {"amount": Decimal("50.0")},
                },
            }
        return {}

    def get_skews(target_ratio=None):
        return [
            {"asset": "ETH", "needs_rebalance": True},
            {"asset": "USDT", "needs_rebalance": False},
        ]

    tracker.skew = skew
    tracker.get_skews = get_skews
    return tracker


@pytest.fixture
def planner(mock_tracker):
    return RebalancePlanner(tracker=mock_tracker)


def test_check_detects_skewed_asset(planner):
    skews = planner.check_all()
    assert len(skews) == 1
    assert skews[0]["asset"] == "ETH"
    assert skews[0]["needs_rebalance"] is True


def test_plan_generates_correct_transfer(planner):
    # Total ETH = 10.0. Binance has 9.0, Wallet has 1.0. Target ratio is 0.5/0.5
    # Binance should send 4.0 ETH to Wallet.
    # Fee is 0.002, Min amount: 0.01. Operating Balance ETH = 0.1
    plans = planner.plan("ETH")

    assert len(plans) == 1
    plan = plans[0]

    assert isinstance(plan, TransferPlan)
    assert plan.from_venue == Venue.BINANCE
    assert plan.to_venue == Venue.WALLET
    assert plan.amount == Decimal("4.0")
    assert plan.asset == "ETH"
    assert plan.estimated_fee == Decimal("0.002")
    assert plan.estimated_time_min == 15
    assert plan.net_amount == Decimal("3.998")


def test_plan_empty_when_balanced(planner):
    # USDT has needs_rebalance = False
    plans = planner.plan("USDT")
    assert len(plans) == 0


def test_plan_respects_min_operating_balance(planner):
    # In USDC, Binance has 950, Wallet has 50. Total = 1000.
    # Target = 500 each.
    # Transfer amount = 450
    # Min operating balance for USDC = 100.
    # Binance donor balance: 950 - 450 = 500 >= 100. It should produce the plan.

    # What if Binance only had 110, Wallet had 10? Total = 120. Target = 60.
    # Transfer 50. Balance donor: 110 - 50 = 60. But Min op balance for USDC is 100.
    # So it should return empty.

    mock_tracker_custom = MagicMock(spec=InventoryTracker)
    mock_tracker_custom.skew.return_value = {
        "needs_rebalance": True,
        "total": Decimal("120.0"),
        "venues": {
            Venue.BINANCE: {"amount": Decimal("110.0")},
            Venue.WALLET: {"amount": Decimal("10.0")},
        },
    }

    planner_custom = RebalancePlanner(tracker=mock_tracker_custom)
    plans = planner_custom.plan("USDC")

    assert len(plans) == 0
