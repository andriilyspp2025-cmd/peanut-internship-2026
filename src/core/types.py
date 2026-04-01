from dataclasses import dataclass
from typing import Optional
from decimal import Decimal
from eth_utils import to_checksum_address, is_address


@dataclass(frozen=True)
class Address:
    """Ethereum address with validation and checksumming."""

    value: str

    def __post_init__(self):
        # Validate and convert to checksum
        if not is_address(self.value):
            raise ValueError(f"Invalid address: {self.value}")
        object.__setattr__(self, "value", to_checksum_address(self.value))

    @classmethod
    def from_string(cls, s: str) -> "Address":
        return cls(value=s)

    @property
    def checksum(self) -> str:
        return self.value

    @property
    def lower(self) -> str:
        return self.value.lower()

    def __eq__(self, other) -> bool:
        if not isinstance(other, Address):
            return NotImplemented
        return self.lower == other.lower


@dataclass(frozen=True)
class TokenAmount:
    """
    Represents a token amount with proper decimal handling.

    Internally stores raw integer (wei-equivalent).
    Provides human-readable formatting.
    """

    raw: int  # Raw amount (e.g., wei)
    decimals: int  # Token decimals (e.g., 18 for ETH, 6 for USDC)
    symbol: Optional[str] = None

    @classmethod
    def from_human(
        cls, amount: str | Decimal, decimals: int, symbol: str = None
    ) -> "TokenAmount":
        """Create from human-readable amount (e.g., '1.5' ETH)."""
        raw_amount = int(Decimal(str(amount)) * (Decimal(10) ** decimals))
        return cls(raw=raw_amount, decimals=decimals, symbol=symbol)

    @property
    def human(self) -> Decimal:
        """Returns human-readable decimal."""
        return Decimal(self.raw) / (Decimal(10) ** self.decimals)

    def __add__(self, other: "TokenAmount") -> "TokenAmount":
        # 1. Перевірка розмірності (decimals)
        if self.decimals != other.decimals:
            raise ValueError(f"Decimals mismatch: {self.decimals} != {other.decimals}")

        # 2. Розумна перевірка символів
        if self.symbol and other.symbol and self.symbol != other.symbol:
            raise ValueError(
                f"Cannot add different tokens: {self.symbol} and {other.symbol}"
            )

        # Визначаємо, який символ залишити новому об'єкту (якщо один з них None)
        resulting_symbol = self.symbol or other.symbol

        new_raw = self.raw + other.raw
        return TokenAmount(raw=new_raw, decimals=self.decimals, symbol=resulting_symbol)

    def __mul__(self, factor: int | Decimal) -> "TokenAmount":
        # Захищаємось від флоатів і примусово повертаємо int
        new_raw = int(Decimal(self.raw) * Decimal(str(factor)))
        return TokenAmount(raw=new_raw, decimals=self.decimals, symbol=self.symbol)

    def __str__(self) -> str:
        return f"{self.human} {self.symbol or ''}".strip()


@dataclass(frozen=True, eq=False)
class Token:
    """
    Represents an ERC-20 token with its on-chain metadata.

    Identity is by address only — two Token instances at the same address
    are equal regardless of symbol/decimals (those are metadata, not identity).
    We use eq=False to override the dataclass-generated __eq__ and define our own.

    This type will be used extensively from Week 2 onward (AMM math, routing, etc.).
    """

    address: Address
    symbol: str
    decimals: int

    def __eq__(self, other) -> bool:
        if isinstance(other, Token):
            return (
                self.address == other.address
            )  # Delegates to Address.__eq__ (case-insensitive)
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.address.lower)

    def __repr__(self) -> str:
        return f"Token({self.symbol},{self.address.checksum})"


@dataclass
class TransactionRequest:
    """A transaction ready to be signed."""

    to: Address
    value: TokenAmount
    data: bytes
    nonce: Optional[int] = None
    gas_limit: Optional[int] = None
    max_fee_per_gas: Optional[int] = None
    max_priority_fee: Optional[int] = None
    chain_id: int = 1

    def to_dict(self) -> dict:
        """Convert to web3-compatible dict."""
        tx = {
            "to": self.to.checksum,
            "value": self.value.raw,
            "data": self.data,
            "chainId": self.chain_id,
        }
        if self.nonce is not None:
            tx["nonce"] = self.nonce
        if self.gas_limit is not None:
            tx["gas"] = self.gas_limit
        if self.max_fee_per_gas is not None:
            tx["maxFeePerGas"] = self.max_fee_per_gas
        if self.max_priority_fee is not None:
            tx["maxPriorityFeePerGas"] = self.max_priority_fee
        return tx


@dataclass
class TransactionReceipt:
    """Parsed transaction receipt."""

    tx_hash: str
    block_number: int
    status: bool  # True = success
    gas_used: int
    effective_gas_price: int
    logs: list

    @property
    def tx_fee(self) -> TokenAmount:
        """Returns transaction fee as TokenAmount."""
        fee_wei = self.gas_used * self.effective_gas_price
        return TokenAmount(raw=fee_wei, decimals=18, symbol=None)

    @classmethod
    def from_web3(cls, receipt: dict) -> "TransactionReceipt":
        """Parse from web3 receipt dict."""
        raw_hash = receipt["transactionHash"]
        tx_hash_str = raw_hash.hex() if hasattr(raw_hash, "hex") else str(raw_hash)
        return cls(
            tx_hash=tx_hash_str,
            block_number=receipt["blockNumber"],
            status=bool(receipt["status"]),
            gas_used=receipt["gasUsed"],
            effective_gas_price=receipt.get("effectiveGasPrice", 0),
            logs=receipt.get("logs", []),
        )
