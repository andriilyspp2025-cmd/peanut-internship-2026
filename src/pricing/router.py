from typing import List, Tuple, Optional, Dict, Set
from src.core.types import Token
from src.pricing.protocols import AMMPool
from src.pricing.errors import AMMError
from collections import defaultdict

_WETH_ADDR = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"


class Route:
    """Represents an execution path through one or more AMM pools."""

    def __init__(self, pools: List[AMMPool], path: List[Token]):
        if len(path) != len(pools) + 1:
            raise ValueError("Path must have exactly one more token than pools")

        self.pools = pools
        self.path = path

    @property
    def num_hops(self) -> int:
        """Returns the number of pools in the route."""
        return len(self.pools)

    def get_output(self, amount_in: int) -> int:
        """Simulates sequential swaps across all pools in the route."""
        current_amount = amount_in

        try:
            for pool, token_in in zip(self.pools, self.path):
                current_amount = pool.get_amount_out(current_amount, token_in)
                if current_amount == 0:
                    return 0
            return current_amount

        except AMMError:
            return 0

    def get_intermediate_amounts(self, amount_in: int) -> List[int]:
        """Returns an array of input/output amounts at each hop."""
        amounts = [amount_in]
        current_amount = amount_in

        try:
            for pool, token_in in zip(self.pools, self.path):
                current_amount = pool.get_amount_out(current_amount, token_in)
                amounts.append(current_amount)
            return amounts

        except AMMError:
            while len(amounts) <= self.num_hops:
                amounts.append(0)
            return amounts

    def estimate_gas(self) -> int:
        """Estimates gas usage based on the number of hops."""
        return 150000 + (self.num_hops * 100000)

    def __str__(self) -> str:
        return " -> ".join([t.symbol for t in self.path])


class RouteFinder:
    """Discovers and evaluates possible pool routing combinations."""

    def __init__(self, pools: List[AMMPool]):
        self.pools = pools
        self.graph = self._build_graph()

    def _build_graph(self) -> Dict[Token, List[Tuple[AMMPool, Token]]]:
        """Builds an adjacency list of connectable AMM pools."""
        graph = defaultdict(list)
        for pool in self.pools:
            graph[pool.token0].append((pool, pool.token1))
            graph[pool.token1].append((pool, pool.token0))
        return graph

    def find_all_routes(
        self, token_in: Token, token_out: Token, max_hops: int = 3
    ) -> List[Route]:
        """Uses Depth-First Search to find all valid acyclic routes."""
        routes = []

        def dfs(
            current_token: Token,
            target_token: Token,
            current_path: List[Token],
            current_pools: List[AMMPool],
            visited: Set[str],
            visited_pools: Set[int],
        ):
            if len(current_pools) > max_hops:
                return

            if (
                current_token.address.lower == target_token.address.lower
                and len(current_pools) > 0
            ):
                routes.append(Route(list(current_pools), list(current_path)))
                return

            if current_token not in self.graph:
                return

            for pool, next_token in self.graph[current_token]:
                pool_id = id(pool)
                next_addr = next_token.address.lower

                if pool_id in visited_pools:
                    continue
                if next_addr in visited and next_addr != target_token.address.lower:
                    continue

                visited.add(next_addr)
                visited_pools.add(pool_id)
                current_path.append(next_token)
                current_pools.append(pool)

                dfs(
                    next_token,
                    target_token,
                    current_path,
                    current_pools,
                    visited,
                    visited_pools,
                )

                current_pools.pop()
                current_path.pop()
                visited.remove(next_addr)
                visited_pools.remove(pool_id)

        dfs(token_in, token_out, [token_in], [], {token_in.address.lower}, set())
        return routes

    def find_best_route(
        self,
        token_in: Token,
        token_out: Token,
        amount_in: int,
        gas_price_gwei: int,
        max_hops: int = 3,
    ) -> Tuple[Optional[Route], int]:
        """Finds the most profitable route after deducting estimated gas fees."""
        all_routes = self.find_all_routes(token_in, token_out, max_hops)

        best_route = None
        max_net_output = 0

        for route in all_routes:
            gross_output = route.get_output(amount_in)
            if gross_output == 0:
                continue

            gas_cost_wei = route.estimate_gas() * gas_price_gwei * (10**9)

            # Gas Routing Logic
            if token_out.address.lower == _WETH_ADDR.lower:
                gas_cost_token = gas_cost_wei
            else:
                # TODO: Requires Oracle or WETH-Pool routing for non-WETH gas estimation
                gas_cost_token = 0

            net_output = max(0, gross_output - gas_cost_token)

            if net_output > max_net_output:
                max_net_output = net_output
                best_route = route

        return best_route, max_net_output

    def compare_routes(
        self,
        token_in: Token,
        token_out: Token,
        amount_in: int,
        gas_price_gwei: int,
        max_hops: int = 3,
    ) -> List[dict]:
        """Provides analytical breakdown of all possible routes."""
        all_routes = self.find_all_routes(token_in, token_out, max_hops)
        results = []

        for route in all_routes:
            gross_output = route.get_output(amount_in)
            gas_estimate = route.estimate_gas()
            gas_cost_wei = gas_estimate * gas_price_gwei * (10**9)

            if token_out.address.lower == _WETH_ADDR.lower:
                gas_cost_token = gas_cost_wei
            else:
                gas_cost_token = 0

            net_output = max(0, gross_output - gas_cost_token)

            results.append(
                {
                    "route": route,
                    "path": str(route),
                    "hops": route.num_hops,
                    "gross_output": gross_output,
                    "gas_estimate": gas_estimate,
                    "gas_cost_token": gas_cost_token,
                    "net_output": net_output,
                }
            )

        # Сортуємо від найприбутковішого до найменш прибуткового
        results.sort(key=lambda x: x["net_output"], reverse=True)
        return results
