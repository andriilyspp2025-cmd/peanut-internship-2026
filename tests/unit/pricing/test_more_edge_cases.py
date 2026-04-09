import re
import pytest
from decimal import Decimal
from src.core.types import Address, Token
from src.pricing.amm import UniswapV2Pair
from src.pricing.errors import (
    InsufficientLiquidityError,
)
from src.pricing.engine import QuoteError, Quote, PricingEngine
from src.pricing.imp_analyzer import PriceImpactAnalyzer
from src.pricing.router import RouteFinder, Route
from src.pricing.mempool import MempoolMonitor, ParsedSwap
from unittest.mock import MagicMock


WETH = Token(Address("0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"), "WETH", 18)
USDC = Token(Address("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"), "USDC", 6)
SHIB = Token(Address("0x95ad61b0a150d79219dcf64e1e6cc01f0b64c4ce"), "SHIB", 18)

# --- AMM TESTS ---


def test_amount_out_never_exceeds_reserve():
    pair = UniswapV2Pair(
        Address("0x0000000000000000000000000000000000000000"),
        WETH,
        USDC,
        10**18,
        2_000_000 * 10**6,
        30,
    )
    out = pair.get_amount_out(10**24, WETH)  # huge amount
    assert out < pair.reserve1


def test_simulate_swap_updates_reserves_correctly():
    pair = UniswapV2Pair(
        Address("0x0000000000000000000000000000000000000000"),
        WETH,
        USDC,
        1000 * 10**18,
        2_000_000 * 10**6,
        30,
    )
    amount_in = 10**18
    amount_out = pair.get_amount_out(amount_in, WETH)
    new_pair = pair.simulate_swap(amount_in, WETH)
    assert new_pair.reserve0 == pair.reserve0 + amount_in
    assert new_pair.reserve1 == pair.reserve1 - amount_out


def test_k_increases_after_swap_due_to_fee():
    pair = UniswapV2Pair(
        Address("0x0000000000000000000000000000000000000000"),
        WETH,
        USDC,
        1000 * 10**18,
        2_000_000 * 10**6,
        30,
    )
    k_before = pair.reserve0 * pair.reserve1
    new_pair = pair.simulate_swap(10**18, WETH)
    k_after = new_pair.reserve0 * new_pair.reserve1
    assert k_after >= k_before


def test_round_trip_swap_loses_to_fees():
    pair = UniswapV2Pair(
        Address("0x0000000000000000000000000000000000000000"),
        WETH,
        USDC,
        1000 * 10**18,
        2_000_000 * 10**6,
        30,
    )
    user_usdc_amount = 1000 * 10**6
    weth_got = pair.get_amount_out(user_usdc_amount, USDC)

    # Use simulate to reflect state
    new_pair = pair.simulate_swap(user_usdc_amount, USDC)
    usdc_got = new_pair.get_amount_out(weth_got, WETH)

    assert usdc_got < user_usdc_amount
    difference = user_usdc_amount - usdc_got
    assert difference > 0


def test_zero_fee_pair():
    pair = UniswapV2Pair(
        Address("0x0000000000000000000000000000000000000000"),
        WETH,
        USDC,
        1000 * 10**18,
        2000 * 10**6,
        fee_bps=0,
    )
    spot = pair.get_spot_price(WETH)
    exec_price = pair.get_execution_price(10, WETH)  # very small trade
    assert abs(spot - exec_price) < Decimal("1e-5")


def test_get_amount_in_full_reserve_raises():
    pair = UniswapV2Pair(
        Address("0x0000000000000000000000000000000000000000"),
        WETH,
        USDC,
        1000 * 10**18,
        2000 * 10**6,
        30,
    )
    with pytest.raises(InsufficientLiquidityError):
        pair.get_amount_in(pair.reserve1, USDC)


# --- IMPACT ANALYZER TESTS ---


def test_generate_impact_table_empty_sizes():
    pair = UniswapV2Pair(
        Address("0x0000000000000000000000000000000000000000"),
        WETH,
        USDC,
        1000 * 10**18,
        2000 * 10**6,
        30,
    )
    analyzer = PriceImpactAnalyzer(pair)
    result = analyzer.generate_impact_table(WETH, [])
    assert result == []


def test_find_max_size_for_100_percent_impact():
    pair = UniswapV2Pair(
        Address("0x0000000000000000000000000000000000000000"),
        WETH,
        USDC,
        1000 * 10**18,
        2000 * 10**6,
        30,
    )
    analyzer = PriceImpactAnalyzer(pair)
    max_size = analyzer.find_max_size_for_impact(WETH, Decimal("100.0"))
    assert max_size > 0


def test_find_max_size_zero_impact_raises():
    pair = UniswapV2Pair(
        Address("0x0000000000000000000000000000000000000000"),
        WETH,
        USDC,
        1000 * 10**18,
        2000 * 10**6,
        30,
    )
    analyzer = PriceImpactAnalyzer(pair)
    with pytest.raises(ValueError):
        analyzer.find_max_size_for_impact(WETH, Decimal("0.0"))
    with pytest.raises(ValueError):
        analyzer.find_max_size_for_impact(WETH, Decimal("-0.5"))


def test_execution_price_always_worse_than_spot():
    pair = UniswapV2Pair(
        Address("0x0000000000000000000000000000000000000000"),
        WETH,
        USDC,
        1000 * 10**18,
        2000 * 10**6,
        30,
    )
    spot = pair.get_spot_price(WETH)
    for size in [10**17, 10**18, 10**19]:
        exec_price = pair.get_execution_price(size, WETH)
        assert exec_price < spot


# --- ROUTER TESTS ---


def test_no_cycles_in_routes():
    A = WETH
    B = USDC
    C = SHIB
    p1 = UniswapV2Pair(
        Address("0x1111111111111111111111111111111111111111"), A, B, 1000, 1000
    )
    p2 = UniswapV2Pair(
        Address("0x2222222222222222222222222222222222222222"), B, C, 1000, 1000
    )
    p3 = UniswapV2Pair(
        Address("0x3333333333333333333333333333333333333333"), C, A, 1000, 1000
    )
    router = RouteFinder([p1, p2, p3])
    routes = router.find_all_routes(A, C, max_hops=3)
    for r in routes:
        addrs = [t.address.lower for t in r.path]
        assert len(addrs) == len(set(addrs))


def test_max_hops_limits_route_depth():
    A, B, C, D, E = (
        WETH,
        USDC,
        SHIB,
        Token(Address("0x0000000000000000000000000000000000000001"), "D", 18),
        Token(Address("0x0000000000000000000000000000000000000002"), "E", 18),
    )
    p1 = UniswapV2Pair(
        Address("0x1111111111111111111111111111111111111111"), A, B, 1000, 1000
    )
    p2 = UniswapV2Pair(
        Address("0x2222222222222222222222222222222222222222"), B, C, 1000, 1000
    )
    p3 = UniswapV2Pair(
        Address("0x3333333333333333333333333333333333333333"), C, D, 1000, 1000
    )
    p4 = UniswapV2Pair(
        Address("0x4444444444444444444444444444444444444444"), D, E, 1000, 1000
    )
    router = RouteFinder([p1, p2, p3, p4])
    assert len(router.find_all_routes(A, E, max_hops=2)) == 0
    assert len(router.find_all_routes(A, E, max_hops=4)) > 0


def test_intermediate_amounts_length():
    A = WETH
    B = USDC
    C = SHIB
    p1 = UniswapV2Pair(
        Address("0x1111111111111111111111111111111111111111"),
        A,
        B,
        1000 * 10**18,
        1000 * 10**6,
    )
    p2 = UniswapV2Pair(
        Address("0x2222222222222222222222222222222222222222"),
        B,
        C,
        1000 * 10**6,
        1000 * 10**18,
    )
    r = Route([p1, p2], [A, B, C])
    amounts = r.get_intermediate_amounts(10**17)
    assert len(amounts) == 3
    assert amounts[0] == 10**17


def test_gas_estimate_increases_with_hops():
    A = WETH
    B = USDC
    C = SHIB
    p1 = UniswapV2Pair(
        Address("0x1111111111111111111111111111111111111111"), A, B, 1000, 1000
    )
    p2 = UniswapV2Pair(
        Address("0x2222222222222222222222222222222222222222"), B, C, 1000, 1000
    )
    r1 = Route([p1], [A, B])
    r2 = Route([p1, p2], [A, B, C])
    assert r2.estimate_gas() > r1.estimate_gas()
    assert r2.estimate_gas() - r1.estimate_gas() == 100_000


def test_same_pool_not_used_twice_in_route():
    A = WETH
    B = USDC
    p1 = UniswapV2Pair(
        Address("0x1111111111111111111111111111111111111111"), A, B, 1000, 1000
    )
    router = RouteFinder([p1])
    routes = router.find_all_routes(A, A, max_hops=3)
    for r in routes:
        assert len(r.pools) == 1


# --- MEMPOOL MONITOR TESTS ---


def test_parse_swap_exact_tokens_for_eth():
    monitor = MempoolMonitor("wss://dummy", MagicMock())
    tx = {
        "input": "0x18cbafe5"
        + "0" * 50
        + "0de0b6b3a7640000"
        + "0" * 50
        + "06f05b59d3b20000"
        + "0" * 60,
        "to": "0x7a250d5630b4cf539739df2c5dacb4c659f2488d",
        "value": "0x0",
        "gasPrice": "0x1",
    }
    # Mock decoding properly instead
    from unittest.mock import patch

    with patch("src.pricing.mempool.decode_function") as mock_dt:
        mock_dt.return_value = {
            "name": "swapExactTokensForETH",
            "args": [
                {"name": "amountIn", "value": 10**18},
                {"name": "amountOutMin", "value": 5 * 10**17},
                {
                    "name": "path",
                    "value": [
                        Address("0x1111111111111111111111111111111111111111"),
                        Address("0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"),
                    ],
                },
                {"name": "to", "value": "0x"},
                {"name": "deadline", "value": 0},
            ],
        }
        res = monitor.parse_transaction(tx)
    assert res.amount_in == 10**18
    assert res.token_out is None


def test_slippage_tolerance_calculation():
    swap = ParsedSwap(
        tx_hash="0x1",
        sender="0x0000000000000000000000000000000000000000",
        token_in=WETH,
        token_out=USDC,
        amount_in=10**18,
        min_amount_out=950,
        gas_price=0,
        router="0x0000000000000000000000000000000000000000",
        dex="UniswapV2",
        method="swap",
        deadline=0,
    )
    swap.expected_amount_out = 1000
    assert swap.slippage_tolerance == Decimal("0.05")

    swap2 = ParsedSwap(
        tx_hash="0x1",
        sender="0x0000000000000000000000000000000000000000",
        token_in=WETH,
        token_out=USDC,
        amount_in=10**18,
        min_amount_out=950,
        gas_price=0,
        router="0x0000000000000000000000000000000000000000",
        dex="UniswapV2",
        method="swap",
        deadline=0,
    )
    with pytest.raises(ValueError):
        _ = swap2.slippage_tolerance


def test_parse_empty_input_returns_none():
    monitor = MempoolMonitor("wss://dummy", MagicMock())
    tx1 = {"input": "", "value": "0"}
    tx2 = {"input": "0x", "value": "0"}
    assert monitor.parse_transaction(tx1) is None
    assert monitor.parse_transaction(tx2) is None


def test_gas_price_fallback_to_max_fee():
    monitor = MempoolMonitor("wss://dummy", MagicMock())
    tx = {
        "input": "0x18cbafe5" + "0" * 60,
        "to": "0x7a250d5630b4cf539739df2c5dacb4c659f2488d",
        "value": "0x0",
        "maxFeePerGas": hex(30 * 10**9),
    }
    monitor.router_contract = MagicMock()
    monitor.router_contract.decode_function_input.return_value = (
        MagicMock(fn_name="swapExactTokensForETH"),
        {
            "amountIn": 10**18,
            "amountOutMin": 5 * 10**17,
            "path": [
                Address("0x1111111111111111111111111111111111111111"),
                Address("0x2222222222222222222222222222222222222222"),
            ],
            "to": "0x",
            "deadline": 0,
        },
    )
    monitor._get_token = MagicMock(return_value=WETH)
    res = monitor.parse_transaction(tx)
    assert res.gas_price == hex(30 * 10**9)


# --- QUOTE AND ENGINE TESTS ---


def test_quote_is_valid_exact_match():
    q = Quote(
        route=MagicMock(),
        amount_in=10**18,
        expected_output=2000 * 10**6,
        simulated_output=2000 * 10**6,
        gas_estimate=0,
        timestamp=0.0,
    )
    assert q.is_valid is True


def test_quote_is_invalid_large_divergence():
    q = Quote(
        route=MagicMock(),
        amount_in=10**18,
        expected_output=1000,
        simulated_output=998,
        gas_estimate=0,
        timestamp=0.0,
    )
    assert q.is_valid is False
    q2 = Quote(
        route=MagicMock(),
        amount_in=10**18,
        expected_output=1000,
        simulated_output=1000,
        gas_estimate=0,
        timestamp=0.0,
    )
    assert q2.is_valid is True


def test_pricing_engine_no_pools_raises_quote_error():
    engine = PricingEngine(MagicMock(), "http://dummy", "wss://dummy")
    with pytest.raises(
        QuoteError,
        match=re.escape("Router is not initialized. Call load_pools() first."),
    ):
        engine.get_quote(WETH, USDC, 10**18, 50)


def test_refresh_pool_updates_reserves():
    engine = PricingEngine(MagicMock(), "http://dummy", "wss://dummy")
    addr = Address("0x1111111111111111111111111111111111111111")
    p1 = UniswapV2Pair(addr, WETH, USDC, 1000, 1000)
    engine.pools[addr] = p1
    engine.router = RouteFinder([p1])

    p2 = UniswapV2Pair(addr, WETH, USDC, 2000, 2000)
    engine.client.web3 = MagicMock()
    # Mock fetching
    with pytest.MonkeyPatch.context() as m:
        m.setattr(UniswapV2Pair, "from_chain", MagicMock(return_value=p2))
        engine.refresh_pool(addr)

    assert engine.pools[addr].reserve0 == 2000
    assert engine.router is not None


WETH = Token(Address("0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"), "WETH", 18)
USDC = Token(Address("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"), "USDC", 6)
SHIB = Token(Address("0x95ad61b0a150d79219dcf64e1e6cc01f0b64c4ce"), "SHIB", 18)

# --- AMM TESTS ---
