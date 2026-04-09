"""
pricing/protocols.py — Structural interfaces (Protocols) for the pricing module.
"""

from __future__ import annotations
from decimal import Decimal
from typing import Protocol, runtime_checkable
from src.core.types import Address, Token


@runtime_checkable
class AMMPool(Protocol):
    """Minimal interface every AMM pool implementation must satisfy."""

    address: Address
    token0: Token
    token1: Token

    def get_amount_out(self, amount_in: int, token_in: Token) -> int:
        """Return the raw output amount for amount_in of token_in."""
        ...

    def get_spot_price(self, token_in: Token) -> Decimal:
        """Return the marginal price as token_out units per token_in unit."""
        ...

    def get_price_impact(self, amount_in: int, token_in: Token) -> Decimal:
        """Return price impact as a fraction in [0, 1] (0.01 = 1%)."""
        ...
