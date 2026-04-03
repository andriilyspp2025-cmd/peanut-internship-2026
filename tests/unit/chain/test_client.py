import pytest
from unittest.mock import MagicMock, patch
from src.chain.client import ChainClient
from src.chain.errors import RPCError
from src.core.types import Address, TokenAmount


@pytest.fixture
def mock_chain_client():

    shared_mock_w3 = MagicMock()
    with patch.object(ChainClient, "_connect", return_value=shared_mock_w3):
        client = ChainClient(
            rpc_urls=["http://rpc1.local", "http://rpc2.local", "http://rpc3.local"],
            timeout=1,
            max_retries=3,
        )
        yield client


def test_chainclient_retry_logic_on_429_or_timeout(mock_chain_client):
    # Setup test address
    test_addr = Address("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")

    # We want w3.eth.get_balance to fail with a 429 error the first time, and succeed the second time
    # This triggers exactly one rotation and one retry.

    mock_chain_client.w3.eth.get_balance.side_effect = [
        Exception("HTTP Error 429 Too Many Requests"),
        1500000000000000000,  # Success on second try
    ]

    # Keep track of initial rpc index
    initial_index = mock_chain_client._current_rpc_index

    # Needs to mock time.sleep to not actually sleep!
    with patch("time.sleep", return_value=None):
        result = mock_chain_client.get_balance(test_addr)

    # Verify index incremented
    assert mock_chain_client._current_rpc_index == (initial_index + 1) % len(
        mock_chain_client.rpc_urls
    )

    # Result check
    assert isinstance(result, TokenAmount)
    assert result.raw == 1500000000000000000


def test_chainclient_max_retries_exceeded_raises_rpcerror(mock_chain_client):
    test_addr = Address("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")

    # Make it fail continuously with timeout to trigger rotation 3 times
    mock_chain_client.w3.eth.get_balance.side_effect = Exception("Timeout occurred")

    with patch("time.sleep", return_value=None):
        with pytest.raises(
            RPCError, match="Action get_balance failed after 3 attempts"
        ):
            mock_chain_client.get_balance(test_addr)

    # Index should have rotated `max_retries - 1` times
    assert mock_chain_client._current_rpc_index == 2


def test_chainclient_immediate_failure_on_non_retriable_error(mock_chain_client):
    test_addr = Address("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")

    # ValueError should not be retried because it's not a 429 or timeout pattern initially handled.
    mock_chain_client.w3.eth.get_balance.side_effect = ValueError(
        "Some weird internal parser error"
    )

    with pytest.raises(ValueError, match="Some weird internal parser error"):
        mock_chain_client.get_balance(test_addr)

    # Ensure index didn't increment
    assert mock_chain_client._current_rpc_index == 0
