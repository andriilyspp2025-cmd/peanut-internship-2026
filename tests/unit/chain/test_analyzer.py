import pytest
from unittest.mock import patch, MagicMock

from src.chain.analyzer import analyze, format_report


@pytest.fixture
def mock_chain_client():
    with patch("src.chain.analyzer.ChainClient") as mock_client_cls:
        client = MagicMock()
        mock_client_cls.return_value = client
        yield client


def test_analyzer_parsing_success_receipt(mock_chain_client):
    # Mock the get_transaction and get_receipt responses
    mock_chain_client.get_transaction.return_value = {
        "hash": "0x123",
        "from": "0xSender",
        "to": "0xReceiver",
        "value": 500000000000000000,  # 0.5 ETH
        "gas": 250000,
        "input": "0x",
    }

    mock_receipt = MagicMock()
    mock_receipt.block_number = 18234567
    mock_receipt.status = True
    mock_receipt.gas_used = 187432
    mock_receipt.effective_gas_price = 27500000000  # 27.5 gwei
    mock_chain_client.get_receipt.return_value = mock_receipt

    mock_chain_client.w3.eth.get_block.return_value = {"timestamp": 1705328625}

    analysis = analyze("0x123", "http://rpc.local")

    assert analysis["transaction"]["hash"] == "0x123"
    assert analysis["receipt"]["status"] is True
    assert analysis["receipt"]["effective_gas_price"] == 27500000000

    # Test formatting
    report = format_report(analysis)
    assert "Status:         SUCCESS" in report
    assert "Gas Limit:      250,000" in report
    assert "Gas Used:       187,432 (74.97%)" in report
    assert "Effective Price: 27.50 gwei" in report
    assert "Transaction Fee: 0.00515 ETH" in report  # 187432 * 27.5 gwei


def test_analyzer_parsing_failed_receipt(mock_chain_client):
    mock_chain_client.get_transaction.return_value = {
        "hash": "0x123",
        "from": "0xSender",
        "to": "0xReceiver",
        "value": 0,
        "gas": 100000,
        "input": "0x",
    }

    mock_receipt = MagicMock()
    mock_receipt.block_number = 10000
    mock_receipt.status = False
    mock_receipt.gas_used = 100000
    mock_receipt.effective_gas_price = 10000000000  # 10 gwei
    mock_chain_client.get_receipt.return_value = mock_receipt

    analysis = analyze("0x123", "http://rpc.local")

    assert analysis["receipt"]["status"] is False

    report = format_report(analysis)
    assert "Status:         FAILED / REVERTED" in report


def test_analyzer_tx_not_found(mock_chain_client):
    mock_chain_client.get_transaction.return_value = None

    with pytest.raises(ValueError, match="Transaction not found."):
        analyze("0xnonexistent", "http://rpc.local")
