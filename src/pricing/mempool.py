import asyncio
import logging
from dataclasses import dataclass
from typing import Callable
from web3 import AsyncWeb3, WebSocketProvider
from web3.exceptions import TransactionNotFound

from src.chain.analyzer import decode_function

UNISWAP_V2_ROUTER = "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D".lower()

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
        wss_url: str,
        callback: Callable[[ParsedSwap], None] = None,
        router_address: str = UNISWAP_V2_ROUTER,
    ):
        self.w3 = AsyncWeb3(WebSocketProvider(wss_url))
        self.callback = callback

    async def start_listening(self):
        """Connects to WSS and streams pending transactions."""
        if not await self.w3.is_connected():
            raise ConnectionError("Failed to connect to WSS provider")

        log.info("Connected to WSS. Listening to mempool...")
        subscription = await self.w3.eth.subscribe("pending_transactions")

        async for message in self.w3.socket.process_subscriptions():
            result = message.get("result") or message.get("params", {}).get("result")

            if result is None:
                continue

            if isinstance(result, bytes):
                tx_hash = "0x" + result.hex()
            else:
                tx_hash = str(result)

            asyncio.create_task(self._process_transaction(tx_hash))

    async def _process_transaction(self, tx_hash):
        """Fetches and parses a single transaction."""
        try:
            tx = await self.w3.eth.get_transaction(tx_hash)
            parsed_swap = self.parse_transaction(dict(tx))
            if parsed_swap and self.callback:
                self.callback(parsed_swap)
        except TransactionNotFound:
            pass
        except Exception as e:
            log.debug(f"Error processing tx {tx_hash}: {e}")

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
        """Listens for Sync events on specified pools to track reserve updates in real-time."""
        if not await self.w3.is_connected():
            raise ConnectionError("Failed to connect to WSS provider")

        log.info(f"Subscribing to Sync events for {len(pool_addresses)} pools...")

        try:
            subscription = await self.w3.eth.subscribe(
                "logs",
                {
                    "address": pool_addresses,
                    "topics": [
                        "0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1"
                    ],
                },
            )
        except Exception as e:
            log.error(f"Failed to subscribe to logs: {e}")
            return

        async for message in self.w3.socket.process_subscriptions():
            try:
                result = message.get("result") or message.get("params", {}).get(
                    "result"
                )
                if not result:
                    continue

                pool_address = result.get("address", "Unknown")
                data = result.get("data", "")

                if data.startswith("0x"):
                    data = data[2:]

                if len(data) >= 128:
                    reserve0_hex = data[:64]
                    reserve1_hex = data[64:128]

                    reserve0 = int(reserve0_hex, 16)
                    reserve1 = int(reserve1_hex, 16)

                    log.info(
                        f"PRICE FEED UPDATE: Pool {pool_address} reserves changed - reserve0: {reserve0}, reserve1: {reserve1}"
                    )
            except Exception as e:
                log.debug(f"Error processing price feed event: {e}")
