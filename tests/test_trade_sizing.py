import logging
from decimal import Decimal

from src.chain.client import ChainClient
from src.config.config import config as app_config
from src.exchange.client import ExchangeClient
from src.strategy.fees import FeeStructure

logger = logging.getLogger("TradeSizingTest")

PAIRS = ["ETH/USDT", "GMX/USDT", "CHIP/USDT"]
TRADE_SIZES_USD = [Decimal("5"), Decimal("10"), Decimal("25")]
DEFAULT_GAS_UNITS_V3_SWAP = 220000
FALLBACK_PRICES = {
    "ETH/USDT": Decimal("2000"),
    "GMX/USDT": Decimal("30"),
    "CHIP/USDT": Decimal("0.05"),
}


def _format_decimal(value: Decimal, places: int) -> str:
    """Format a Decimal with fixed decimal places."""
    return format(value, f".{places}f")


def _get_price_usd(exchange: ExchangeClient, pair: str) -> Decimal:
    """Fetch best-ask price from CEX, falling back to static defaults."""
    try:
        order_book = exchange.fetch_order_book(pair)
        return order_book["best_ask"][0]
    except Exception as exc:
        logger.warning("Price fetch failed for %s: %s; using fallback", pair, exc)
        return FALLBACK_PRICES[pair]


def _estimate_gas_cost_usd(
    chain_client: ChainClient,
    eth_price_usd: Decimal,
    fees: FeeStructure,
) -> Decimal:
    """Estimate gas cost in USD using chain data with a safe fallback."""
    try:
        return chain_client.estimate_gas_cost_usd(
            DEFAULT_GAS_UNITS_V3_SWAP,
            eth_price_usd,
            buffer_bps=app_config.GAS_BUFFER_BPS,
        )
    except Exception as exc:
        logger.warning("Gas estimate failed: %s; using fallback", exc)
        return fees.gas_cost_usd


def main() -> int:
    """Run a simple USD sizing check for configured pairs."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    exchange = ExchangeClient(app_config.binance_config)
    chain_client = ChainClient([app_config.RPC_URL], simulation_mode=True)
    fees = FeeStructure.from_config({})

    eth_price_usd = _get_price_usd(exchange, "ETH/USDT")
    gas_cost_usd = _estimate_gas_cost_usd(chain_client, eth_price_usd, fees)

    print("=== Trade Sizing Check (USD -> Tokens) ===")
    print(f"Max trade USD: {app_config.MAX_TRADE_USD}")
    print(f"Gas cost estimate: ${_format_decimal(gas_cost_usd, 4)}")

    for pair in PAIRS:
        price_usd = _get_price_usd(exchange, pair)
        print(f"\nPair {pair} | price=${_format_decimal(price_usd, 6)}")

        for trade_usd in TRADE_SIZES_USD:
            token_amount = trade_usd / price_usd
            gas_pct = (gas_cost_usd / trade_usd) * Decimal("100")
            within_max = trade_usd <= app_config.MAX_TRADE_USD
            status = "OK" if within_max else "EXCEEDS_MAX"

            print(
                "  size_usd=$%s -> tokens=%s | gas=%s%% | %s"
                % (
                    _format_decimal(trade_usd, 2),
                    _format_decimal(token_amount, 8),
                    _format_decimal(gas_pct, 2),
                    status,
                )
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
