import pytest
from decimal import Decimal
from src.core.types import Address, TokenAmount


def test_address_validation_success():
    valid_address = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"  # valid checksum
    addr = Address(valid_address)
    assert addr.value == valid_address
    assert addr.checksum == valid_address
    assert addr.lower == valid_address.lower()


def test_address_invalid_raises_error():
    with pytest.raises(ValueError, match="Address validation failed"):
        Address("0xinvalidaddress")


def test_address_equality_different_casing():
    addr1 = Address("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")
    # Case-insensitive representation equivalent
    addr2 = Address("0xd8da6bf26964af9d7eed9e03e53415d37aa96045")

    assert addr1 == addr2
    assert addr1.value == addr2.value  # Both converted to checksum correctly


def test_tokenamount_from_human_conversion():
    amount = TokenAmount.from_human("1.5", 18)
    assert amount.raw == 1500000000000000000
    assert amount.decimals == 18


def test_tokenamount_from_human_zero():
    amount = TokenAmount.from_human("0", 18)
    assert amount.raw == 0


def test_tokenamount_from_human_float_raises_type_error():
    with pytest.raises(TypeError, match="Strict precision enforced"):
        TokenAmount.from_human(1.5, 18)


def test_tokenamount_addition_success():
    amt1 = TokenAmount.from_human("1.0", 18, "ETH")
    amt2 = TokenAmount.from_human("0.5", 18, "ETH")
    result = amt1 + amt2
    assert result.raw == 1500000000000000000
    assert result.decimals == 18
    assert result.symbol == "ETH"
    assert result.human == Decimal("1.5")


def test_tokenamount_addition_mismatched_decimals_raises():
    amt1 = TokenAmount.from_human("1.0", 18, "ETH")
    amt2 = TokenAmount.from_human("1.0", 6, "USDC")
    with pytest.raises(ValueError, match="Incompatible token decimals"):
        amt1 + amt2


def test_tokenamount_subtraction_drops_below_zero_raises():
    amt1 = TokenAmount.from_human("1.0", 18, "ETH")
    amt2 = TokenAmount.from_human("1.5", 18, "ETH")
    with pytest.raises(ValueError, match="cannot drop below zero"):
        amt1 - amt2
