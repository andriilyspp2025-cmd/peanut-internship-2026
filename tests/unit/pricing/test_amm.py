import pytest
from decimal import Decimal
from src.core.types import Address, Token
from src.pricing.amm import UniswapV2Pair
from src.pricing.errors import (
    InsufficientLiquidityError,
    AmountError,
    InvalidTokenError,
)
from src.pricing.imp_analyzer import PriceImpactAnalyzer

WETH = Token(Address("0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"), "WETH", 18)
USDC = Token(Address("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"), "USDC", 6)
WRONG_TOKEN = Token(Address("0xdeadbeef00000000000000000000000000000000"), "WRONG", 18)


def test_get_amount_out_basic():
    pair = UniswapV2Pair(
        Address("0x0000000000000000000000000000000000000000"),
        WETH,
        USDC,
        1000 * 10**18,
        2_000_000 * 10**6,
        30,
    )
    eth_out = pair.get_amount_out(2000 * 10**6, USDC)
    assert eth_out < 1 * 10**18
    assert eth_out > int(0.99 * 10**18)
    assert isinstance(eth_out, int)


def test_matches_uniswap_v2_solidity_vector():
    pair = UniswapV2Pair(
        Address("0x0000000000000000000000000000000000000000"),
        WETH,
        USDC,
        5 * 10**18,
        10 * 10**18,
        30,
    )
    out = pair.get_amount_out(10**18, WETH)
    assert out == 1662497915624478906


def test_integer_precision_large_numbers():
    pair = UniswapV2Pair(
        Address("0x0000000000000000000000000000000000000000"),
        WETH,
        USDC,
        10**30,
        10**30,
        30,
    )
    out1 = pair.get_amount_out(10**25, WETH)
    assert isinstance(out1, int)
    assert out1 > 0
    assert pair.get_amount_out(10**25 + 1, WETH) >= out1


def test_simulate_swap_does_not_mutate_original():
    pair = UniswapV2Pair(
        Address("0x0000000000000000000000000000000000000000"),
        WETH,
        USDC,
        1000 * 10**18,
        2_000_000 * 10**6,
    )
    o0, o1 = pair.reserve0, pair.reserve1
    np = pair.simulate_swap(10**18, WETH)
    assert pair.reserve0 == o0 and pair.reserve1 == o1
    assert np.reserve0 != o0 and np.reserve1 != o1


def test_get_amount_in_is_inverse_of_get_amount_out():
    pair = UniswapV2Pair(
        Address("0x0000000000000000000000000000000000000000"),
        WETH,
        USDC,
        1000 * 10**18,
        2_000_000 * 10**6,
        30,
    )
    amount_in = pair.get_amount_in(10**6, USDC)
    actual_out = pair.get_amount_out(amount_in, WETH)
    assert actual_out >= 10**6 and (actual_out - 10**6) <= 1


def test_get_amount_out_zero_input_raises():
    pair = UniswapV2Pair(
        Address("0x0000000000000000000000000000000000000000"), WETH, USDC, 1000, 1000
    )
    with pytest.raises(AmountError):
        pair.get_amount_out(0, WETH)
    with pytest.raises(AmountError):
        pair.get_amount_out(-1, WETH)


def test_get_amount_out_wrong_token_raises():
    pair = UniswapV2Pair(
        Address("0x0000000000000000000000000000000000000000"), WETH, USDC, 1000, 1000
    )
    with pytest.raises(InvalidTokenError):
        pair.get_amount_out(100, WRONG_TOKEN)


def test_pair_zero_reserves_raises():
    addr = Address("0x0000000000000000000000000000000000000000")
    with pytest.raises(InsufficientLiquidityError):
        UniswapV2Pair(addr, WETH, USDC, 0, 1000)
    with pytest.raises(InsufficientLiquidityError):
        UniswapV2Pair(addr, WETH, USDC, 1000, 0)
    with pytest.raises(InsufficientLiquidityError):
        UniswapV2Pair(addr, WETH, USDC, -10, -10)


def test_higher_fee_gives_less_output():
    addr = Address("0x0000000000000000000000000000000000000000")
    pl = UniswapV2Pair(addr, WETH, USDC, 1000 * 10**18, 2_000_000 * 10**6, 30)
    ph = UniswapV2Pair(addr, WETH, USDC, 1000 * 10**18, 2_000_000 * 10**6, 100)
    assert pl.get_amount_out(10**18, WETH) > ph.get_amount_out(10**18, WETH)


def test_spot_price_symmetry():
    pair = UniswapV2Pair(
        Address("0x0000000000000000000000000000000000000000"), WETH, USDC, 1000, 1000
    )
    assert abs(
        pair.get_spot_price(WETH) * pair.get_spot_price(USDC) - Decimal(1)
    ) < Decimal("1e-10")


def test_generate_impact_table_structure():
    pair = UniswapV2Pair(
        Address("0x0000000000000000000000000000000000000000"),
        WETH,
        USDC,
        1000 * 10**18,
        2_000_000 * 10**6,
    )
    table = PriceImpactAnalyzer(pair).generate_impact_table(
        WETH, [10**18, 10 * 10**18, 100 * 10**18]
    )
    assert len(table) == 3
    for r in table:
        assert all(
            k in r
            for k in [
                "amount_in",
                "amount_out",
                "spot_price",
                "execution_price",
                "price_impact_pct",
            ]
        )
    assert (
        table[0]["price_impact_pct"]
        < table[1]["price_impact_pct"]
        < table[2]["price_impact_pct"]
    )


def test_price_impact_increases_with_size():
    pair = UniswapV2Pair(
        Address("0x0000000000000000000000000000000000000000"),
        WETH,
        USDC,
        1000 * 10**18,
        2_000_000 * 10**6,
    )
    table = PriceImpactAnalyzer(pair).generate_impact_table(
        WETH, [10**17, 10**18, 10**19, 10**20]
    )
    impacts = [r["price_impact_pct"] for r in table]
    for i in range(len(impacts) - 1):
        assert impacts[i] < impacts[i + 1]


def test_find_max_size_for_impact_correctness():
    pair = UniswapV2Pair(
        Address("0x0000000000000000000000000000000000000000"),
        WETH,
        USDC,
        1000 * 10**18,
        2_000_000 * 10**6,
    )
    analyzer = PriceImpactAnalyzer(pair)
    max_size = analyzer.find_max_size_for_impact(WETH, Decimal("1.0"))
    assert pair.get_price_impact(max_size, WETH) <= Decimal("0.01")
    assert pair.get_price_impact(max_size + 1, WETH) > Decimal("0.01")


def test_estimate_true_cost_returns_correct_structure():
    pair = UniswapV2Pair(
        Address("0x0000000000000000000000000000000000000000"),
        WETH,
        USDC,
        1000 * 10**18,
        2_000_000 * 10**6,
    )
    res = PriceImpactAnalyzer(pair).estimate_true_cost(10**18, WETH, gas_price_gwei=20)
    assert all(
        k in res
        for k in [
            "gross_output",
            "gas_cost_eth",
            "gas_cost_in_output_token",
            "net_output",
            "effective_price",
        ]
    )
    assert res["net_output"] <= res["gross_output"] and res["gas_cost_eth"] > 0


def test_estimate_true_cost_zero_gas():
    pair = UniswapV2Pair(
        Address("0x0000000000000000000000000000000000000000"),
        WETH,
        USDC,
        1000 * 10**18,
        2_000_000 * 10**6,
    )
    res = PriceImpactAnalyzer(pair).estimate_true_cost(10**18, WETH, gas_price_gwei=0)
    assert res["net_output"] == res["gross_output"]
