import logging
from web3 import Web3
from src.core.types import Token

logger = logging.getLogger("V3Quoter")

# Офіційна адреса QuoterV2 на Arbitrum
ARBITRUM_QUOTER_V2 = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"

QUOTER_V2_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "tokenIn", "type": "address"},
                    {"internalType": "address", "name": "tokenOut", "type": "address"},
                    {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                    {"internalType": "uint24", "name": "fee", "type": "uint24"},
                    {
                        "internalType": "uint160",
                        "name": "sqrtPriceLimitX96",
                        "type": "uint160",
                    },
                ],
                "internalType": "struct IQuoterV2.QuoteExactInputSingleParams",
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "quoteExactInputSingle",
        "outputs": [
            {"internalType": "uint256", "name": "amountOut", "type": "uint256"},
            {"internalType": "uint160", "name": "sqrtPriceX96After", "type": "uint160"},
            {
                "internalType": "uint32",
                "name": "initializedTicksCrossed",
                "type": "uint32",
            },
            {"internalType": "uint256", "name": "gasEstimate", "type": "uint256"},
        ],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]


class UniswapV3Pricer:
    def __init__(self, w3: Web3):
        self.w3 = w3
        self.quoter = self.w3.eth.contract(
            address=self.w3.to_checksum_address(ARBITRUM_QUOTER_V2),
            abi=QUOTER_V2_ABI,
        )

    def get_amount_out(
        self, token_in: Token, token_out: Token, amount_in: int, fee_tier: int
    ) -> int:
        try:
            params = (
                token_in.address.checksum,
                token_out.address.checksum,
                amount_in,
                fee_tier,
                0,
            )
            result = self.quoter.functions.quoteExactInputSingle(params).call()
            return int(result[0])
        except Exception as e:
            logger.error(f"V3 Quoter error: {e}")
            return 0
