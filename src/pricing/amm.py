from decimal import Decimal
from src.core.types import Token, Address
from src.pricing.errors import (
    FeeError,
    InsufficientLiquidityError,
    AmountError,
    InvalidTokenError,
)
from src.chain.client import ChainClient


class UniswapV2Pair:
    """Represents a Uniswap V2 liquidity pair with exact integer arithmetic."""

    def __init__(
        self,
        address: Address,
        token0: Token,
        token1: Token,
        reserve0: int,
        reserve1: int,
        fee_bps: int = 30,
    ):
        self.address = address
        self.token0 = token0
        self.token1 = token1
        self.reserve0 = reserve0
        self.reserve1 = reserve1
        self.fee_bps = fee_bps

        if self.fee_bps >= 10000 or self.fee_bps < 0:
            raise FeeError()
        if self.reserve0 <= 0 or self.reserve1 <= 0:
            raise InsufficientLiquidityError()

    def get_amount_out(self, amount_in: int, token_in: "Token") -> int:
        """Calculates expected output amount using constant product formula."""
        if amount_in <= 0:
            raise AmountError()

        if token_in == self.token0:
            reserve_in, reserve_out = self.reserve0, self.reserve1
        elif token_in == self.token1:
            reserve_in, reserve_out = self.reserve1, self.reserve0
        else:
            raise InvalidTokenError()

        amount_in_with_fee = amount_in * (10000 - self.fee_bps)
        numerator = amount_in_with_fee * reserve_out
        denominator = (reserve_in * 10000) + amount_in_with_fee
        amount_out = numerator // denominator

        return amount_out

    def get_amount_in(self, amount_out: int, token_out: "Token") -> int:
        """
        Calculate required input for desired output.
        (Inverse of get_amount_out)
        """
        if amount_out <= 0:
            raise AmountError()

        if token_out == self.token0:
            reserve_in, reserve_out = self.reserve1, self.reserve0
        elif token_out == self.token1:
            reserve_in, reserve_out = self.reserve0, self.reserve1
        else:
            raise InvalidTokenError()

        if amount_out >= reserve_out:
            raise InsufficientLiquidityError()

        numerator = reserve_in * amount_out * 10000
        denominator = (reserve_out - amount_out) * (10000 - self.fee_bps)
        amount_in = (numerator // denominator) + 1

        return amount_in

    def get_spot_price(self, token_in: Token) -> Decimal:
        """
        Returns spot price (for display only, not calculations).
        """
        if token_in == self.token0:
            reserve_in, reserve_out = self.reserve0, self.reserve1
        elif token_in == self.token1:
            reserve_in, reserve_out = self.reserve1, self.reserve0
        else:
            raise InvalidTokenError()
        return Decimal(reserve_out) / Decimal(reserve_in)

    def get_execution_price(self, amount_in: int, token_in: Token) -> Decimal:
        """
        Returns actual execution price for given trade size.
        """
        amount_out = self.get_amount_out(amount_in, token_in)
        return Decimal(amount_out) / Decimal(amount_in)

    def get_price_impact(self, amount_in: int, token_in: Token) -> Decimal:
        """
        Returns price impact as a decimal (0.01 = 1%).
        """
        spot_price, execution_price = self.get_spot_price(
            token_in
        ), self.get_execution_price(amount_in, token_in)
        if spot_price == 0:
            return Decimal(0)
        impact = (spot_price - execution_price) / spot_price
        return impact

    def simulate_swap(self, amount_in: int, token_in: Token) -> "UniswapV2Pair":
        """
        Returns a new pair with updated reserves after the swap.
        """
        amount_out = self.get_amount_out(amount_in, token_in)

        if token_in == self.token0:
            new_reserve0, new_reserve1 = (
                self.reserve0 + amount_in,
                self.reserve1 - amount_out,
            )
        elif token_in == self.token1:
            new_reserve0, new_reserve1 = (
                self.reserve0 - amount_out,
                self.reserve1 + amount_in,
            )
        else:
            raise InvalidTokenError()

        return UniswapV2Pair(
            self.address,
            self.token0,
            self.token1,
            new_reserve0,
            new_reserve1,
            self.fee_bps,
        )

    @classmethod
    def from_chain(cls, address: Address, client: ChainClient) -> "UniswapV2Pair":
        """
        Fetches pair token addresses, decimals, and reserves from the chain.
        """
        token0_addr_bytes = client.w3.eth.call(
            {"to": address.checksum, "data": "0x0dfe1681"}
        )
        token1_addr_bytes = client.w3.eth.call(
            {"to": address.checksum, "data": "0xd21220a7"}
        )

        t0_addr_str = client.w3.to_checksum_address(
            "0x" + token0_addr_bytes[-20:].hex()
        )
        t1_addr_str = client.w3.to_checksum_address(
            "0x" + token1_addr_bytes[-20:].hex()
        )

        t0_addr = Address(t0_addr_str)
        t1_addr = Address(t1_addr_str)

        def _get_token_decimals(token_address_checksum: str) -> int:
            try:
                res = client.w3.eth.call(
                    {"to": token_address_checksum, "data": "0x313ce567"}
                )
                return int.from_bytes(res, "big") if res else 18
            except Exception:
                return 18

        dec0 = _get_token_decimals(t0_addr_str)
        dec1 = _get_token_decimals(t1_addr_str)

        token0 = Token(t0_addr, "TKN0", dec0)
        token1 = Token(t1_addr, "TKN1", dec1)

        reserves_bytes = client.w3.eth.call(
            {"to": address.checksum, "data": "0x0902f1ac"}
        )
        reserve0 = int.from_bytes(reserves_bytes[0:32], "big")
        reserve1 = int.from_bytes(reserves_bytes[32:64], "big")

        return cls(address, token0, token1, reserve0, reserve1)
