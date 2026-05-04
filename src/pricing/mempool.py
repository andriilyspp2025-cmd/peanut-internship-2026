import asyncio
import logging
import ssl
import sys
from dataclasses import dataclass
from typing import Callable
from web3 import AsyncWeb3, AsyncHTTPProvider, WebSocketProvider
from web3.exceptions import TransactionNotFound

from src.chain.analyzer import decode_function

UNISWAP_V2_ROUTER = "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D".lower()
V3_SWAP_EVENT_TOPIC = (
    "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
)

log = logging.getLogger(__name__)


@dataclass
class ParsedSwap:
    tx_hash: str
    router: str
    dex: str
    method: str
    token_in: str | None
    token_out: str | None
    amount_in: int
    min_amount_out: int
    deadline: int
    sender: str
    gas_price: int
    expected_amount_out: int | None = None

    @property
    def slippage_tolerance(self):
        from decimal import Decimal

        if self.expected_amount_out is None:
            raise ValueError("expected_amount_out is not set")
        if self.expected_amount_out == 0:
            return Decimal("0")
        return Decimal(self.expected_amount_out - self.min_amount_out) / Decimal(
            self.expected_amount_out
        )


class MempoolMonitor:
    def __init__(
        self,
        wss_url: str | None = None,
        http_url: str | None = None,
        callback: Callable[[ParsedSwap], None] = None,
        router_address: str = UNISWAP_V2_ROUTER,
    ):
        self.wss_url = wss_url
        self.http_url = http_url
        self.callback = callback
        self._last_block_number: int | None = None
        self.router_address = router_address
        self.w3: AsyncWeb3 | None = None
        self._http_w3: AsyncWeb3 | None = None

        self._ssl_context = ssl.create_default_context()
        self._ssl_context.check_hostname = False
        self._ssl_context.verify_mode = ssl.CERT_NONE

        self._set_windows_selector_policy()

    def _set_windows_selector_policy(self) -> None:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    async def _ensure_w3(self) -> None:
        if self.w3 is None:
            if not self.wss_url:
                raise RuntimeError("WSS provider is not configured.")

            self.w3 = AsyncWeb3(
                WebSocketProvider(
                    self.wss_url,
                    websocket_kwargs={
                        "ping_interval": 20,
                        "ping_timeout": 20,
                        "max_size": 2**25,
                        "ssl": self._ssl_context,
                    },
                )
            )

        if hasattr(self.w3.provider, "connect"):
            await self.w3.provider.connect()

        if not await self.w3.is_connected():
            raise ConnectionError(
                f"Failed to connect to WSS provider: {self.w3.provider.endpoint_uri}"
            )

    async def connect(self) -> None:
        """Public helper for explicit WSS initialization."""
        await self._ensure_w3()

    async def _connect_http(self) -> None:
        if not self.http_url:
            raise RuntimeError("HTTP endpoint is not configured for fallback polling.")

        self._http_w3 = AsyncWeb3(AsyncHTTPProvider(self.http_url))

    async def start_listening(self):
        """Connects to WSS and streams pending transactions, falling back to HTTP polling."""
        if self.wss_url:
            try:
                await self._ensure_w3()
                log.info("Connected to WSS. Listening to mempool...")
                await self.w3.eth.subscribe("pending_transactions")

                async for message in self.w3.socket.process_subscriptions():
                    result = message.get("result") or message.get("params", {}).get(
                        "result"
                    )
                    if result is None:
                        continue

                    if isinstance(result, bytes):
                        tx_hash = "0x" + result.hex()
                    else:
                        tx_hash = str(result)

                    asyncio.create_task(self._process_transaction(tx_hash))

                return
            except Exception as exc:
                log.warning(
                    "WSS mempool subscription failed (%s). Falling back to HTTP polling.",
                    exc,
                )

        if self.http_url:
            await self._start_http_polling()
            return

        raise RuntimeError(
            "No mempool transport available: configure either WSS or HTTP fallback."
        )

    async def _process_transaction(self, tx_hash):
        """Fetches and parses a single transaction."""
        if self.w3 is None:
            return

        try:
            tx = await self.w3.eth.get_transaction(tx_hash)
            parsed_swap = self.parse_transaction(dict(tx))
            if parsed_swap and self.callback:
                if asyncio.iscoroutinefunction(self.callback):
                    asyncio.create_task(self.callback(parsed_swap))
                else:
                    self.callback(parsed_swap)
        except TransactionNotFound:
            pass
        except Exception as e:
            log.debug(f"Error processing tx {tx_hash}: {e}")

    async def _start_http_polling(self, interval: float = 2.0):
        await self._connect_http()
        if self._http_w3 is None:
            raise RuntimeError("HTTP polling client not available.")

        log.info("Starting HTTP polling fallback for mempool events...")

        while True:
            try:
                block = await self._http_w3.eth.get_block(
                    "latest", full_transactions=True
                )
                block_number = (
                    block["number"]
                    if isinstance(block, dict)
                    else getattr(block, "number", None)
                )
                if block_number is None:
                    raise ValueError("Unable to read block number from HTTP response")

                if self._last_block_number is None:
                    self._last_block_number = block_number
                elif block_number > self._last_block_number:
                    transactions = (
                        block["transactions"]
                        if isinstance(block, dict)
                        else getattr(block, "transactions", [])
                    )
                    for tx in transactions:
                        tx_dict = dict(tx) if not isinstance(tx, dict) else tx
                        parsed_swap = self.parse_transaction(tx_dict)
                        if parsed_swap and self.callback:
                            if asyncio.iscoroutinefunction(self.callback):
                                asyncio.create_task(self.callback(parsed_swap))
                            else:
                                self.callback(parsed_swap)
                    self._last_block_number = block_number
            except Exception as e:
                log.warning("HTTP polling error: %s", e)
            await asyncio.sleep(interval)

    def parse_transaction(self, tx: dict) -> ParsedSwap | None:
        """Extracts swap details from a raw transaction dictionary."""
        if not tx.get("to") or tx["to"].lower() != UNISWAP_V2_ROUTER:
            return None

        raw_input = tx.get("input", b"")
        if not raw_input:
            return None

        if isinstance(raw_input, bytes):
            input_data = "0x" + raw_input.hex()
        else:
            input_data = str(raw_input)

        if len(input_data) < 10:
            return None

        selector = input_data[:10].lower()
        calldata = input_data[10:]

        try:
            return self.decode_swap_params(selector, bytes.fromhex(calldata), tx)
        except Exception as exc:
            log.debug(f"decode_swap_params failed for {selector}: {exc}")
            return None

    def decode_swap_params(
        self, selector: str, data: bytes, tx: dict
    ) -> dict | None | ParsedSwap:
        """Decodes swap parameters from transaction calldata."""
        input_data = (
            "0x" + tx.get("input", b"").hex()
            if isinstance(tx.get("input"), bytes)
            else str(tx.get("input"))
        )
        try:
            decoded = decode_function(input_data)
        except Exception:
            return None

        func_name = decoded.get("name", "")
        if "swap" not in func_name.lower():
            return None

        args_dict = {arg["name"]: arg["value"] for arg in decoded.get("args", [])}

        amount_in = (
            tx.get("value", 0)
            if "swapExactETHForTokens" in func_name
            else args_dict.get("amountIn", args_dict.get("amountInMax", 0))
        )
        min_amount_out = args_dict.get("amountOutMin", args_dict.get("amountOut", 0))
        path = args_dict.get("path", [])
        deadline = args_dict.get("deadline", 0)

        gas_price = tx.get("gasPrice") or tx.get("maxFeePerGas") or 0
        sender = tx.get("from", "Unknown")
        tx_hash = tx.get("hash", "")
        if isinstance(tx_hash, bytes):
            tx_hash = "0x" + tx_hash.hex()

        return ParsedSwap(
            tx_hash=str(tx_hash),
            router=tx.get("to", ""),
            dex="UniswapV2",
            method=func_name,
            token_in=(
                None if "ethfor" in func_name.lower() else (path[0] if path else None)
            ),
            token_out=(
                None if "foreth" in func_name.lower() else (path[-1] if path else None)
            ),
            amount_in=amount_in,
            min_amount_out=min_amount_out,
            deadline=deadline,
            sender=sender,
            gas_price=gas_price,
        )

    async def start_price_feed(self, pool_addresses: list[str]):
        """Listens for Uniswap V3 Swap events on specified pools."""
        try:
            await self._ensure_w3()
        except Exception as e:
            raise Exception(f"REAL WSS ERROR: {type(e).__name__} - {str(e)}")

        log.info(f"Subscribing to V3 Swap events for {len(pool_addresses)} pools...")

        formatted_addresses = [addr.lower() for addr in pool_addresses]

        try:
            await self.w3.eth.subscribe(
                "logs",
                {
                    "address": formatted_addresses,
                    "topics": [V3_SWAP_EVENT_TOPIC],
                },
            )
        except Exception as e:
            log.error(f"Failed to subscribe to V3 logs: {e}")
            return

        async for message in self.w3.socket.process_subscriptions():
            try:
                result = message.get("result") or message.get("params", {}).get(
                    "result"
                )
                if not result:
                    continue

                pool_address = result.get("address", "Unknown")
                log.info(f"V3 swap detected at pool {pool_address}. Triggering tick.")

                if self.callback:
                    if asyncio.iscoroutinefunction(self.callback):
                        asyncio.create_task(self.callback(pool_address))
                    else:
                        self.callback(pool_address)
            except Exception as e:
                log.debug(f"Error processing V3 event: {e}")
