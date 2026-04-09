class AMMError(Exception):
    """Base class for all AMM-related exceptions."""

    pass


class FeeError(AMMError):
    """Raised when fee is set incorrectly (>= 10000 bps or < 0)."""

    def __init__(self, message="Fee must be less than 10000 bps (100%)"):
        super().__init__(message)


class InsufficientLiquidityError(AMMError):
    """Raised when the pool lacks sufficient reserves to complete the operation."""

    def __init__(self, message="Not enough liquidity in the pool for this trade"):
        super().__init__(message)


class AmountError(AMMError):
    """Raised when an invalid amount (e.g., zero or negative) is provided."""

    def __init__(self, message="Amount must be greater than 0"):
        super().__init__(message)


class InvalidTokenError(AMMError):
    """Raised when a specified token is not part of the active pool."""

    def __init__(self, message="The provided token is not part of this pair"):
        super().__init__(message)
