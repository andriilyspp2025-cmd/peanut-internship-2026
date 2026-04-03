import pytest
from unittest.mock import MagicMock
from decimal import Decimal

from src.chain.builder import TransactionBuilder
from src.core.types import Address, TokenAmount


@pytest.fixture
def mock_chain_client():
    from src.chain.client import ChainClient, GasPrice

    client = MagicMock(spec=ChainClient)
    # Return 15 base fee
    priority_low = int(1 * 1e9)
    priority_medium = int(2 * 1e9)
    priority_high = int(5 * 1e9)
    gp = GasPrice(
        base_fee=int(15 * 1e9),
        priority_fee_low=priority_low,
        priority_fee_medium=priority_medium,
        priority_fee_high=priority_high,
    )
    client.get_gas_price.return_value = gp
    client.estimate_gas.return_value = 21000
    client.get_nonce.return_value = 42
    return client


@pytest.fixture
def mock_wallet():
    from src.core.wallet import WalletManager

    wallet = MagicMock(spec=WalletManager)
    wallet.address = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
    return wallet


def test_builder_build_autofills_nonce_and_gas(mock_chain_client, mock_wallet):
    builder = TransactionBuilder(mock_chain_client, mock_wallet)

    to_addr = Address("0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9")
    amount = TokenAmount.from_human("1.0", 18)

    # We only set the core elements
    builder.to(to_addr).value(amount).data(b"")

    tx = builder.build()

    # Verify autofilled fields
    assert tx.nonce == 42  # mocked from get_nonce
    assert tx.gas_limit == int(21000 * Decimal("1.2"))  # 21000 * 1.2

    # max_fee_per_gas computation from `medium`: 15 * 1.2 + 2 = 18 + 2 = 20 gwei
    assert tx.max_fee_per_gas == int(20 * 1e9)
    assert tx.max_priority_fee == int(2 * 1e9)
    assert tx.to == to_addr
    assert tx.value == amount


def test_builder_missing_core_fields_raises(mock_chain_client, mock_wallet):
    builder = TransactionBuilder(mock_chain_client, mock_wallet)

    # Missing value and data
    builder.to(Address("0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9"))

    with pytest.raises(ValueError, match="Missing required fields: value, data"):
        builder.build()


def test_builder_explicit_values_not_overwritten(mock_chain_client, mock_wallet):
    builder = TransactionBuilder(mock_chain_client, mock_wallet)

    to_addr = Address("0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9")
    amount = TokenAmount.from_human("0.5", 18)

    builder.to(to_addr).value(amount).data(b"").nonce(99).gas_limit(50000)

    tx = builder.build()

    assert tx.nonce == 99
    assert tx.gas_limit == 50000

    # Verify get_nonce / estimate_gas weren't called internally because we supplied overrides
    mock_chain_client.get_nonce.assert_not_called()
    mock_chain_client.estimate_gas.assert_not_called()


def test_builder_negative_nonce_raises(mock_chain_client, mock_wallet):
    builder = TransactionBuilder(mock_chain_client, mock_wallet)
    with pytest.raises(ValueError, match="nonce must be non-negative"):
        builder.nonce(-1)


def test_builder_zero_or_negative_gas_limit_raises(mock_chain_client, mock_wallet):
    builder = TransactionBuilder(mock_chain_client, mock_wallet)
    with pytest.raises(ValueError, match="gas_limit must be strictly positive"):
        builder.gas_limit(0)

    with pytest.raises(ValueError, match="gas_limit must be strictly positive"):
        builder.gas_limit(-500)
