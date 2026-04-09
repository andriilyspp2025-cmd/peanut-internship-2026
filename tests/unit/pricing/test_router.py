from src.core.types import Address, Token
from src.pricing.amm import UniswapV2Pair
from src.pricing.router import RouteFinder, Route

WETH = Token(Address("0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"), "WETH", 18)
USDC = Token(Address("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"), "USDC", 6)
SHIB = Token(Address("0x95ad61b0a150d79219dcf64e1e6cc01f0b64c4ce"), "SHIB", 18)


def test_route_finder_initialization():
    pool1 = UniswapV2Pair(
        Address("0x1111111111111111111111111111111111111111"),
        WETH,
        USDC,
        10**18,
        2000 * 10**6,
    )
    router = RouteFinder([pool1])
    assert len(router.pools) == 1
    assert len(router.graph) > 0


def test_find_all_routes_max_hops():
    pool1 = UniswapV2Pair(
        Address("0x1111111111111111111111111111111111111111"),
        WETH,
        USDC,
        10**18,
        2000 * 10**6,
    )
    pool2 = UniswapV2Pair(
        Address("0x2222222222222222222222222222222222222222"),
        USDC,
        SHIB,
        2000 * 10**6,
        10**24,
    )
    router = RouteFinder([pool1, pool2])

    routes = router.find_all_routes(WETH, SHIB, max_hops=2)
    assert len(routes) > 0
    assert routes[0].num_hops == 2


def test_route_gas_estimation():
    pool1 = UniswapV2Pair(
        Address("0x1111111111111111111111111111111111111111"),
        WETH,
        USDC,
        10**18,
        2000 * 10**6,
    )
    route = Route([pool1], [WETH, USDC])

    gas = route.estimate_gas()
    # 150000 base + 1 hops * 100000
    assert gas == 250000
