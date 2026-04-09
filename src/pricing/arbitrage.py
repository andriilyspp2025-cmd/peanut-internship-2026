from dataclasses import dataclass
from typing import List

from src.core.types import Token
from src.pricing.amm import UniswapV2Pair
from src.pricing.router import RouteFinder, Route


@dataclass
class ArbitrageOpportunity:
    route: Route
    amount_in: int
    gross_profit: int
    gas_cost: int
    strategy_name: str

    @property
    def net_profit(self) -> int:
        return self.gross_profit - self.gas_cost


class ArbitrageDetector:
    """Detects cyclic arbitrage opportunities across a set of AMM pools."""

    def __init__(self, pools: List[UniswapV2Pair]):
        self.pools = pools
        # Reuse RouteFinder's graph-building and DFS logic
        self.route_finder = RouteFinder(pools)

    def find_arbitrage_opportunities(
        self, base_token: Token, amount_in: int, gas_price_gwei: int, max_hops: int = 4
    ) -> List[ArbitrageOpportunity]:
        """
        Finds circular routes (e.g., WETH -> USDC -> WETH) and evaluating profitability.
        Strictly uses integer math.
        """
        # Find cyclic routes starting and ending with base_token
        all_routes = self.route_finder.find_all_routes(base_token, base_token, max_hops)
        opportunities = []

        for route in all_routes:
            if route.num_hops < 2:
                continue

            gross_output = route.get_output(amount_in)
            if gross_output == 0:
                continue

            # Calculate gas cost in wei mapped to base token (assuming base_token is WETH)
            gas_cost_wei = route.estimate_gas() * gas_price_gwei * (10**9)

            # Output minus the initial inputted capital
            gross_profit = gross_output - amount_in
            net_profit = gross_profit - gas_cost_wei

            if net_profit > 0:
                opportunities.append(
                    ArbitrageOpportunity(
                        route=route,
                        amount_in=amount_in,
                        gross_profit=gross_profit,
                        gas_cost=gas_cost_wei,
                        strategy_name=f"Cyclic Arbitrage ({route.num_hops} hops)",
                    )
                )

        # Sort the opportunities by their net profit descending
        opportunities.sort(key=lambda opp: opp.net_profit, reverse=True)
        return opportunities
