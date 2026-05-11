from decimal import Decimal

import ccxt

from src.config.config import Settings

STABLE_ASSETS = {"USDT", "USDC", "DAI", "FDUSD"}
MIN_SELL_QTY = Decimal("0.0001")


def _confirm() -> bool:
    answer = (
        input("Are you sure you want to market sell all assets to USDT? (y/n): ")
        .strip()
        .lower()
    )
    return answer == "y"


def main() -> None:
    if not _confirm():
        print("Aborted.")
        return

    settings = Settings()
    config = settings.binance_config
    exchange = ccxt.binance(config)
    if config.get("sandbox"):
        exchange.set_sandbox_mode(True)
        print("WARNING: Sandbox mode enabled. Set ENVIRONMENT=prod for live trading.")

    exchange.load_markets()
    balances = exchange.fetch_balance()

    for asset, data in balances.items():
        if asset in {"info", "free", "used", "total"}:
            continue
        if not isinstance(data, dict) or "free" not in data:
            continue
        if asset.upper() in STABLE_ASSETS:
            continue

        free_amount = Decimal(str(data.get("free", 0) or 0))
        if free_amount < MIN_SELL_QTY:
            continue

        symbol = f"{asset}/USDT"
        if symbol not in exchange.markets:
            print(f"Skipping {asset}: no USDT market")
            continue

        try:
            order = exchange.create_market_order(
                symbol=symbol,
                side="sell",
                amount=str(free_amount),
            )
            print(f"Sold {free_amount} {asset} -> USDT (order: {order.get('id')})")
        except Exception as exc:
            print(f"Failed to sell {asset}: {exc}")


if __name__ == "__main__":
    main()
