"""
chain/analyzer.py — CLI tool to analyze Ethereum transactions.
"""

import argparse
import json
import sys
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, Any

from web3 import Web3
import eth_abi

from src.chain.client import ChainClient

FUNCTIONS = {
    "0xa9059cbb": (
        "transfer(address,uint256)",
        ["address", "uint256"],
        ["to", "value"],
    ),
    "0x23b872dd": (
        "transferFrom(address,address,uint256)",
        ["address", "address", "uint256"],
        ["from", "to", "value"],
    ),
    "0x095ea7b3": (
        "approve(address,uint256)",
        ["address", "uint256"],
        ["spender", "value"],
    ),
    "0x38ed1739": (
        "swapExactTokensForTokens(uint256,uint256,address[],address,uint256)",
        ["uint256", "uint256", "address[]", "address", "uint256"],
        ["amountIn", "amountOutMin", "path", "to", "deadline"],
    ),
    "0x7ff36ab5": (
        "swapExactETHForTokens(uint256,address[],address,uint256)",
        ["uint256", "address[]", "address", "uint256"],
        ["amountOutMin", "path", "to", "deadline"],
    ),
    "0x18cbafe5": (
        "swapExactTokensForETH(uint256,uint256,address[],address,uint256)",
        ["uint256", "uint256", "address[]", "address", "uint256"],
        ["amountIn", "amountOutMin", "path", "to", "deadline"],
    ),
    "0xe8e33700": (
        "addLiquidity(address,address,uint256,uint256,uint256,uint256,address,uint256)",
        [
            "address",
            "address",
            "uint256",
            "uint256",
            "uint256",
            "uint256",
            "address",
            "uint256",
        ],
        [
            "tokenA",
            "tokenB",
            "amountADesired",
            "amountBDesired",
            "amountAMin",
            "amountBMin",
            "to",
            "deadline",
        ],
    ),
    "0xbaa2abde": (
        "removeLiquidity(address,address,uint256,uint256,uint256,address,uint256)",
        ["address", "address", "uint256", "uint256", "uint256", "address", "uint256"],
        ["tokenA", "tokenB", "liquidity", "amountAMin", "amountBMin", "to", "deadline"],
    ),
}

EVENT_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
EVENT_SWAP_V2 = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
EVENT_SYNC_V2 = "0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1"

TOKEN_CACHE: Dict[str, Dict[str, Any]] = {}


def _get_token_info(client: ChainClient, address: str) -> dict:
    addr_lower = address.lower()
    if addr_lower in TOKEN_CACHE:
        return TOKEN_CACHE[addr_lower]

    try:
        checksum_addr = Web3.to_checksum_address(address)
        symbol_data = client.w3.eth.call({"to": checksum_addr, "data": "0x95d89b41"})
        decimals_data = client.w3.eth.call({"to": checksum_addr, "data": "0x313ce567"})

        symbol = eth_abi.decode(["string"], symbol_data)[0] if symbol_data else "???"
        decimals = (
            int.from_bytes(decimals_data, byteorder="big") if decimals_data else 18
        )
        info = {"symbol": symbol, "decimals": decimals}
    except Exception:
        info = {"symbol": address[:6] + "…", "decimals": 18}

    TOKEN_CACHE[addr_lower] = info
    return info


def decode_function(input_data: str) -> dict:
    if len(input_data) < 10:
        return {"selector": None, "name": "ETH Transfer", "args": []}

    selector = input_data[:10]
    raw_args = bytes.fromhex(input_data[10:])
    known = FUNCTIONS.get(selector)

    if not known:
        return {
            "selector": selector,
            "name": "Unknown",
            "args": [{"name": "raw", "value": input_data[10:]}],
        }

    func_sig, types, names = known
    args_list = []
    try:
        decoded = eth_abi.decode(types, raw_args)
        for name, val in zip(names, decoded):
            if isinstance(val, bytes):
                val = "0x" + val.hex()
            elif isinstance(val, tuple) or isinstance(val, list):
                val = [("0x" + v.hex() if isinstance(v, bytes) else v) for v in val]
            args_list.append({"name": name, "value": val})
    except Exception as e:
        args_list.append({"name": "decode_error", "value": str(e)})

    return {"selector": selector, "name": func_sig, "args": args_list}


def analyze(tx_hash: str, rpc_url: str) -> dict:
    client = ChainClient(rpc_urls=[rpc_url], timeout=15)

    tx = client.get_transaction(tx_hash)
    receipt = client.get_receipt(tx_hash)

    if not tx:
        raise ValueError("Transaction not found.")

    analysis = {
        "transaction": {
            "hash": tx_hash,
            "from": tx.get("from"),
            "to": tx.get("to"),
            "value": tx.get("value", 0),
            "gas_limit": tx.get("gas", 0),
            "input": tx.get("input", "0x"),
        },
        "receipt": None,
        "function": decode_function(tx.get("input", "0x")),
        "events": {"transfers": [], "swaps": [], "syncs": []},
        "revert_reason": None,
    }

    if receipt is None:
        return analysis

    analysis["receipt"] = {
        "block_number": receipt.block_number,
        "status": receipt.status,
        "gas_used": receipt.gas_used,
        "effective_gas_price": receipt.effective_gas_price,
    }

    try:
        block = client.w3.eth.get_block(receipt.block_number)
        analysis["timestamp"] = block.get("timestamp")
    except Exception:
        pass

    if not receipt.status:
        try:
            client.w3.eth.call(
                {
                    "from": tx.get("from"),
                    "to": tx.get("to"),
                    "data": tx.get("input"),
                    "value": tx.get("value", 0),
                },
                receipt.block_number - 1,
            )
        except Exception as e:
            analysis["revert_reason"] = str(e)

    for log in getattr(receipt, "logs", []):
        topics = log.get("topics", [])
        if not topics:
            continue

        topic0 = "0x" + topics[0].hex() if isinstance(topics[0], bytes) else topics[0]

        if topic0 == EVENT_TRANSFER and len(topics) >= 3:
            token_addr = log.get("address")
            info = _get_token_info(client, token_addr)
            from_addr = Web3.to_checksum_address(
                "0x"
                + (
                    topics[1].hex()[-40:]
                    if hasattr(topics[1], "hex")
                    else topics[1][-40:]
                )
            )
            to_addr = Web3.to_checksum_address(
                "0x"
                + (
                    topics[2].hex()[-40:]
                    if hasattr(topics[2], "hex")
                    else topics[2][-40:]
                )
            )
            raw_data = log.get("data", b"")
            amount_raw = int.from_bytes(
                bytes.fromhex(
                    raw_data[2:] if isinstance(raw_data, str) else raw_data.hex()
                ),
                "big",
            )
            amount_human = Decimal(amount_raw) / Decimal(10 ** info["decimals"])

            analysis["events"]["transfers"].append(
                {
                    "symbol": info["symbol"],
                    "from": from_addr,
                    "to": to_addr,
                    "amount": amount_human,
                }
            )

        elif topic0 == EVENT_SWAP_V2:
            raw_data = log.get("data", b"")
            data_bytes = bytes.fromhex(
                raw_data[2:] if isinstance(raw_data, str) else raw_data.hex()
            )
            if len(data_bytes) >= 128:
                amounts = [
                    int.from_bytes(data_bytes[i : i + 32], "big")
                    for i in range(0, 128, 32)
                ]
                analysis["events"]["swaps"].append(
                    {
                        "pair": log.get("address"),
                        "amount0In": amounts[0],
                        "amount1In": amounts[1],
                        "amount0Out": amounts[2],
                        "amount1Out": amounts[3],
                    }
                )

        elif topic0 == EVENT_SYNC_V2:
            raw_data = log.get("data", b"")
            data_bytes = bytes.fromhex(
                raw_data[2:] if isinstance(raw_data, str) else raw_data.hex()
            )
            if len(data_bytes) >= 64:
                analysis["events"]["syncs"].append(
                    {
                        "pair": log.get("address"),
                        "reserve0": int.from_bytes(data_bytes[:32], "big"),
                        "reserve1": int.from_bytes(data_bytes[32:64], "big"),
                    }
                )

    return analysis


def format_report(data: dict) -> str:
    tx = data["transaction"]
    rec = data["receipt"]

    lines = ["\nTransaction Analysis", "=" * 20]
    lines.append(f"Hash:           {tx['hash']}")

    if not rec:
        lines.append("Status:         PENDING (In Mempool)")
        return "\n".join(lines)

    status = "SUCCESS" if rec["status"] else "FAILED / REVERTED"
    lines.append(f"Block:          {rec['block_number']}")

    if "timestamp" in data:
        dt = datetime.fromtimestamp(data["timestamp"], tz=timezone.utc)
        lines.append(f"Timestamp:      {dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")

    lines.append(f"Status:         {status}")
    lines.append(f"\nFrom:           {tx['from']}")
    lines.append(f"To:             {tx['to'] or 'Contract Creation'}")
    lines.append(f"Value:          {Decimal(tx['value']) / Decimal(10**18):.6f} ETH")

    gas_limit = tx["gas_limit"]
    gas_used = rec["gas_used"]
    pct = (gas_used / gas_limit * 100) if gas_limit else 0
    eff_price = rec["effective_gas_price"]
    fee_eth = Decimal(gas_used * eff_price) / Decimal(10**18)

    lines.extend(
        [
            "\nGas Analysis",
            "-" * 12,
            f"Gas Limit:      {gas_limit:,}",
            f"Gas Used:       {gas_used:,} ({pct:.2f}%)",
            f"Effective Price: {Decimal(eff_price) / Decimal(10**9):.2f} gwei",
            f"Transaction Fee: {fee_eth:.5f} ETH",
        ]
    )

    if data["revert_reason"]:
        lines.extend(["\nRevert Reason", "-" * 13, f" {data['revert_reason']}"])

    func = data["function"]
    lines.extend(["\nFunction Called", "-" * 15])
    if func["selector"]:
        lines.append(f"Selector:       {func['selector']}")
    lines.append(f"Function:       {func['name']}")
    if func["args"]:
        lines.append("Arguments:")
        for arg in func["args"]:
            lines.append(f"  - {arg['name']:<15} {arg['value']}")

    transfers = data["events"]["transfers"]
    if transfers:
        lines.extend(["\nToken Transfers", "-" * 15])
        for i, t in enumerate(transfers, 1):
            lines.append(
                f"{i}. {t['symbol']:<6}: {t['from'][:8]}... → {t['to'][:8]}...  {t['amount']:,.4f} {t['symbol']}"
            )

    swaps = data["events"]["swaps"]
    if swaps:
        lines.extend(["\nSwap Summary (Raw)", "-" * 18])
        for i, s in enumerate(swaps, 1):
            lines.append(
                f"Pair {s['pair'][:8]}... In: {s['amount0In']}/{s['amount1In']} Out: {s['amount0Out']}/{s['amount1Out']}"
            )

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Ethereum Transaction Analyzer")
    parser.add_argument("tx_hash", help="Transaction hash (0x...)")
    parser.add_argument(
        "--rpc",
        default=os.getenv("RPC_URL", "https://eth.llamarpc.com"),
        help="RPC URL",
    )
    parser.add_argument(
        "--format", choices=["text", "json"], default="text", help="Output format"
    )

    args = parser.parse_args()

    if not args.tx_hash.startswith("0x") or len(args.tx_hash) != 66:
        print(" Invalid transaction hash format.", file=sys.stderr)
        sys.exit(1)

    try:
        analysis = analyze(args.tx_hash, args.rpc)

        if args.format == "json":

            def _serializer(obj):
                if isinstance(obj, Decimal):
                    return float(obj)
                raise TypeError

            print(json.dumps(analysis, indent=2, default=_serializer))
        else:
            print(format_report(analysis))

    except Exception as e:
        print(f" Analysis failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
