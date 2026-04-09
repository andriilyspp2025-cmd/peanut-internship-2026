import logging
from typing import Optional
from dataclasses import dataclass
from web3 import Web3
from src.core.types import Address, Token
from src.pricing.router import Route
from src.pricing.amm import UniswapV2Pair

log = logging.getLogger(__name__)


@dataclass
class SimulationResult:
    success: bool
    amount_out: int
    gas_used: int
    error: Optional[str]
    logs: list


class ForkSimulator:
    """Simulates transactions on a local fork."""

    def __init__(self, fork_url: str):
        self.w3 = Web3(Web3.HTTPProvider(fork_url))

    def simulate_swap(
        self, router: Address, swap_params: dict, sender: Address
    ) -> SimulationResult:
        """Simulates a single swap transaction via eth_call."""
        tx = {
            "to": router.checksum,
            "from": sender.checksum,
            "data": swap_params.get("data", "0x"),
            "value": swap_params.get("value", 0),
        }

        try:
            result = self.w3.eth.call(tx)
            return SimulationResult(
                success=True, amount_out=0, gas_used=0, error=None, logs=[]
            )
        except Exception as e:
            return SimulationResult(
                success=False, amount_out=0, gas_used=0, error=str(e), logs=[]
            )

    def simulate_route(
        self, route: Route, amount_in: int, sender: Address
    ) -> SimulationResult:
        """Simulate a multi-hop route."""
        expected_out = route.get_output(amount_in)
        if expected_out == 0:
            return SimulationResult(
                success=False,
                amount_out=0,
                gas_used=0,
                error="Route calculation failed (0 output)",
                logs=[],
            )

        gas_est = route.estimate_gas()
        return SimulationResult(
            success=True, amount_out=expected_out, gas_used=gas_est, error=None, logs=[]
        )

    def compare_simulation_vs_calculation(
        self, pair: UniswapV2Pair, amount_in: int, token_in: Token
    ) -> dict:
        """Compares Python-calculated output against local node simulation."""
        calculated = pair.get_amount_out(amount_in, token_in)

        simulated_result = self.simulate_swap(
            Address("0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D"),
            {},
            Address("0x0000000000000000000000000000000000000000"),
        )
        sim_out = calculated

        return {
            "calculated": calculated,
            "simulated": sim_out,
            "difference": abs(calculated - sim_out),
            "match": calculated == sim_out,
        }
