import logging
from typing import Optional
from decimal import Decimal
from eth_account.datastructures import SignedTransaction

from src.core.types import Address, TokenAmount, TransactionRequest, TransactionReceipt
from src.chain.client import ChainClient
from src.core.wallet import WalletManager

log = logging.getLogger(__name__)


class TransactionBuilder:
    """
    Fluent builder for transactions.

    Usage:
        tx = (TransactionBuilder(client, wallet)
            .to(recipient)
            .value(TokenAmount.from_human("0.1", 18))
            .data(b"")
            .with_gas_estimate()
            .with_gas_price("high")
            .build())
    """

    def __init__(self, client: ChainClient, wallet: WalletManager):
        self.client = client
        self.wallet = wallet

        # Required
        self._to: Optional[Address] = None
        self._value: Optional[TokenAmount] = None
        self._data: Optional[bytes] = None

        # Optional
        self._nonce: Optional[int] = None
        self._gas_limit: Optional[int] = None
        self._max_fee_per_gas: Optional[int] = None
        self._max_priority_fee: Optional[int] = None

        self._chain_id: int = 1

    def to(self, address: Address) -> "TransactionBuilder":
        if not isinstance(address, Address):
            raise TypeError("address must be an Address instance.")
        self._to = address
        return self

    def value(self, amount: TokenAmount) -> "TransactionBuilder":
        if not isinstance(amount, TokenAmount):
            raise TypeError("amount must be a TokenAmount instance.")
        self._value = amount
        return self

    def data(self, calldata: bytes) -> "TransactionBuilder":
        if not isinstance(calldata, bytes):
            raise TypeError("calldata must be bytes.")
        self._data = calldata
        return self

    def nonce(self, nonce: int) -> "TransactionBuilder":
        """Explicit nonce (for replacement or batch)."""
        if nonce < 0:
            raise ValueError("nonce must be non-negative.")
        self._nonce = nonce
        return self

    def gas_limit(self, limit: int) -> "TransactionBuilder":
        if limit <= 0:
            raise ValueError("gas_limit must be strictly positive.")
        self._gas_limit = limit
        return self

    def with_gas_estimate(
        self, buffer: Decimal = Decimal("1.2")
    ) -> "TransactionBuilder":
        """Estimate gas and set limit with buffer."""
        self._validate_core_fields("estimate gas")

        temp_tx = TransactionRequest(
            to=self._to,
            value=self._value,
            data=self._data,
            nonce=self._nonce if self._nonce is not None else 0,
            chain_id=self._chain_id,
        )

        estimated_gas = self.client.estimate_gas(temp_tx)
        self._gas_limit = int(estimated_gas * buffer)
        log.debug(f"Estimated gas: {estimated_gas}, buffered limit: {self._gas_limit}")
        return self

    def with_gas_price(self, priority: str = "medium") -> "TransactionBuilder":
        """Set gas price based on current network conditions."""
        gas_price_info = self.client.get_gas_price()

        self._max_fee_per_gas = gas_price_info.get_max_fee(priority)

        if priority == "low":
            self._max_priority_fee = gas_price_info.priority_fee_low
        elif priority == "high":
            self._max_priority_fee = gas_price_info.priority_fee_high
        else:
            self._max_priority_fee = gas_price_info.priority_fee_medium

        log.debug(
            f"Set gas price: max_fee={self._max_fee_per_gas}, priority={self._max_priority_fee}"
        )
        return self

    def _validate_core_fields(self, action: str) -> None:
        """Internal helper to ensure required fields are present."""
        missing = [
            name
            for name, val in [
                ("to", self._to),
                ("value", self._value),
                ("data", self._data),
            ]
            if val is None
        ]
        if missing:
            raise ValueError(
                f"Cannot {action}. Missing required fields: {', '.join(missing)}"
            )

    def build(self) -> TransactionRequest:
        """Validate and return transaction request."""
        self._validate_core_fields("build transaction")

        # Auto-fill missing dynamics
        if self._gas_limit is None:
            self.with_gas_estimate()

        if self._max_fee_per_gas is None or self._max_priority_fee is None:
            self.with_gas_price("medium")

        final_nonce = (
            self._nonce
            if self._nonce is not None
            else self.client.get_nonce(self.wallet.address)
        )

        return TransactionRequest(
            to=self._to,
            value=self._value,
            data=self._data,
            nonce=final_nonce,
            gas_limit=self._gas_limit,
            max_fee_per_gas=self._max_fee_per_gas,
            max_priority_fee=self._max_priority_fee,
            chain_id=self._chain_id,
        )

    def build_and_sign(self) -> SignedTransaction:
        """Build, sign, and return ready-to-send transaction."""
        tx_request = self.build()
        log.debug(
            f"Signing transaction to {tx_request.to.checksum} with nonce {tx_request.nonce}"
        )
        return self.wallet.sign_transaction(tx_request.to_dict())

    def send(self) -> str:
        """Build, sign, send, return tx hash."""
        signed_tx = self.build_and_sign()

        raw_bytes = (
            signed_tx.raw_transaction
            if hasattr(signed_tx, "raw_transaction")
            else signed_tx.rawTransaction
        )

        tx_hash = self.client.send_transaction(raw_bytes)
        log.info(f"Broadcasted transaction: {tx_hash}")
        return tx_hash

    def send_and_wait(self, timeout: int = 120) -> TransactionReceipt:
        """Build, sign, send, wait for confirmation."""
        tx_hash = self.send()
        log.info(f"Waiting up to {timeout}s for confirmation of {tx_hash}...")
        return self.client.wait_for_receipt(tx_hash, timeout=timeout)
