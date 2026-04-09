import argparse
from decimal import Decimal
from src.core.types import Token, Address
from src.chain.client import ChainClient
from src.pricing.amm import UniswapV2Pair
from src.pricing.errors import InsufficientLiquidityError


_WETH_ADDR = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"


class PriceImpactAnalyzer:
    """Evaluates the execution price impact of trades across different sizes."""

    def __init__(self, pair: UniswapV2Pair):
        self.pair = pair

    def generate_impact_table(self, token_in: Token, sizes: list[int]) -> list[dict]:
        table_data = []
        for amount_in in sizes:
            try:
                amount_out = self.pair.get_amount_out(amount_in, token_in)
                spot_price = self.pair.get_spot_price(token_in)
                exec_price = self.pair.get_execution_price(amount_in, token_in)
                impact = self.pair.get_price_impact(amount_in, token_in)

                table_data.append(
                    {
                        "amount_in": amount_in,
                        "amount_out": amount_out,
                        "spot_price": spot_price,
                        "execution_price": exec_price,
                        "price_impact_pct": impact,
                    }
                )
            except Exception:
                continue
        return table_data

    def find_max_size_for_impact(self, token_in: Token, max_impact_pct: Decimal) -> int:
        """Finds the maximum trade size permitted by a set maximum price impact."""
        if max_impact_pct <= 0:
            raise ValueError("max_impact_pct must be positive.")

        if token_in == self.pair.token0:
            high = self.pair.reserve0
        elif token_in == self.pair.token1:
            high = self.pair.reserve1
        else:
            raise ValueError("Token not in pair")

        max_impact_frac = max_impact_pct / Decimal(100)

        low = 1
        best_size = 0

        while low <= high:
            mid = (high + low) // 2
            try:
                impact = self.pair.get_price_impact(mid, token_in)
                if impact <= max_impact_frac:
                    best_size = mid
                    low = mid + 1
                else:
                    high = mid - 1
            except InsufficientLiquidityError:
                high = mid - 1
            except Exception as e:
                raise e

        return best_size

    def estimate_true_cost(
        self,
        amount_in: int,
        token_in: Token,
        gas_price_gwei: int,
        gas_estimate: int = 150000,
    ) -> dict:
        """Estimates total cost of transaction execution, including gas usage factored into inputs."""
        gross_output = self.pair.get_amount_out(amount_in, token_in)
        gas_cost_eth = gas_estimate * gas_price_gwei * (10**9)

        token_out = (
            self.pair.token1 if token_in == self.pair.token0 else self.pair.token0
        )

        # Gas Routing
        if token_out.address.lower == _WETH_ADDR:
            gas_cost_in_output_token = gas_cost_eth
        elif token_in.address.lower == _WETH_ADDR:
            try:
                if gas_cost_eth == 0:
                    gas_cost_in_output_token = 0
                else:
                    gas_cost_in_output_token = self.pair.get_amount_out(
                        gas_cost_eth, token_in
                    )
            except InsufficientLiquidityError:
                gas_cost_in_output_token = 0
        else:
            gas_cost_in_output_token = 0

        net_output = gross_output - gas_cost_in_output_token
        if net_output < 0:
            net_output = 0

        effective_price = (
            Decimal(net_output) / Decimal(amount_in) if amount_in > 0 else Decimal(0)
        )

        return {
            "gross_output": gross_output,
            "gas_cost_eth": gas_cost_eth,
            "gas_cost_in_output_token": gas_cost_in_output_token,
            "net_output": net_output,
            "effective_price": effective_price,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Analyze AMM price impact across trade sizes."
    )
    parser.add_argument(
        "pair_address", help="The address of the targeted Uniswap V2 pair"
    )
    parser.add_argument(
        "--token-in", required=True, help="Address or Symbol of the input token"
    )
    parser.add_argument(
        "--sizes", required=True, help="Comma-separated list of integer input sizes"
    )
    parser.add_argument(
        "--rpc",
        default="",
        help="Optional RPC endpoint URL (defaults to empty/local config)",
    )

    args = parser.parse_args()

    client = ChainClient(args.rpc) if args.rpc else ChainClient()
    pair_address = Address(args.pair_address)
    pair = UniswapV2Pair.from_chain(pair_address, client)

    analyzer = PriceImpactAnalyzer(pair)

    # 2. Parse the sizes argument
    sizes_list = [int(s.strip()) for s in args.sizes.split(",")]

    # Identify token_in
    if (
        args.token_in.lower() == pair.token0.address.checksum.lower()
        or args.token_in.lower() == pair.token0.symbol.lower()
    ):
        token_in = pair.token0
    elif (
        args.token_in.lower() == pair.token1.address.checksum.lower()
        or args.token_in.lower() == pair.token1.symbol.lower()
    ):
        token_in = pair.token1
    else:
        raise ValueError(
            f"Token '{args.token_in}' is not recognized in pair {pair_address.checksum}"
        )

    # 3. Run analyses
    impact_table = analyzer.generate_impact_table(token_in, sizes_list)
    max_impact_size = analyzer.find_max_size_for_impact(token_in, Decimal("1.0"))

    # 4. Print results
    print("\n" + "=" * 80)
    print(f"Price Impact Analysis for Pair: {pair.address.checksum}")
    print(f"Input Token: {token_in.symbol} ({token_in.address.checksum})")
    print("=" * 80)

    print(f"{'USDC In':<20} | {'ETH Out':<20} | {'Exec Price':<20} | {'Impact':<10}")
    print("-" * 80)

    for row in impact_table:
        impact_str = f"{row['price_impact_pct'] * 100:.4f}%"
        exec_price_str = f"{row['execution_price']:.8f}"
        print(
            f"{row['amount_in']:<20} | {row['amount_out']:<20} | {exec_price_str:<20} | {impact_str:<10}"
        )

    print("-" * 80)
    print(
        f"Maximum trade size for <= 1.0% impact: {max_impact_size} {token_in.symbol}\n"
    )
