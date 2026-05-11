"""
scripts/approve_chip_router.py
==============================
Одноразовий скрипт: видає unlimited approve для вказаних ERC20 токенів
до Uniswap V3 Router на Arbitrum.

Потрібно виконати ТІЛЬКИ ОДИН РАЗ перед першим DEX swap.

Запуск:
    python scripts/approve_chip_router.py

Опційно (обмежити список токенів):
    APPROVE_SYMBOLS=ESP,USDC python scripts/approve_chip_router.py

Опційно (один перемикач-профіль):
    APPROVE_PROFILE=ESP python scripts/approve_chip_router.py
"""

import os
import sys
from decimal import Decimal
from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

# ── Конфіг ──
CHIP_ADDRESS = "0x0C1c1C109FE34733fca54b82d7B46B75CFb71F6e"
USDC_ADDRESS = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
ESP_ADDRESS = "0x3b8db18e69d6686ad9371a423afe3dd1065c94f1"
ZRO_ADDRESS = "0x6985884C4392D348587B19cb9eAAf157F13271cd"
V3_ROUTER = "0xE592427A0AEce92De3Edee1F18E0157C05861564"
MAX_UINT256 = 2**256 - 1

APPROVE_ABI = [
    {
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
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

TOKENS = [
    {"symbol": "CHIP", "address": CHIP_ADDRESS, "decimals": 18},
    {"symbol": "ESP", "address": ESP_ADDRESS, "decimals": 18},
    {"symbol": "ZRO", "address": ZRO_ADDRESS, "decimals": 18},
    {"symbol": "USDC", "address": USDC_ADDRESS, "decimals": 6},
]

TOKEN_REGISTRY = {token["symbol"]: token for token in TOKENS}

# One-switch profiles: change only APPROVE_PROFILE to control approve set.
APPROVE_PROFILES = {
    "ALL": ["CHIP", "ESP", "ZRO", "USDC"],
    "ESP": ["ESP", "USDC"],
    "ZRO": ["ZRO", "USDC"],
    "CHIP": ["CHIP", "USDC"],
    "ESP_ONLY": ["ESP"],
    "ZRO_ONLY": ["ZRO"],
    "USDC_ONLY": ["USDC"],
}


def resolve_tokens_to_approve() -> list[dict]:
    """Resolve token list from APPROVE_SYMBOLS or APPROVE_PROFILE."""
    raw = os.getenv("APPROVE_SYMBOLS", "").strip()
    if not raw:
        profile = os.getenv("APPROVE_PROFILE", "ALL").strip().upper()
        symbols = APPROVE_PROFILES.get(profile)
        if symbols is None:
            print(f"❌ Невідомий APPROVE_PROFILE: {profile}")
            print(f"   Доступні профілі: {', '.join(sorted(APPROVE_PROFILES.keys()))}")
            sys.exit(1)
        return [TOKEN_REGISTRY[symbol] for symbol in symbols]

    symbols = [part.strip().upper() for part in raw.split(",") if part.strip()]
    selected: list[dict] = []
    unknown: list[str] = []
    for symbol in symbols:
        token = TOKEN_REGISTRY.get(symbol)
        if token is None:
            unknown.append(symbol)
            continue
        selected.append(token)

    if unknown:
        print(f"❌ Невідомі символи в APPROVE_SYMBOLS: {', '.join(unknown)}")
        print(f"   Доступні: {', '.join(sorted(TOKEN_REGISTRY.keys()))}")
        sys.exit(1)

    if not selected:
        print("❌ APPROVE_SYMBOLS задано, але не вибрано жодного валідного токена")
        sys.exit(1)

    return selected


def resolve_rpc_candidates() -> list[str]:
    """Build prioritized HTTP RPC candidate list from environment variables."""
    candidates: list[str] = []

    primary = [
        os.getenv("RPC_URL", "").strip(),
        os.getenv("ARBITRUM_RPC_URL", "").strip(),
    ]
    for url in primary:
        if url:
            candidates.append(url)

    qn_keys = sorted(
        [k for k in os.environ.keys() if k.startswith("QN_HTTP_")],
        key=lambda name: (
            int(name.split("_")[-1]) if name.split("_")[-1].isdigit() else 9999
        ),
    )
    for key in qn_keys:
        url = os.getenv(key, "").strip()
        if url:
            candidates.append(url)

    http_list = os.getenv("HTTP_RPC_ENDPOINTS", "").strip()
    if http_list:
        for part in http_list.split(","):
            url = part.strip()
            if url:
                candidates.append(url)

    deduped: list[str] = []
    seen: set[str] = set()
    for url in candidates:
        if not url.startswith("http"):
            continue
        if url in seen:
            continue
        seen.add(url)
        deduped.append(url)

    return deduped


def approve_token(w3, wallet, private_key, token_info, router_c):
    symbol = token_info["symbol"]
    decimals = token_info["decimals"]
    contract = w3.eth.contract(
        address=w3.to_checksum_address(token_info["address"]),
        abi=APPROVE_ABI,
    )

    current = contract.functions.allowance(wallet, router_c).call()
    current_human = Decimal(current) / Decimal(10**decimals)
    print(f"\n[{symbol}] Поточний allowance: {current_human:.2f}")

    if current >= MAX_UINT256 // 2:
        print(f"[{symbol}] ✅ Allowance вже sufficient — пропускаємо.")
        return

    print(f"[{symbol}] 🔐 Видаємо unlimited approve для Router...")

    nonce = w3.eth.get_transaction_count(wallet, "pending")
    base_fee = w3.eth.get_block("latest")["baseFeePerGas"]
    priority = w3.eth.max_priority_fee or 1_000_000
    max_fee = base_fee * 2 + priority

    print(f"[{symbol}]    baseFee:      {base_fee / 1e9:.4f} Gwei")
    print(f"[{symbol}]    maxFeePerGas: {max_fee  / 1e9:.4f} Gwei")

    tx = contract.functions.approve(router_c, MAX_UINT256).build_transaction(
        {
            "from": wallet,
            "nonce": nonce,
            "gas": 100_000,
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": priority,
            "chainId": 42161,
        }
    )

    signed = w3.eth.account.sign_transaction(tx, private_key=private_key)
    raw = (
        signed.raw_transaction
        if hasattr(signed, "raw_transaction")
        else signed.rawTransaction
    )

    print(f"[{symbol}] 📤 Відправляємо...")
    tx_hash = w3.eth.send_raw_transaction(raw)
    print(f"[{symbol}]    TX: https://arbiscan.io/tx/{tx_hash.hex()}")
    print(f"[{symbol}] ⏳ Чекаємо підтвердження...")

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt.status == 1:
        new_all = contract.functions.allowance(wallet, router_c).call()
        status = "unlimited" if new_all >= MAX_UINT256 // 2 else str(new_all)
        print(f"[{symbol}] ✅ Approve успішний! Новий allowance: {status}")
    else:
        print(f"[{symbol}] ❌ Approve REVERTED!")


def main():
    private_key = os.getenv("PRIVATE_KEY")
    wallet_addr = os.getenv("ADDRESS")
    rpc_candidates = resolve_rpc_candidates()

    if not all([private_key, wallet_addr]):
        print("❌ Потрібні: PRIVATE_KEY, ADDRESS у .env")
        sys.exit(1)

    if not rpc_candidates:
        print(
            "❌ Не знайдено HTTP RPC endpoint у .env (RPC_URL / ARBITRUM_RPC_URL / QN_HTTP_* / HTTP_RPC_ENDPOINTS)"
        )
        sys.exit(1)

    w3 = None
    active_rpc = None
    for rpc_url in rpc_candidates:
        print(f"🔌 Перевіряю RPC: {rpc_url}")
        candidate = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))
        try:
            if candidate.is_connected():
                w3 = candidate
                active_rpc = rpc_url
                break
        except Exception as exc:
            print(f"⚠️ RPC помилка: {rpc_url} -> {exc}")

    if w3 is None or active_rpc is None:
        print("❌ Жоден RPC endpoint не доступний")
        sys.exit(1)

    wallet = w3.to_checksum_address(wallet_addr)
    router_c = w3.to_checksum_address(V3_ROUTER)

    print(f"🌐 Active RPC: {active_rpc}")
    print(f"🔑 Гаманець: {wallet}")
    print(f"📄 Router:   {V3_ROUTER}")
    print(f"🌐 Chain ID: {w3.eth.chain_id}")

    tokens_to_approve = resolve_tokens_to_approve()
    print(f"🧾 Токени для approve: {', '.join(t['symbol'] for t in tokens_to_approve)}")

    for token in tokens_to_approve:
        approve_token(w3, wallet, private_key, token, router_c)

    print("\n🚀 Готово! Approve виконано для обраних токенів.")
    print("   Запусти тести: python -m pytest tests/test_dex_preflight.py -v")


if __name__ == "__main__":
    main()
