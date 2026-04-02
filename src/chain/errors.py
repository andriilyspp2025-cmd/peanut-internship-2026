from __future__ import annotations
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.core.types import TransactionReceipt


class ChainError(Exception):
    """Base class for all chain-related errors."""

    pass


class RPCError(ChainError):
    """RPC request failed (network issue, rate limit, etc.)."""

    def __init__(self, message: str, code: Optional[int] = None):
        self.code = code
        super().__init__(message)


class TransactionFailed(ChainError):
    """Transaction was mined but reverted on-chain."""

    def __init__(self, tx_hash: str, receipt: TransactionReceipt):
        self.tx_hash = tx_hash
        self.receipt = receipt
        super().__init__(
            f"Transaction {tx_hash} reverted in block {receipt.block_number}"
        )


class TransactionTimeout(ChainError):
    """Transaction was not confirmed within the expected timeframe."""

    def __init__(self, tx_hash: str, timeout: int):
        self.tx_hash = tx_hash
        self.timeout = timeout
        super().__init__(f"Transaction {tx_hash} not confirmed after {timeout} seconds")


class InsufficientFunds(ChainError):
    """Account balance is too low to cover value + gas."""

    pass


class NonceTooLow(ChainError):
    """Nonce has already been used (transaction already confirmed)."""

    pass


class ReplacementUnderpriced(ChainError):
    """Replacement transaction rejected: gas price must be higher."""

    pass
