from typing import Optional
from src.core.types import TransactionReceipt


class ChainError(Exception):
    """Base class for chain errors."""

    pass


class RPCError(ChainError):
    """RPC request failed."""

    def __init__(self, message: str, code: Optional[int] = None):
        self.code = code
        super().__init__(message)


class TransactionFailed(ChainError):
    """Transaction reverted."""

    def __init__(self, tx_hash: str, receipt: TransactionReceipt):
        self.tx_hash = tx_hash
        self.receipt = receipt
        super().__init__(f"Transaction {tx_hash} reverted")


class InsufficientFunds(ChainError):
    """Not enough balance for transaction."""

    pass


class NonceTooLow(ChainError):
    """Nonce already used."""

    pass


class ReplacementUnderpriced(ChainError):
    """Replacement transaction gas too low."""

    pass
