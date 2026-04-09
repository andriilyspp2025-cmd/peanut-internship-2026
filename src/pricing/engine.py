import time
import logging
from decimal import Decimal
from typing import Optional, List, Dict
from dataclasses import dataclass
from src.core.types import Address, Token
from src.chain.client import ChainClient
from src.pricing.amm import UniswapV2Pair
from src.pricing.router import RouteFinder, Route
from src.pricing.simulator import ForkSimulator
from src.pricing.mempool import MempoolMonitor, ParsedSwap

log = logging.getLogger(__name__)


class QuoteError(Exception):
    """Raised when a price quote cannot be produced (e.g., simulation fails, no route)."""

    pass


@dataclass
class Quote:
    route: Route
    amount_in: int
    expected_output: int
    simulated_output: int
    gas_estimate: int
    timestamp: float

    @property
    def is_valid(self) -> bool:
        """Quote valid if simulation matches expectation within tolerance."""
        if self.expected_output == 0:
            return self.simulated_output == 0

        tolerance = Decimal("0.001")
        diff = Decimal(abs(self.expected_output - self.simulated_output))
        return (diff / Decimal(self.expected_output)) < tolerance


class PricingEngine:
    """Manages AMM prices, routing, simulation, and mempool monitoring."""

    def __init__(self, chain_client: ChainClient, fork_url: str, ws_url: str):
        self.client = chain_client
        self.simulator = ForkSimulator(fork_url)
        self.monitor = MempoolMonitor(ws_url, self._on_mempool_swap)
        self.pools: Dict[Address, UniswapV2Pair] = {}
        self.router: Optional[RouteFinder] = None

    def load_pools(self, pool_addresses: List[Address]):
        """Populates pool reserves from chain and initializes the router."""
        log.info(f"Loading {len(pool_addresses)} pools from chain")
        for addr in pool_addresses:
            self.pools[addr] = UniswapV2Pair.from_chain(addr, self.client)
        self.router = RouteFinder(list(self.pools.values()))
        log.info("Pools loaded and RouteFinder initialized.")

    def refresh_pool(self, address: Address):
        """Refreshes a single pool's state."""
        if address in self.pools:
            log.info(f"Refreshing reserves for pool {address.checksum}")
            self.pools[address] = UniswapV2Pair.from_chain(address, self.client)
            self.router = RouteFinder(list(self.pools.values()))

    def get_quote(
        self,
        token_in: Token,
        token_out: Token,
        amount_in: int,
        gas_price_gwei: int,
        sender: Address = None,
    ) -> Quote:
        """Finds the best route and simulates execution."""
        if not self.router:
            raise QuoteError("Router is not initialized. Call load_pools() first.")

        route, net_output = self.router.find_best_route(
            token_in, token_out, amount_in, gas_price_gwei
        )

        if not route or net_output == 0:
            raise QuoteError("No profitable route available.")

        effective_sender = sender or Address(
            "0x0000000000000000000000000000000000000000"
        )

        # --- PRE-FLIGHT CHECKS ---
        my_balance = self.client.get_balance(effective_sender, token_in)
        balance_amount = my_balance.raw if hasattr(my_balance, "raw") else my_balance
        if balance_amount < amount_in:
            raise QuoteError("Insufficient balance")

        allowance = self.client.get_allowance(
            effective_sender, route.pools[0].address, token_in
        )
        allowance_amount = allowance.raw if hasattr(allowance, "raw") else allowance
        if allowance_amount < amount_in:
            log.warning("Approve required before swap")

        sim_result = self.simulator.simulate_route(
            route, amount_in, sender=effective_sender
        )

        if not sim_result.success:
            raise QuoteError(f"Simulation failed: {sim_result.error}")

        return Quote(
            route=route,
            amount_in=amount_in,
            expected_output=route.get_output(amount_in),
            simulated_output=sim_result.amount_out,
            gas_estimate=sim_result.gas_used,
            timestamp=time.time(),
        )

    def _on_mempool_swap(self, swap: ParsedSwap):
        """Callback for incoming mempool swaps."""
        log.info(f"Processing mempool swap: {swap.amount_in}")
        pass

    async def start_monitoring(self):
        """Connects and listens to the mempool stream."""
        await self.monitor.start_listening()
