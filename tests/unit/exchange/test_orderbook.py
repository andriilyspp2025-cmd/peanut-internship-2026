import pytest
from decimal import Decimal
from src.exchange.orderbook import OrderBookAnalyzer


@pytest.fixture
def mock_orderbook_data():
    return {
        "symbol": "ETH/USDT",
        "timestamp": 1234567890,
        "bids": [
            [Decimal("2000.0"), Decimal("1.5")],
            [Decimal("1999.0"), Decimal("2.0")],
            [Decimal("1990.0"), Decimal("10.0")],
        ],
        "asks": [
            [Decimal("2001.0"), Decimal("2.0")],
            [Decimal("2002.0"), Decimal("1.5")],
            [Decimal("2010.0"), Decimal("10.0")],
        ],
        "best_bid": (Decimal("2000.0"), Decimal("1.5")),
        "best_ask": (Decimal("2001.0"), Decimal("2.0")),
        "mid_price": Decimal("2000.5"),
        "spread_bps": Decimal("4.9987503124"),
    }


@pytest.fixture
def analyzer(mock_orderbook_data):
    return OrderBookAnalyzer(mock_orderbook_data)


def test_spread_calculation(analyzer, mock_orderbook_data):
    assert analyzer.bids == mock_orderbook_data["bids"]
    assert analyzer.asks == mock_orderbook_data["asks"]
    assert analyzer.mid_price == Decimal("2000.5")
    assert analyzer.best_bid_price == Decimal("2000.0")
    assert analyzer.best_ask_price == Decimal("2001.0")


def test_walk_the_book_exact_fill(analyzer):
    # Ask has 2.0 @ 2001.0 and 1.5 @ 2002.0
    # We want to buy exact 2.0
    result = analyzer.walk_the_book("buy", qty=2.0)

    assert result["fully_filled"] is True
    assert result["levels_consumed"] == 1
    assert len(result["fills"]) == 1
    assert result["fills"][0]["qty"] == Decimal("2.0")
    assert result["fills"][0]["price"] == Decimal("2001.0")
    assert result["total_cost"] == Decimal("4002.0")  # 2.0 * 2001.0
    assert result["avg_price"] == Decimal("2001.0")
    assert result["slippage_bps"] == Decimal("0.0")


def test_effective_spread_greater_than_quoted(analyzer):
    # Walk asks with qty=3.0 (consumes 2.0 @ 2001.0 and 1.0 @ 2002.0)
    result = analyzer.walk_the_book("buy", qty=3.0)

    assert result["fully_filled"] is True
    assert result["levels_consumed"] == 2

    total_cost = (Decimal("2.0") * Decimal("2001.0")) + (
        Decimal("1.0") * Decimal("2002.0")
    )
    assert result["total_cost"] == total_cost

    avg_price = total_cost / Decimal("3.0")
    assert result["avg_price"] == avg_price

    # Slippage compared to best ask (2001.0)
    expected_slippage_bps = (
        (avg_price - Decimal("2001.0")) / Decimal("2001.0")
    ) * Decimal("10000")
    assert result["slippage_bps"] == expected_slippage_bps
    assert result["slippage_bps"] > Decimal("0")


def test_walk_the_book_insufficient_liquidity(analyzer):
    # We want to sell 15.0, but bids only have 1.5 + 2.0 + 10.0 = 13.5
    result = analyzer.walk_the_book("sell", qty=15.0)

    assert result["fully_filled"] is False
    assert result["levels_consumed"] == 3
    assert len(result["fills"]) == 3

    total_filled_qty = sum(f["qty"] for f in result["fills"])
    assert total_filled_qty == Decimal("13.5")

    expected_cost = (
        (Decimal("1.5") * Decimal("2000.0"))
        + (Decimal("2.0") * Decimal("1999.0"))
        + (Decimal("10.0") * Decimal("1990.0"))
    )
    assert result["total_cost"] == expected_cost

    avg_price = expected_cost / Decimal("13.5")
    assert result["avg_price"] == avg_price


def test_walk_the_book_invalid_side(analyzer):
    with pytest.raises(ValueError, match="side must be 'buy' or 'sell'"):
        analyzer.walk_the_book("invalid", qty=1.0)
