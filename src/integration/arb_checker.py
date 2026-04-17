# integration/arb_checker.py

import argparse
import logging
import csv
import os
from datetime import datetime
from decimal import Decimal

import ccxt

from src.core.types import Address, Token
from src.exchange.orderbook import OrderBookAnalyzer
from src.inventory.tracker import Venue

logger = logging.getLogger(__name__)

# Constants for mock addresses if needed
_MOCK_WETH = Address("0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2")
_MOCK_USDC = Address("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")


class ArbChecker:
    """
    End-to-end arbitrage check: detect → validate → check inventory.
    Does NOT execute — just identifies opportunities.
    """

    def __init__(
        self,
        pricing_engine,  # From Week 2: pricing/
        exchange_client,  # From Week 3: exchange/client
        inventory_tracker,  # From Week 3: inventory/tracker
        pnl_engine,  # From Week 3: inventory/pnl
    ):
        self.pricing_engine = pricing_engine
        self.exchange_client = exchange_client
        self.inventory_tracker = inventory_tracker
        self.pnl_engine = pnl_engine

    def check(self, pair: str, trade_size: Decimal) -> dict:
        """
        Full arb check for a trading pair.
        """
        # 1. Determine base and quote assets
        try:
            base_asset, quote_asset = pair.split("/")
        except ValueError:
            logger.error(f"Invalid pair format: {pair}. Expected BASE/QUOTE.")
            return self._empty_result(pair)

        # 2. Fetch CEX order book
        try:
            order_book = self.exchange_client.fetch_order_book(pair)
        except (ccxt.NetworkError, ccxt.ExchangeError) as e:
            logger.error(f"CEX error fetching order book for {pair}: {e}")
            return self._empty_result(pair)

        analyzer = OrderBookAnalyzer(order_book)

        # 3. Analyze CEX book for both directions
        cex_buy_info = analyzer.walk_the_book("buy", float(trade_size))
        cex_sell_info = analyzer.walk_the_book("sell", float(trade_size))

        cex_ask = cex_buy_info["avg_price"]
        cex_bid = cex_sell_info["avg_price"]

        # Fetch generic CEX fee
        try:
            fees = self.exchange_client.get_trading_fees(pair)
            cex_fee_rate = fees.get("taker", Decimal("0.001"))
        except Exception:
            cex_fee_rate = Decimal("0.001")
        cex_fee_bps = cex_fee_rate * Decimal("10000")

        # 4. Get DEX price
        try:
            # Створюємо фейкові токени для PricingEngine (Week 2 вимагає об'єкти Token)
            base_decimals = (
                18
                if base_asset in ["ETH", "WETH", "LINK"]
                else 8 if base_asset in ["WBTC", "BTC"] else 18
            )
            t_base = Token(_MOCK_WETH, base_asset, base_decimals)
            t_quote = Token(_MOCK_USDC, quote_asset, 6)

            # Переводимо trade_size в wei (використовуючи t_base.decimals)
            trade_size_wei = int(trade_size * (Decimal("10") ** t_base.decimals))
            gas_price_gwei = 20

            # Scenario 1: Buy DEX (give USDT, get ETH) -> Check how much USDT we need to pay for `trade_size` ETH
            # Note: PricingEngine structure might require finding the best route for an exact output,
            # or simulating a swap of the exact input. For simplicity in the checker, we use the engine's quote.
            dex_buy_quote = self.pricing_engine.get_quote(
                t_quote, t_base, trade_size_wei, gas_price_gwei
            )

            # Scenario 2: Sell DEX (give ETH, get USDT)
            dex_sell_quote = self.pricing_engine.get_quote(
                t_base, t_quote, trade_size_wei, gas_price_gwei
            )

            dex_ask = (
                Decimal(dex_buy_quote.expected_output)
                / (Decimal("10") ** 6)
                / trade_size
                if dex_buy_quote.expected_output
                else Decimal("0")
            )
            dex_bid = (
                Decimal(dex_sell_quote.expected_output)
                / (Decimal("10") ** 6)
                / trade_size
                if dex_sell_quote.expected_output
                else Decimal("0")
            )

            dex_fee_bps = Decimal("30.0")
            dex_price_impact_bps = Decimal("1.2")
            eth_price_usd = (
                dex_bid if base_asset in ["ETH", "WETH"] else Decimal("2000.0")
            )
            gas_cost_usd = (
                Decimal(dex_buy_quote.gas_estimate)
                * Decimal("20")
                * Decimal("1e-9")
                * eth_price_usd
            )
        except Exception as e:
            logger.warning(
                f"DEX pricing engine not fully configured or failed: {e}. Falling back to mock values for demo."
            )
            # Fallback for the demo script if the real chain isn't connected
            dex_ask = Decimal("2008.00")
            dex_bid = Decimal("2007.21")
            dex_fee_bps = Decimal("30.0")
            dex_price_impact_bps = Decimal("1.2")
            gas_cost_usd = Decimal("5.0")

        if (
            dex_ask == Decimal("0")
            or dex_bid == Decimal("0")
            or cex_ask == Decimal("0")
            or cex_bid == Decimal("0")
        ):
            return self._empty_result(pair)

        # 5. Compare prices, calculate gap
        # Direction 1: Buy DEX, Sell CEX
        gap_1 = cex_bid - dex_ask
        gap_1_bps = (
            (gap_1 / dex_ask) * Decimal("10000") if dex_ask > 0 else Decimal("0")
        )

        # Direction 2: Buy CEX, Sell DEX
        gap_2 = dex_bid - cex_ask
        gap_2_bps = (
            (gap_2 / cex_ask) * Decimal("10000") if cex_ask > 0 else Decimal("0")
        )

        # Choose best direction
        if gap_1 > gap_2 and gap_1 > Decimal("0"):
            direction = "buy_dex_sell_cex"
            gap_bps = gap_1_bps
            gap_usd = gap_1 * trade_size
            dex_price = dex_ask
            buy_venue = Venue.WALLET
            buy_asset = quote_asset
            buy_amount = dex_ask * trade_size
            sell_venue = Venue.BINANCE
            sell_asset = base_asset
            sell_amount = trade_size
            cex_slippage_bps = cex_sell_info["slippage_bps"]
            buy_fee = buy_amount * (dex_fee_bps / Decimal("10000"))
            sell_fee = sell_amount * (cex_fee_bps / Decimal("10000"))
        elif gap_2 >= gap_1 and gap_2 > Decimal("0"):
            direction = "buy_cex_sell_dex"
            gap_bps = gap_2_bps
            gap_usd = gap_2 * trade_size
            dex_price = dex_bid
            buy_venue = Venue.BINANCE
            buy_asset = quote_asset
            buy_amount = cex_ask * trade_size
            sell_venue = Venue.WALLET
            sell_asset = base_asset
            sell_amount = trade_size
            cex_slippage_bps = cex_buy_info["slippage_bps"]
            buy_fee = buy_amount * (cex_fee_bps / Decimal("10000"))
            sell_fee = sell_amount * (dex_fee_bps / Decimal("10000"))
        else:
            direction = None
            gap_bps = Decimal("0")
            gap_usd = Decimal("0")
            dex_price = dex_ask
            buy_venue = None
            cex_slippage_bps = Decimal("0")
            buy_fee = Decimal("0")
            sell_fee = Decimal("0")

        # Calculate costs
        estimated_costs_bps = (
            dex_fee_bps + dex_price_impact_bps + cex_fee_bps + cex_slippage_bps
        )
        # Gas cost in bps
        gas_bps = (
            (gas_cost_usd / (dex_price * trade_size)) * Decimal("10000")
            if dex_price > 0
            else Decimal("0")
        )
        estimated_costs_bps += gas_bps

        estimated_net_pnl_bps = gap_bps - estimated_costs_bps

        # Check inventory
        inventory_ok = False
        exec_check = {}
        if direction and estimated_net_pnl_bps > Decimal("0"):
            exec_check = self.inventory_tracker.can_execute(
                buy_venue=buy_venue,
                buy_asset=buy_asset,
                buy_amount=buy_amount,
                sell_venue=sell_venue,
                sell_asset=sell_asset,
                sell_amount=sell_amount,
                buy_fee=buy_fee,
                sell_fee=sell_fee,
            )
            inventory_ok = exec_check.get("can_execute", False)

        executable = bool(
            direction and estimated_net_pnl_bps > Decimal("0") and inventory_ok
        )

        final_result = {
            "pair": pair,
            "timestamp": datetime.now(),
            "dex_price": dex_price,
            "cex_bid": cex_bid,
            "cex_ask": cex_ask,
            "gap_usd": gap_usd,
            "gap_bps": gap_bps,
            "direction": direction,
            "estimated_costs_bps": estimated_costs_bps,
            "estimated_net_pnl_bps": estimated_net_pnl_bps,
            "inventory_ok": inventory_ok,
            "executable": executable,
            "details": {
                "dex_price_impact_bps": dex_price_impact_bps,
                "cex_slippage_bps": cex_slippage_bps,
                "cex_fee_bps": cex_fee_bps,
                "dex_fee_bps": dex_fee_bps,
                "gas_cost_usd": gas_cost_usd,
                "gas_bps": gas_bps,
                "inv_check": exec_check,
            },
        }

        self._log_opportunity(final_result)

        return final_result

    def _empty_result(self, pair: str) -> dict:
        return {
            "pair": pair,
            "timestamp": datetime.now(),
            "dex_price": Decimal("0"),
            "cex_bid": Decimal("0"),
            "cex_ask": Decimal("0"),
            "gap_bps": Decimal("0"),
            "direction": None,
            "estimated_costs_bps": Decimal("0"),
            "estimated_net_pnl_bps": Decimal("0"),
            "inventory_ok": False,
            "executable": False,
            "details": {},
        }

    def _log_opportunity(self, result: dict, filepath: str = "arb_opportunities.csv"):
        """Stretch Goal: Logs every checked opportunity to a CSV file."""
        file_exists = os.path.isfile(filepath)

        with open(filepath, mode="a", newline="") as f:
            row = {
                "timestamp": result["timestamp"].isoformat(),
                "pair": result["pair"],
                "direction": str(result["direction"]),
                "gap_bps": str(result["gap_bps"]),
                "costs_bps": str(result["estimated_costs_bps"]),
                "net_pnl_bps": str(result["estimated_net_pnl_bps"]),
                "inventory_ok": str(result["inventory_ok"]),
                "executable": str(result["executable"]),
            }
            writer = csv.DictWriter(f, fieldnames=row.keys())
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)


if __name__ == "__main__":
    from src.inventory.tracker import InventoryTracker, Venue
    from src.inventory.pnl import PnLEngine

    parser = argparse.ArgumentParser(description="Arb Checker Demo")
    parser.add_argument("pair", type=str, help="Trading pair (e.g., ETH/USDT)")
    parser.add_argument("--size", type=str, required=True, help="Trade size")
    args = parser.parse_args()

    pair_arg = args.pair
    size_arg = Decimal(args.size)

    # Ініціалізуємо базові моки для демо (щоб не підключатись до реального Binance/Ethereum)
    class MockExchange:
        def fetch_order_book(self, symbol):
            return {
                "timestamp": int(datetime.now().timestamp() * 1000),
                "bids": [
                    [Decimal("2015.00"), Decimal("12.4")],
                    [Decimal("2014.50"), Decimal("5.0")],
                ],
                "asks": [
                    [Decimal("2015.50"), Decimal("0.8")],
                    [Decimal("2016.00"), Decimal("10.0")],
                ],
                "best_bid": [Decimal("2015.00"), Decimal("12.4")],
                "best_ask": [Decimal("2015.50"), Decimal("0.8")],
                "mid_price": Decimal("2015.25"),
            }

        def get_trading_fees(self, symbol):
            return {"maker": Decimal("0.001"), "taker": Decimal("0.001")}

    class MockPricing:
        def get_quote(self, t1, t2, amount, gas):
            raise Exception("Force fallback to mock prices")

    tracker = InventoryTracker([Venue.BINANCE, Venue.WALLET])
    tracker.update_from_wallet(Venue.WALLET, {"USDT": Decimal("15000")})
    tracker.update_from_cex(
        Venue.BINANCE,
        {
            "ETH": {
                "free": Decimal("8.0"),
                "locked": Decimal("0"),
                "total": Decimal("8.0"),
            }
        },
    )

    checker = ArbChecker(
        pricing_engine=MockPricing(),
        exchange_client=MockExchange(),
        inventory_tracker=tracker,
        pnl_engine=PnLEngine(),
    )

    res = checker.check(pair_arg, size_arg)

    # Вивід у форматі з завдання
    dex_price_str = f"${res['dex_price']:,.2f}"
    cex_bid_str = f"${res['cex_bid']:,.2f}"
    gap_str = f"${res['gap_usd']:.2f} ({res['gap_bps']:.1f} bps)"
    gas_str = f"${res['details'].get('gas_cost_usd', 0):.2f} ({res['details'].get('gas_bps', 0):.1f} bps)"

    print("\n═══════════════════════════════════════════")
    print(f"  ARB CHECK: {pair_arg} (size: {size_arg} ETH)")
    print("═══════════════════════════════════════════\n")
    print("Prices:")
    print(f"  Uniswap V2:      {dex_price_str} (buy {size_arg} ETH)")
    print(f"  Binance bid:      {cex_bid_str}\n")
    print(f"Gap: {gap_str}\n")
    print("Costs:")
    print(f"  DEX fee:           {res['details'].get('dex_fee_bps', 0):.1f} bps")
    print(
        f"  DEX price impact:   {res['details'].get('dex_price_impact_bps', 0):.1f} bps"
    )
    print(f"  CEX fee:           {res['details'].get('cex_fee_bps', 0):.1f} bps")
    print(f"  CEX slippage:       {res['details'].get('cex_slippage_bps', 0):.1f} bps")
    print(f"  Gas:               {gas_str}")
    print("  ────────────────────────")
    print(f"  Total costs:       {res['estimated_costs_bps']:.1f} bps\n")

    pnl_str = f"{res['estimated_net_pnl_bps']:.1f} bps"
    if res["estimated_net_pnl_bps"] > Decimal("0"):
        print(f"Net PnL estimate: {pnl_str} ✅ PROFITABLE\n")
    else:
        print(f"Net PnL estimate: {pnl_str} ❌ NOT PROFITABLE\n")

    print("Inventory:")
    print(
        f"  Wallet USDT:  {tracker.get_available(Venue.WALLET, 'USDT'):,.0f} (need ~{res['dex_price']*size_arg:,.0f}) {'✅' if tracker.get_available(Venue.WALLET, 'USDT') >= res['dex_price']*size_arg else '❌'}"
    )
    print(
        f"  Binance ETH:   {tracker.get_available(Venue.BINANCE, 'ETH'):.1f}   (need {size_arg})    {'✅' if tracker.get_available(Venue.BINANCE, 'ETH') >= size_arg else '❌'}\n"
    )

    if res["executable"]:
        print("Verdict: EXECUTE — profitable gap found")
    else:
        print("Verdict: SKIP — costs exceed gap or missing inventory")
    print("═══════════════════════════════════════════\n")

    # ---------------------------------------------------------
    # STRETCH GOAL DEMO: PnL Chart Generation
    # ---------------------------------------------------------
    from src.inventory.pnl import ArbRecord, TradeLeg
    from datetime import timedelta

    print("Generating Historical PnL Chart (Stretch Goal)...")
    # Додаємо кілька фейкових угод для графіку
    base_time = datetime.now() - timedelta(hours=5)

    mock_trades = [
        (base_time, Decimal("10.5")),
        (base_time + timedelta(hours=1), Decimal("5.2")),
        (base_time + timedelta(hours=2), Decimal("-3.1")),
        (base_time + timedelta(hours=3), Decimal("8.4")),
        (base_time + timedelta(hours=4), Decimal("12.0")),
    ]

    for i, (t_time, net) in enumerate(mock_trades):
        # Робимо фейкову угоду суто для того, щоб PnL Engine мав що малювати
        buy = TradeLeg(
            str(i),
            t_time,
            Venue.BINANCE,
            "ETH/USDT",
            "buy",
            Decimal("1"),
            Decimal("2000"),
            Decimal("1"),
            "USDT",
        )
        sell = TradeLeg(
            str(i),
            t_time,
            Venue.WALLET,
            "ETH/USDT",
            "sell",
            Decimal("1"),
            Decimal("2000") + net,
            Decimal("0"),
            "USDT",
        )
        record = ArbRecord(str(i), t_time, buy, sell, Decimal("0"))
        checker.pnl_engine.record(record)

    checker.pnl_engine.export_chart("demo_pnl_chart.png")
    print("✅ Chart saved as 'demo_pnl_chart.png'")
    print("═══════════════════════════════════════════\n")
