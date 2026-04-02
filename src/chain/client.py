import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Any, Callable
from web3 import Web3
from web3.exceptions import TransactionNotFound

from src.core.types import Address, TokenAmount, TransactionRequest, TransactionReceipt
from src.chain.errors import (
    RPCError,
    ChainError,
    TransactionFailed,
    InsufficientFunds,
    NonceTooLow,
    ReplacementUnderpriced,
)


_RPC_ERROR_MAP = [
    ("insufficient funds", InsufficientFunds),
    ("nonce too low", NonceTooLow),
    ("replacement transaction underpriced", ReplacementUnderpriced),
    ("transaction underpriced", ReplacementUnderpriced),
    ("already known", ReplacementUnderpriced),
    ("execution reverted", ChainError),
]


def _parse_rpc_error(exc: Exception) -> Exception:
    error_msg = str(exc).lower()
    for pattern, err_class in _RPC_ERROR_MAP:
        if pattern in error_msg:
            return err_class(f"RPC Error ({err_class.__name__}): {exc}")
    return exc


@dataclass
class GasPrice:
    """Current gas price information."""

    base_fee: int
    priority_fee_low: int
    priority_fee_medium: int
    priority_fee_high: int

    def get_max_fee(
        self, priority: str = "medium", buffer: Decimal = Decimal("1.2")
    ) -> int:
        """Calculate maxFeePerGas with buffer for base fee increase."""
        if priority == "low":
            priority_fee = self.priority_fee_low
        elif priority == "high":
            priority_fee = self.priority_fee_high
        else:
            priority_fee = self.priority_fee_medium

        return int(self.base_fee * buffer) + priority_fee


class ChainClient:
    """
    Ethereum RPC client with reliability features.

    Features:
    - Automatic retry with exponential backoff
    - Multiple RPC endpoint fallback (Persistent Rotation)
    - Request timing/logging
    - Proper error classification
    """

    def __init__(
        self,
        rpc_urls: list[str],
        native_symbol: str = "ETH",
        timeout: int = 30,
        max_retries: int = 3,
    ):
        if not rpc_urls:
            raise ValueError("Must provide at least one RPC URL")

        self.rpc_urls = rpc_urls
        self.native_symbol = native_symbol
        self.timeout = timeout
        self.max_retries = max_retries

        self._current_rpc_index = 0
        self.w3 = self._connect(self.rpc_urls[self._current_rpc_index])

    def _connect(self, url: str) -> Web3:
        provider = Web3.HTTPProvider(url, request_kwargs={"timeout": self.timeout})
        return Web3(provider)

    def _rotate_rpc(self):
        """Твоя перевага: швидке і перманентне перемикання нод."""
        self._current_rpc_index = (self._current_rpc_index + 1) % len(self.rpc_urls)
        new_url = self.rpc_urls[self._current_rpc_index]
        self.w3 = self._connect(new_url)

    def _execute(self, func: Callable[[], Any], name: str) -> Any:
        """Твоя перевага: чиста ізоляція мережевої логіки."""
        delay = 1.0
        for attempt in range(self.max_retries):
            try:
                return func()
            except Exception as e:
                if isinstance(
                    e,
                    (
                        ChainError,
                        InsufficientFunds,
                        NonceTooLow,
                        ReplacementUnderpriced,
                    ),
                ):
                    raise e

                if attempt == self.max_retries - 1:
                    raise RPCError(
                        f"Action {name} failed after {self.max_retries} attempts: {e}"
                    )

                error_msg = str(e).lower()
                if "429" in error_msg or "timeout" in error_msg:
                    self._rotate_rpc()
                    time.sleep(delay)
                    delay *= 2
                else:
                    raise e

    def get_balance(self, address: Address) -> TokenAmount:
        def _request():
            raw_wei = self.w3.eth.get_balance(address.checksum)
            return TokenAmount(raw=raw_wei, decimals=18, symbol=self.native_symbol)

        return self._execute(_request, "get_balance")

    def get_nonce(self, address: Address, block: str = "pending") -> int:
        def _request():
            return self.w3.eth.get_transaction_count(address.checksum, block)

        return self._execute(_request, "get_nonce")

    def get_gas_price(self) -> GasPrice:
        def _request():
            try:
                # Основний план: EIP-1559
                history = self.w3.eth.fee_history(4, "latest", [25.0, 50.0, 75.0])
                base_fee = history.get("baseFeePerGas", [0])[-1]

                rewards = history.get("reward", [])
                if not rewards:
                    fallback_priority = self.w3.eth.max_priority_fee
                    return GasPrice(
                        base_fee,
                        fallback_priority,
                        fallback_priority,
                        fallback_priority,
                    )

                low = sum(r[0] for r in rewards) // len(rewards)
                med = sum(r[1] for r in rewards) // len(rewards)
                high = sum(r[2] for r in rewards) // len(rewards)

                return GasPrice(
                    base_fee=base_fee,
                    priority_fee_low=low,
                    priority_fee_medium=med,
                    priority_fee_high=high,
                )
            except Exception:
                # Резервний план: fallback для нод без підтримки fee_history
                legacy_gas_price = self.w3.eth.gas_price
                return GasPrice(
                    base_fee=legacy_gas_price,
                    priority_fee_low=0,
                    priority_fee_medium=0,
                    priority_fee_high=0,
                )

        return self._execute(_request, "get_gas_price")

    def estimate_gas(self, tx: TransactionRequest) -> int:
        def _request():
            try:
                return self.w3.eth.estimate_gas(tx.to_dict())
            except Exception as e:
                raise _parse_rpc_error(e)

        return self._execute(_request, "estimate_gas")

    def send_transaction(self, signed_tx: bytes) -> str:
        def _request():
            try:
                tx_hash = self.w3.eth.send_raw_transaction(signed_tx)
                return tx_hash.hex()
            except Exception as e:
                raise _parse_rpc_error(e)

        return self._execute(_request, "send_transaction")

    def get_transaction(self, tx_hash: str) -> dict:
        def _request():
            return dict(self.w3.eth.get_transaction(tx_hash))

        return self._execute(_request, f"get_transaction({tx_hash})")

    def get_receipt(self, tx_hash: str) -> Optional[TransactionReceipt]:
        def _request():
            try:
                receipt = self.w3.eth.get_transaction_receipt(tx_hash)
                if receipt:
                    return TransactionReceipt.from_web3(dict(receipt))
                return None
            except TransactionNotFound:
                return None
            except Exception as e:
                if "not found" in str(e).lower():
                    return None
                raise e

        return self._execute(_request, f"get_receipt({tx_hash})")

    def wait_for_receipt(
        self, tx_hash: str, timeout: int = 120, poll_interval: float = 1.0
    ) -> TransactionReceipt:
        start_time = time.time()
        while time.time() - start_time < timeout:
            receipt = self.get_receipt(tx_hash)
            if receipt is not None:
                if not receipt.status:
                    raise TransactionFailed(tx_hash, receipt)
                return receipt
            time.sleep(poll_interval)
        raise ChainError(
            f"Timeout waiting for transaction {tx_hash} after {timeout} seconds."
        )

    def call(self, tx: TransactionRequest, block: str = "latest") -> bytes:
        def _request():
            try:
                return self.w3.eth.call(tx.to_dict(), block_identifier=block)
            except Exception as e:
                raise _parse_rpc_error(e)

        return self._execute(_request, "call")
