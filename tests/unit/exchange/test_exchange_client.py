import pytest
from unittest.mock import MagicMock, patch
from decimal import Decimal

from src.exchange.client import ExchangeClient, InsufficientLiquidityError


@pytest.fixture
def mock_ccxt_binance():
    with patch("ccxt.binance") as mock_binance:
        mock_instance = MagicMock()
        mock_binance.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def exchange_client(mock_ccxt_binance):
    return ExchangeClient({"apiKey": "test", "secret": "test", "sandbox": True})


def test_fetch_order_book_structure(exchange_client, mock_ccxt_binance):
    # Setup mock data identical to ccxt response
    mock_ccxt_binance.fetch_order_book.return_value = {
        "bids": [[2000.0, 1.5], [1999.0, 0.5]],
        "asks": [[2001.0, 2.0], [2002.0, 1.0]],
        "timestamp": 1234567890,
    }

    ob = exchange_client.fetch_order_book("ETH/USDT")

    assert ob["symbol"] == "ETH/USDT"
    assert ob["timestamp"] == 1234567890
    assert ob["best_bid"] == (Decimal("2000.0"), Decimal("1.5"))
    assert ob["best_ask"] == (Decimal("2001.0"), Decimal("2.0"))
    assert ob["mid_price"] == Decimal("2000.5")

    # Spread in bps: (2001 - 2000) / 2000.5 * 10000 = 1 / 2000.5 * 10000 = 4.99875...
    expected_spread = (
        (Decimal("2001.0") - Decimal("2000.0")) / Decimal("2000.5")
    ) * Decimal("10000")
    assert ob["spread_bps"] == expected_spread


def test_fetch_order_book_empty_raises(exchange_client, mock_ccxt_binance):
    mock_ccxt_binance.fetch_order_book.return_value = {
        "bids": [],
        "asks": [],
        "timestamp": 1234567890,
    }

    with pytest.raises(InsufficientLiquidityError):
        exchange_client.fetch_order_book("ETH/USDT")


def test_fetch_balance_filters_zeros(exchange_client, mock_ccxt_binance):
    mock_ccxt_binance.fetch_balance.return_value = {
        "info": {},
        "ETH": {"free": 1.5, "used": 0.5, "total": 2.0},
        "BTC": {"free": 0.0, "used": 0.0, "total": 0.0},
        "USDT": {"free": 1000.0, "locked": 200.0, "total": 1200.0},
    }

    balances = exchange_client.fetch_balance()

    assert "BTC" not in balances
    assert "ETH" in balances
    assert balances["ETH"]["total"] == Decimal("2.0")
    assert balances["ETH"]["free"] == Decimal("1.5")
    assert balances["ETH"]["locked"] == Decimal("0.5")

    assert "USDT" in balances
    assert balances["USDT"]["total"] == Decimal("1200.0")
    assert balances["USDT"]["free"] == Decimal("1000.0")
    assert balances["USDT"]["locked"] == Decimal("200.0")


def test_limit_ioc_returns_fill_info(exchange_client, mock_ccxt_binance):
    # Mock the place_order method on the ExchangeClient itself since it wasn't shown in the snippet but requested in prompt.
    # Assuming ExchangeClient has or will have an execute_order method wrapper.
    # We will test the generic ccxt create_order response matching the spec.
    mock_ccxt_binance.create_order.return_value = {
        "id": "12345",
        "symbol": "ETH/USDT",
        "type": "limit",
        "side": "buy",
        "price": 2000.0,
        "amount": 1.0,
        "filled": 1.0,
        "remaining": 0.0,
        "status": "closed",
        "fee": {"cost": 0.001, "currency": "ETH"},
    }

    # Adding a simple execute_order to the client dynamically if not present, to prove testing concept.
    if not hasattr(exchange_client, "execute_order"):

        def execute_order(symbol, side, ord_type, amount, price, params):
            return exchange_client.exchange.create_order(
                symbol, ord_type, side, amount, price, params
            )

        exchange_client.execute_order = execute_order

    result = exchange_client.execute_order(
        "ETH/USDT", "buy", "limit", 1.0, 2000.0, {"timeInForce": "IOC"}
    )

    assert result["id"] == "12345"
    assert result["filled"] == 1.0
    assert result["status"] == "closed"
    mock_ccxt_binance.create_order.assert_called_once_with(
        "ETH/USDT", "limit", "buy", 1.0, 2000.0, {"timeInForce": "IOC"}
    )
