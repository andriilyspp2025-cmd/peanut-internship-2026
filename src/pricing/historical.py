import logging
from typing import Dict, List, Any

from src.chain.client import ChainClient
from src.core.types import Address, Token
from src.pricing.amm import UniswapV2Pair

log = logging.getLogger(__name__)


class HistoricalAnalyzer:
    """Standalone analyzer for evaluating historical price impacts over specified blocks."""

    def __init__(self, client: ChainClient):
        self.client = client

    async def analyze_historical_impact(
        self,
        pair_address: Address,
        token_in: Token,
        token_out: Token,
        amount_in: int,
        blocks: List[int],
    ) -> Dict[int, Dict[str, Any]]:
        """
        Calculates the historical price impact and spot price of a theoretical trade
        across a series of specific block heights in the past.
        """
        results = {}

        # Uniswap V2 pairs strictly sort tokens hexadecimally
        if token_in.address.checksum.lower() < token_out.address.checksum.lower():
            token0, token1 = token_in, token_out
        else:
            token0, token1 = token_out, token_in

        for block in blocks:
            try:
                # 0x0902f1ac specifies getReserves()
                reserves_bytes = self.client.w3.eth.call(
                    {"to": pair_address.checksum, "data": "0x0902f1ac"},
                    block_identifier=block,
                )

                if not reserves_bytes or len(reserves_bytes) < 64:
                    continue

                reserve0 = int.from_bytes(reserves_bytes[0:32], "big")
                reserve1 = int.from_bytes(reserves_bytes[32:64], "big")

                if reserve0 <= 0 or reserve1 <= 0:
                    continue

                # Transient in-memory instantiation for state playback
                temp_pair = UniswapV2Pair(
                    address=pair_address,
                    token0=token0,
                    token1=token1,
                    reserve0=reserve0,
                    reserve1=reserve1,
                )

                impact = temp_pair.get_price_impact(amount_in, token_in)
                spot_price = temp_pair.get_spot_price(token_in)

                results[block] = {"price_impact_pct": impact, "spot_price": spot_price}

            except Exception as e:
                # Typically caused by missing archive state or if the pool hadn't deployed yet
                log.debug(f"Failed historical analysis for block {block}: {str(e)}")
                continue

        return results
