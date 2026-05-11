import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Any, Callable
from web3 import Web3
from web3.exceptions import TransactionNotFound

from src.core.types import (
    Address,
    Token,
    TokenAmount,
    TransactionRequest,
    TransactionReceipt,
)
from src.chain.errors import (
    RPCError,
    ChainError,
    TransactionFailed,
    InsufficientFunds,
    NonceTooLow,
    ReplacementUnderpriced,
)
from src.config.rpc_router import RpcRouter

logger = logging.getLogger("ChainClient")

_RPC_ERROR_MAP = [
    ("insufficient funds", InsufficientFunds),
    ("nonce too low", NonceTooLow),
    ("replacement transaction underpriced", ReplacementUnderpriced),
    ("transaction underpriced", ReplacementUnderpriced),
    ("already known", ReplacementUnderpriced),
    ("execution reverted", ChainError),
]

_ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]

_SIMULATION_BALANCE = Decimal("20000")


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
        simulation_mode: bool = False,
    ):
        if not rpc_urls:
            raise ValueError("Must provide at least one RPC URL")

        self.rpc_urls = rpc_urls
        self.native_symbol = native_symbol
        self.timeout = timeout
        self.max_retries = max_retries
        self.simulation_mode = simulation_mode

        # Initialize RPC router with HTTP and optional WSS endpoints
        from src.config.config import config as app_config

        wss_endpoints = (
            app_config.WSS_RPC_ENDPOINTS
            if hasattr(app_config, "WSS_RPC_ENDPOINTS")
            else None
        )
        self.router = RpcRouter(http_endpoints=rpc_urls, wss_endpoints=wss_endpoints)

        # Create Web3 once and reuse it (avoid eth_chainId on every rotation)
        self.w3 = self._create_w3(self.router.current_http)
        logger.info(f"✅ ChainClient initialized with {len(rpc_urls)} RPC endpoints")

    def _create_w3(self, url: str) -> Web3:
        """Create Web3 instance with HTTP provider."""
        provider = Web3.HTTPProvider(url, request_kwargs={"timeout": self.timeout})
        return Web3(provider)

    def _rotate_rpc(self, reason: str = "error"):
        """
        Rotate to next RPC endpoint WITHOUT recreating Web3.

        This preserves the w3 object and only changes the underlying provider,
        avoiding the expensive eth_chainId call that happens during __init__.
        """
        # RpcRouter.rotate() returns dict with http, wss, index
        result = self.router.rotate(reason=reason, endpoint_type="both")
        new_http_url = result.get("http")

        if new_http_url:
            # Replace provider in-place instead of recreating w3
            new_provider = Web3.HTTPProvider(
                new_http_url, request_kwargs={"timeout": self.timeout}
            )
            self.w3.provider = new_provider
            logger.warning(
                f"🔄 HTTP RPC rotated to index {result.get('index')}: {new_http_url[:60]}..."
            )
            return new_http_url

        return None

    def _execute(self, func: Callable[[], Any], name: str) -> Any:
        """
        Execute function with automatic retry and RPC rotation on 429/timeout.

        Uses RpcRouter.on_error() for intelligent error handling.
        """
        delay = 1.0
        for attempt in range(self.max_retries):
            try:
                result = func()
                self.router.reset_error_count()  # Success: reset error counter
                return result
            except Exception as e:
                # Don't retry on application errors
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

                # Check if error requires rotation (router.on_error returns dict or None)
                result = self.router.on_error(e, endpoint_type="http")
                if result and result.get("http"):
                    # Rotate provider in-place
                    new_provider = Web3.HTTPProvider(
                        result["http"], request_kwargs={"timeout": self.timeout}
                    )
                    self.w3.provider = new_provider
                    time.sleep(delay)
                    delay = min(delay * 2, 10.0)  # Cap backoff at 10s
                    continue

                # Last attempt or non-recoverable error
                if attempt == self.max_retries - 1:
                    raise RPCError(
                        f"Action {name} failed after {self.max_retries} attempts: {e}"
                    )

                # Recoverable error but no rotation needed
                time.sleep(delay)
                delay = min(delay * 2, 10.0)

    def _erc20_contract(self, token: Token):
        """Build a minimal ERC20 contract instance for balance/allowance calls."""
        return self.w3.eth.contract(address=token.address.checksum, abi=_ERC20_ABI)

    def get_balance(
        self,
        address: Address,
        block_identifier: str | Token = "latest",
        *args,
        **kwargs,
    ) -> TokenAmount:
        """
        Returns native balance or ERC20 balance when a Token is provided as block_identifier.
        ERC20 balances are mocked when simulation_mode=True to avoid false negatives.
        """
        token = None
        if isinstance(block_identifier, Token):
            token = block_identifier
        elif hasattr(block_identifier, "address") and hasattr(
            block_identifier, "decimals"
        ):
            token = block_identifier

        def _request():
            if token is not None:
                if self.simulation_mode:
                    return TokenAmount.from_human(
                        _SIMULATION_BALANCE,
                        token.decimals,
                        getattr(token, "symbol", "ERC20"),
                    )

                contract = self._erc20_contract(token)
                raw = contract.functions.balanceOf(address.checksum).call()
                return TokenAmount(
                    raw=int(raw),
                    decimals=token.decimals,
                    symbol=getattr(token, "symbol", "ERC20"),
                )

            raw_wei = self.w3.eth.get_balance(address.checksum, block_identifier)
            return TokenAmount(raw=raw_wei, decimals=18, symbol=self.native_symbol)

        return self._execute(_request, "get_balance")

    def get_allowance(
        self,
        owner: Address,
        spender: Address,
        token: Token,
        block_identifier: str = "latest",
    ) -> TokenAmount:
        """Returns ERC20 allowance; mocked when simulation_mode=True."""

        def _request():
            if self.simulation_mode:
                return TokenAmount.from_human(
                    _SIMULATION_BALANCE,
                    token.decimals,
                    token.symbol,
                )

            contract = self._erc20_contract(token)
            raw = contract.functions.allowance(owner.checksum, spender.checksum).call(
                block_identifier=block_identifier
            )
            return TokenAmount(
                raw=int(raw), decimals=token.decimals, symbol=token.symbol
            )

        return self._execute(_request, "get_allowance")

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

    def estimate_gas_cost_usd(
        self,
        gas_units: int,
        eth_price_usd: Decimal,
        buffer_bps: Decimal = Decimal("20"),
    ) -> Decimal:
        """Estimate gas cost in USD using current gas price and an optional buffer."""
        if gas_units <= 0:
            raise ValueError("gas_units must be positive")

        gas_price = self.get_gas_price().get_max_fee(priority="medium")
        gas_cost_wei = gas_units * gas_price
        gas_cost_eth = Decimal(gas_cost_wei) / Decimal("1000000000000000000")
        buffer_multiplier = (Decimal("10000") + buffer_bps) / Decimal("10000")
        return gas_cost_eth * eth_price_usd * buffer_multiplier

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
