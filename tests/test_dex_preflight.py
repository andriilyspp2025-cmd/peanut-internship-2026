"""
tests/test_dex_preflight.py
===========================
DEX Preflight Test — перевіряє весь DEX pipeline БЕЗ реальної транзакції.

Рівні перевірки:
  1. ABI Encoding      — чи правильно кодується exactInputSingle (web3 v7)
  2. eth_call dry-run  — чи не reverts swap на живому RPC (без gas, без підпису)
  3. Allowance check   — чи є approve для V3 Router
  4. Balance check     — чи достатньо токенів на гаманці

Запуск:
    python -m pytest tests/test_dex_preflight.py -v
    # або окремий тест:
    python -m pytest tests/test_dex_preflight.py::test_abi_encoding -v
"""

import os
import time
import pytest
from decimal import Decimal
from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

# ──────────────────────────────────────────────
# Константи
# ──────────────────────────────────────────────
V3_ROUTER = "0xE592427A0AEce92De3Edee1F18E0157C05861564"
CHIP_ADDRESS = "0x0C1c1C109FE34733fca54b82d7B46B75CFb71F6e"
ESP_ADDRESS = "0x3b8db18e69d6686ad9371a423afe3dd1065c94f1"
USDC_ADDRESS = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
CHIP_FEE_TIER = 100  # 0.01% — правильний тір для CHIP/USDC пулу
ESP_FEE_TIER = 100  # 0.01% — правильний тір для ESP/USDC пулу
CHIP_DECIMALS = 18
ESP_DECIMALS = 18
USDC_DECIMALS = 6
TEST_SIZE_CHIP = Decimal("10")  # маленький розмір для тесту
TEST_SIZE_ESP = Decimal("10")

V3_ROUTER_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "tokenIn", "type": "address"},
                    {"internalType": "address", "name": "tokenOut", "type": "address"},
                    {"internalType": "uint24", "name": "fee", "type": "uint24"},
                    {"internalType": "address", "name": "recipient", "type": "address"},
                    {"internalType": "uint256", "name": "deadline", "type": "uint256"},
                    {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                    {
                        "internalType": "uint256",
                        "name": "amountOutMinimum",
                        "type": "uint256",
                    },
                    {
                        "internalType": "uint160",
                        "name": "sqrtPriceLimitX96",
                        "type": "uint160",
                    },
                ],
                "internalType": "struct ISwapRouter.ExactInputSingleParams",
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "exactInputSingle",
        "outputs": [
            {"internalType": "uint256", "name": "amountOut", "type": "uint256"}
        ],
        "stateMutability": "payable",
        "type": "function",
    }
]

ERC20_ABI = [
    {
        "inputs": [{"name": "owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
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


# ──────────────────────────────────────────────
# Фікстури
# ──────────────────────────────────────────────
@pytest.fixture(scope="module")
def w3():
    rpc_url = os.getenv("RPC_URL") or os.getenv("ARBITRUM_RPC_URL")
    if not rpc_url:
        pytest.skip("RPC_URL не задано у .env")
    client = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 15}))
    if not client.is_connected():
        pytest.skip(f"RPC недоступний: {rpc_url}")
    return client


@pytest.fixture(scope="module")
def wallet_address(w3):
    addr = os.getenv("ADDRESS")
    if not addr:
        pytest.skip("ADDRESS не задано у .env")
    return w3.to_checksum_address(addr)


@pytest.fixture(scope="module")
def router(w3):
    return w3.eth.contract(
        address=w3.to_checksum_address(V3_ROUTER),
        abi=V3_ROUTER_ABI,
    )


@pytest.fixture(scope="module")
def chip_contract(w3):
    return w3.eth.contract(
        address=w3.to_checksum_address(CHIP_ADDRESS),
        abi=ERC20_ABI,
    )


@pytest.fixture(scope="module")
def esp_contract(w3):
    return w3.eth.contract(
        address=w3.to_checksum_address(ESP_ADDRESS),
        abi=ERC20_ABI,
    )


def _is_pair_enabled(pair_name: str) -> bool:
    try:
        from scripts.arb_bot import PAIRS_CONFIG

        return bool(PAIRS_CONFIG.get(pair_name, {}).get("enabled", False))
    except Exception:
        return True


def _make_swap_params(
    w3, wallet_address, amount_chip: Decimal, min_usdc: Decimal
) -> dict:
    """Будує params для exactInputSingle CHIP → USDC."""
    return {
        "tokenIn": w3.to_checksum_address(CHIP_ADDRESS),
        "tokenOut": w3.to_checksum_address(USDC_ADDRESS),
        "fee": CHIP_FEE_TIER,
        "recipient": wallet_address,
        "deadline": int(time.time()) + 300,
        "amountIn": int(amount_chip * (Decimal(10) ** CHIP_DECIMALS)),
        "amountOutMinimum": int(min_usdc * (Decimal(10) ** USDC_DECIMALS)),
        "sqrtPriceLimitX96": 0,
    }


# ══════════════════════════════════════════════
# TEST 1: ABI Encoding
# ══════════════════════════════════════════════
def test_abi_encoding(router, w3, wallet_address):
    """
    Перевіряє що web3 v7 коректно кодує calldata для exactInputSingle.
    Якщо тут помилка — DEX leg впаде ще до відправки транзакції.
    """
    params = _make_swap_params(w3, wallet_address, TEST_SIZE_CHIP, Decimal("0"))

    # web3 v7: abi_element_identifier замість fn_name
    calldata = router.encode_abi(
        abi_element_identifier="exactInputSingle", args=[params]
    )

    assert isinstance(calldata, str), "encode_abi має повертати hex-рядок"
    assert calldata.startswith("0x"), "calldata має починатись з 0x"
    assert calldata[:10] == "0x414bf389", (
        f"Неправильний selector! Очікується 0x414bf389 (exactInputSingle), "
        f"отримано: {calldata[:10]}"
    )
    print(
        f"\n✅ ABI Encoding OK — calldata: {calldata[:20]}...  (len={len(calldata)} chars)"
    )


# ══════════════════════════════════════════════
# TEST 2: Баланс CHIP на гаманці
# ══════════════════════════════════════════════
def test_chip_balance(chip_contract, wallet_address):
    """
    Перевіряє що на гаманці є хоча б TEST_SIZE_CHIP токенів CHIP для свапу.
    """
    if not _is_pair_enabled("CHIP/USDT"):
        pytest.skip("CHIP/USDT вимкнений у PAIRS_CONFIG")

    raw_balance = chip_contract.functions.balanceOf(wallet_address).call()
    balance = Decimal(raw_balance) / Decimal(10**CHIP_DECIMALS)
    print(f"\n💰 CHIP balance: {balance:.4f} CHIP")
    assert (
        balance >= TEST_SIZE_CHIP
    ), f"Недостатньо CHIP: є {balance:.4f}, потрібно мінімум {TEST_SIZE_CHIP}"
    print(f"✅ Balance OK — {balance:.4f} CHIP >= {TEST_SIZE_CHIP} CHIP")


def test_esp_balance(esp_contract, wallet_address):
    """
    Перевіряє що на гаманці є хоча б TEST_SIZE_ESP токенів ESP для свапу.
    """
    if not _is_pair_enabled("ESP/USDC"):
        pytest.skip("ESP/USDC вимкнений у PAIRS_CONFIG")

    raw_balance = esp_contract.functions.balanceOf(wallet_address).call()
    balance = Decimal(raw_balance) / Decimal(10**ESP_DECIMALS)
    print(f"\n💰 ESP balance: {balance:.4f} ESP")
    assert (
        balance >= TEST_SIZE_ESP
    ), f"Недостатньо ESP: є {balance:.4f}, потрібно мінімум {TEST_SIZE_ESP}"
    print(f"✅ Balance OK — {balance:.4f} ESP >= {TEST_SIZE_ESP} ESP")


# ══════════════════════════════════════════════
# TEST 3: Allowance CHIP → V3 Router
# ══════════════════════════════════════════════
def test_chip_allowance(chip_contract, w3, wallet_address):
    """
    Перевіряє що Router має approve на витрату CHIP.
    Потрібно для BUY_CEX_SELL_DEX (продаємо CHIP на DEX).
    """
    if not _is_pair_enabled("CHIP/USDT"):
        pytest.skip("CHIP/USDT вимкнений у PAIRS_CONFIG")

    router_checksum = w3.to_checksum_address(V3_ROUTER)
    raw_allowance = chip_contract.functions.allowance(
        wallet_address, router_checksum
    ).call()
    allowance = Decimal(raw_allowance) / Decimal(10**CHIP_DECIMALS)

    print(f"\n🔑 CHIP allowance для Router: {allowance:.2f}")

    if allowance < TEST_SIZE_CHIP:
        pytest.fail(
            "❌ НЕДОСТАТНІЙ CHIP ALLOWANCE!\n"
            "   Виправлення: python scripts/approve_chip_router.py"
        )
    print("✅ CHIP Allowance OK")


def test_esp_allowance(esp_contract, w3, wallet_address):
    """
    Перевіряє що Router має approve на витрату ESP.
    Потрібно для SELL_DEX leg по парі ESP/USDC.
    """
    if not _is_pair_enabled("ESP/USDC"):
        pytest.skip("ESP/USDC вимкнений у PAIRS_CONFIG")

    router_checksum = w3.to_checksum_address(V3_ROUTER)
    raw_allowance = esp_contract.functions.allowance(
        wallet_address, router_checksum
    ).call()
    allowance = Decimal(raw_allowance) / Decimal(10**ESP_DECIMALS)

    print(f"\n🔑 ESP allowance для Router: {allowance:.2f}")

    if allowance < TEST_SIZE_ESP:
        pytest.fail(
            "❌ НЕДОСТАТНІЙ ESP ALLOWANCE!\n"
            "   Виправлення: python scripts/approve_chip_router.py"
        )
    print("✅ ESP Allowance OK")


def test_usdc_allowance(w3, wallet_address):
    """
    Перевіряє що Router має approve на витрату USDC.
    Потрібно для BUY_DEX_SELL_CEX (купуємо CHIP за USDC на DEX).
    """
    usdc_contract = w3.eth.contract(
        address=w3.to_checksum_address(USDC_ADDRESS),
        abi=ERC20_ABI,
    )
    router_checksum = w3.to_checksum_address(V3_ROUTER)
    raw_allowance = usdc_contract.functions.allowance(
        wallet_address, router_checksum
    ).call()
    allowance_usdc = Decimal(raw_allowance) / Decimal(10**USDC_DECIMALS)

    print(f"\n🔑 USDC allowance для Router: {allowance_usdc:.2f}")

    if allowance_usdc < Decimal("1"):
        pytest.fail(
            "❌ НЕДОСТАТНІЙ USDC ALLOWANCE!\n"
            "   Виправлення: python scripts/approve_chip_router.py"
        )
    print("✅ USDC Allowance OK")


# ══════════════════════════════════════════════
# TEST 4: eth_call dry-run (симуляція без gas)
# ══════════════════════════════════════════════
def test_eth_call_simulation(router, w3, wallet_address):
    """
    Найважливіший тест: виконує eth_call до V3 Router.
    eth_call НЕ витрачає gas і НЕ відправляє транзакцію,
    але повертає той самий результат що й реальний swap.

    Якщо тут revert → реальна транзакція теж упаде.
    Якщо OK → отримуємо очікувану кількість USDC.
    """
    if not _is_pair_enabled("CHIP/USDT"):
        pytest.skip("CHIP/USDT вимкнений у PAIRS_CONFIG")

    # amountOutMinimum=0 щоб не отримати revert через slippage
    params = _make_swap_params(w3, wallet_address, TEST_SIZE_CHIP, Decimal("0"))

    calldata = router.encode_abi(
        abi_element_identifier="exactInputSingle", args=[params]
    )
    calldata_bytes = bytes.fromhex(calldata[2:])

    print("\n🔍 Виконую eth_call симуляцію...")
    print(f"   tokenIn:  CHIP  ({CHIP_ADDRESS})")
    print(f"   tokenOut: USDC  ({USDC_ADDRESS})")
    print(f"   fee:      {CHIP_FEE_TIER} ({CHIP_FEE_TIER/10000:.2%})")
    print(f"   amountIn: {TEST_SIZE_CHIP} CHIP")

    try:
        result = w3.eth.call(
            {
                "to": w3.to_checksum_address(V3_ROUTER),
                "data": calldata_bytes,
                "from": wallet_address,
            },
            "latest",
        )

        amount_out_raw = int.from_bytes(result, "big") if result else 0
        amount_out_usdc = Decimal(amount_out_raw) / Decimal(10**USDC_DECIMALS)

        print("\n✅ eth_call успішний!")
        print(
            f"   Очікувано отримати: {amount_out_usdc:.6f} USDC за {TEST_SIZE_CHIP} CHIP"
        )
        print(f"   Ціна DEX: ~{amount_out_usdc / TEST_SIZE_CHIP:.6f} USDC/CHIP")

        assert amount_out_usdc > Decimal(
            "0"
        ), "eth_call повернув 0 USDC — пул не має ліквідності або неправильний fee tier"

    except Exception as e:
        error_msg = str(e)
        # Намагаємось розшифрувати revert reason
        hint = ""
        if "STF" in error_msg or "transfer amount" in error_msg.lower():
            hint = "\n   ⚠️  STF = 'Safe Transfer Failed' → CHIP approve не виданий для Router!"
        elif "AS" in error_msg:
            hint = "\n   ⚠️  AS = 'Arithmetic error' → amountOutMinimum занадто велике (slippage)"
        elif "SPL" in error_msg:
            hint = "\n   ⚠️  SPL = 'Price Limit Exceeded' → sqrtPriceLimitX96 занадто обмежує"
        elif "insufficient liquidity" in error_msg.lower() or "IIA" in error_msg:
            hint = f"\n   ⚠️  Недостатньо ліквідності у пулі з fee_tier={CHIP_FEE_TIER}"
        elif "execution reverted" in error_msg.lower() and "0x" in error_msg:
            hint = "\n   ⚠️  Контракт зробив revert — перевір адреси токенів і fee_tier"

        pytest.fail(
            f"❌ eth_call FAILED — swap зробить revert на реальній транзакції!\n"
            f"   Помилка: {error_msg}{hint}"
        )


def test_eth_call_simulation_esp(router, w3, wallet_address):
    """
    eth_call dry-run для ESP -> USDC.
    Якщо тут revert — реальний DEX swap для ESP також впаде.
    """
    if not _is_pair_enabled("ESP/USDC"):
        pytest.skip("ESP/USDC вимкнений у PAIRS_CONFIG")

    params = {
        "tokenIn": w3.to_checksum_address(ESP_ADDRESS),
        "tokenOut": w3.to_checksum_address(USDC_ADDRESS),
        "fee": ESP_FEE_TIER,
        "recipient": wallet_address,
        "deadline": int(time.time()) + 300,
        "amountIn": int(TEST_SIZE_ESP * (Decimal(10) ** ESP_DECIMALS)),
        "amountOutMinimum": 0,
        "sqrtPriceLimitX96": 0,
    }

    calldata = router.encode_abi(
        abi_element_identifier="exactInputSingle", args=[params]
    )
    calldata_bytes = bytes.fromhex(calldata[2:])

    print("\n🔍 Виконую eth_call симуляцію для ESP...")
    print(f"   tokenIn:  ESP   ({ESP_ADDRESS})")
    print(f"   tokenOut: USDC  ({USDC_ADDRESS})")
    print(f"   fee:      {ESP_FEE_TIER} ({ESP_FEE_TIER/10000:.2%})")
    print(f"   amountIn: {TEST_SIZE_ESP} ESP")

    try:
        result = w3.eth.call(
            {
                "to": w3.to_checksum_address(V3_ROUTER),
                "data": calldata_bytes,
                "from": wallet_address,
            },
            "latest",
        )

        amount_out_raw = int.from_bytes(result, "big") if result else 0
        amount_out_usdc = Decimal(amount_out_raw) / Decimal(10**USDC_DECIMALS)

        print("\n✅ eth_call ESP успішний!")
        print(
            f"   Очікувано отримати: {amount_out_usdc:.6f} USDC за {TEST_SIZE_ESP} ESP"
        )
        print(f"   Ціна DEX: ~{amount_out_usdc / TEST_SIZE_ESP:.6f} USDC/ESP")

        assert amount_out_usdc > Decimal(
            "0"
        ), "eth_call ESP повернув 0 USDC — пул не має ліквідності або неправильний fee tier"
    except Exception as e:
        pytest.fail(
            f"❌ eth_call ESP FAILED — swap зробить revert на реальній транзакції!\n"
            f"   Помилка: {e}"
        )


# ══════════════════════════════════════════════
# TEST 5: Перевірка fee_tier конфігу
# ══════════════════════════════════════════════
def test_pairs_config_fee_tier():
    """
    Перевіряє що PAIRS_CONFIG у arb_bot.py містить правильний fee_tier для ESP/USDC.
    fee_tier=100 відповідає пулу ESP/USDC (0.01%).
    """
    try:
        from scripts.arb_bot import PAIRS_CONFIG
    except ImportError as e:
        pytest.skip(f"Не вдалося імпортувати PAIRS_CONFIG: {e}")

    assert "ESP/USDC" in PAIRS_CONFIG, "ESP/USDC відсутній у PAIRS_CONFIG"
    cfg = PAIRS_CONFIG["ESP/USDC"]

    assert cfg.get("enabled") is True, "ESP/USDC вимкнений у PAIRS_CONFIG"
    assert (
        cfg.get("fee_tier") == 100
    ), f"Неправильний fee_tier: {cfg.get('fee_tier')} (потрібно 100 для ESP/USDC 0.01%)"
    assert (
        cfg.get("dex_quote") == "USDC"
    ), f"Неправильний dex_quote: {cfg.get('dex_quote')} (потрібно 'USDC')"
    print(
        f"\n✅ PAIRS_CONFIG ESP OK — fee_tier={cfg['fee_tier']}, dex_quote={cfg['dex_quote']}"
    )


# ══════════════════════════════════════════════
# TEST 6: Encoder + Router selector перевірка
# ══════════════════════════════════════════════
def test_router_address_and_selector(w3):
    """
    Перевіряє що V3_ROUTER — це дійсно контракт на Arbitrum (не EOA),
    і selector 0x414bf389 відповідає exactInputSingle.
    """
    code = w3.eth.get_code(w3.to_checksum_address(V3_ROUTER))
    assert (
        len(code) > 10
    ), f"V3_ROUTER ({V3_ROUTER}) — не контракт або не задеплоєний на цій мережі!"

    # Перевірка selector
    from web3 import Web3 as _W3

    selector = _W3.keccak(
        text="exactInputSingle((address,address,uint24,address,uint256,uint256,uint256,uint160))"
    )[:4].hex()
    assert (
        selector == "414bf389"
    ), f"Function selector не збігається: {selector} != 414bf389"
    print(f"\n✅ Router contract OK — code size: {len(code)} bytes")
    print(f"✅ exactInputSingle selector: 0x{selector}")
