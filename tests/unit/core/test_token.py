from src.core.types import Address, Token


def test_token_equality_by_address_only():
    addr = Address("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")
    token1 = Token(address=addr, symbol="USDC", decimals=6)
    token2 = Token(address=addr, symbol="USDT", decimals=18)

    assert (
        token1 == token2
    ), "Token equality should strictly check address, not symbol or decimals"


def test_token_hash_matches_equality():
    addr1 = Address("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")
    addr2 = Address("0xd8da6bf26964af9d7eed9e03e53415d37aa96045")

    token1 = Token(address=addr1, symbol="USDC", decimals=6)
    token2 = Token(address=addr2, symbol="USDT", decimals=18)

    assert hash(token1) == hash(token2)


def test_token_inequality():
    token1 = Token(
        address=Address("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"),
        symbol="USDC",
        decimals=6,
    )
    token2 = Token(
        address=Address("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"),
        symbol="USDC",
        decimals=6,
    )

    assert token1 != token2
