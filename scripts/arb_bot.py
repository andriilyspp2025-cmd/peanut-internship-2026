import argparse
import asyncio
import os
import logging
from decimal import Decimal
from datetime import datetime

from src.exchange.client import ExchangeClient
from src.inventory.tracker import InventoryTracker, Venue
from src.inventory.pnl import PnLEngine, ArbRecord, TradeLeg
from src.strategy.fees import FeeStructure
from src.strategy.generator import SignalGenerator
from src.strategy.scorer import SignalScorer
from src.executor.engine import Executor, ExecutorConfig, ExecutorState
from src.config.config import config as app_config
from src.chain.client import ChainClient
from src.pricing.engine import PricingEngine
from src.core.types import Address

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ArbBot")


def execution_to_arb_record(ctx) -> ArbRecord:
    """Bridge between Week 4's ExecutionContext and Week 3's ArbRecord."""
    signal = ctx.signal
    quote_asset = signal.pair.split("/")[1]

    buy_leg = TradeLeg(
        id=f"{signal.signal_id}_buy",
        timestamp=datetime.fromtimestamp(ctx.started_at),
        venue=Venue.BINANCE if ctx.leg1_venue == "cex" else Venue.WALLET,
        symbol=signal.pair,
        side="buy",
        amount=ctx.leg1_fill_size or Decimal("0"),
        price=ctx.leg1_fill_price or Decimal("0"),
        fee=Decimal("0"),
        fee_asset=quote_asset,
    )

    sell_leg = TradeLeg(
        id=f"{signal.signal_id}_sell",
        timestamp=datetime.fromtimestamp(ctx.finished_at or ctx.started_at),
        venue=Venue.WALLET if ctx.leg2_venue == "dex" else Venue.BINANCE,
        symbol=signal.pair,
        side="sell",
        amount=ctx.leg2_fill_size or Decimal("0"),
        price=ctx.leg2_fill_price or Decimal("0"),
        fee=Decimal("0"),
        fee_asset=quote_asset,
    )

    return ArbRecord(
        id=signal.signal_id,
        timestamp=datetime.fromtimestamp(ctx.started_at),
        buy_leg=buy_leg,
        sell_leg=sell_leg,
    )


class ArbBot:
    def __init__(self, config: dict):
        self.is_test_mode = config.get("simulation", True)
        self.verbose = config.get("verbose", False)

        self.exchange = ExchangeClient(config)

        self.chain_client = ChainClient([app_config.RPC_URL])

        wss_url = None

        self.pricing_engine = PricingEngine(
            chain_client=self.chain_client, fork_url=app_config.FORK_URL, ws_url=wss_url
        )

        self.inventory = InventoryTracker([Venue.BINANCE, Venue.WALLET])
        self.pnl_engine = PnLEngine()
        self.fees = FeeStructure()

        self.generator = SignalGenerator(
            self.exchange,
            self.pricing_engine,
            self.inventory,
            self.fees,
            config.get("signal_config", {}),
        )
        self.scorer = SignalScorer()

        exec_config = ExecutorConfig(
            simulation_mode=config.get("simulation", True), use_flashbots=False
        )
        self.executor = Executor(
            self.exchange, self.pricing_engine, self.inventory, exec_config
        )

        self.pairs = config.get("pairs", ["ETH/USDT"])
        self.trade_size = Decimal(str(config.get("trade_size", "0.1")))

        raw_pools = config.get("dex_pools", [])
        self.dex_pools = [Address(p) if isinstance(p, str) else p for p in raw_pools]

        addr_str = os.getenv("ADDRESS")
        self.wallet_address = Address(addr_str) if addr_str else None

        self.running = False
        self._tick_lock = asyncio.Lock()

    async def _sync_balances(self):
        """Sync balances from both CEX and on-chain wallet."""
        try:
            # CEX Balances
            cex_balances = self.exchange.fetch_balance()
            eth_cex = cex_balances.get("ETH", {}).get("free", "0")
            usdt_cex = cex_balances.get("USDT", {}).get("free", "0")
            logger.info(f"CEX Balances | ETH: {eth_cex} | USDT: {usdt_cex}")
            self.inventory.update_from_cex(Venue.BINANCE, cex_balances)

            # DEX Balances
            if self.is_test_mode:
                # TEST MODE (Fake money for simulating transactions)
                logger.info("DEX Balances (TEST MODE) | ETH: 1.0 | USDT: 10000.0")
                self.inventory.update_from_wallet(
                    Venue.WALLET, {"ETH": Decimal("1.0"), "USDT": Decimal("10000.0")}
                )
            elif self.wallet_address and self.pricing_engine is not None:
                eth_wallet_bal = self.chain_client.get_balance(self.wallet_address)
                eth_decimal = eth_wallet_bal.human

                usdt_address = self.generator.token_map["USDT"]["address"]
                calldata = "0x70a08231" + self.wallet_address.checksum[2:].zfill(64)
                try:
                    res = self.chain_client.w3.eth.call(
                        {"to": usdt_address, "data": calldata}
                    )
                    usdt_raw = int.from_bytes(res, "big") if res else 0
                    usdt_decimal = Decimal(usdt_raw) / Decimal(10**6)
                except Exception:
                    usdt_decimal = Decimal("0")

                logger.info(
                    f"DEX Balances (PROD) | ETH: {eth_decimal} | USDT: {usdt_decimal}"
                )
                self.inventory.update_from_wallet(
                    Venue.WALLET, {"ETH": eth_decimal, "USDT": usdt_decimal}
                )
        except Exception as e:
            logger.error(f"Error syncing balances: {e}")

    async def run(self):
        self.running = True

        mode_name = (
            "SIMULATION (TEST)" if self.is_test_mode else "REAL BLOCKCHAIN (PROD)"
        )
        logger.info(f"🚀 Bot starting in {mode_name} mode...")

        if not self.is_test_mode and self.pricing_engine and self.dex_pools:
            logger.info("📡 Loading REAL DEX liquidity pools...")
            pools_loaded = False
            retry_count = 1
            max_retries = 4

            fallback_rpcs = [
                app_config.RPC_URL,
                "https://rpc.mevblocker.io",
                "https://ethereum-rpc.publicnode.com",
                "https://eth.merkle.io",
            ]

            while not pools_loaded and self.running:
                try:
                    await asyncio.sleep(2)
                    self.pricing_engine.load_pools(self.dex_pools)
                    pools_loaded = True
                    logger.info(
                        f"✅ Successfully loaded {len(self.pricing_engine.router.pools)} pools from MAINNET."
                    )
                except Exception as e:
                    if retry_count >= max_retries:
                        logger.critical(
                            f"❌ Failed to load pools after {max_retries} attempts. Stopping bot to protect funds."
                        )
                        self.running = False
                        return

                    # RPC ROTATION
                    next_rpc = fallback_rpcs[retry_count % len(fallback_rpcs)]
                    logger.warning(f"⚠️ RPC Error on attempt {retry_count}: {e}")
                    logger.info(f"🔄 Switching to alternative RPC: {next_rpc}")

                    self.chain_client = ChainClient([next_rpc])
                    self.pricing_engine = PricingEngine(
                        chain_client=self.chain_client,
                        fork_url=app_config.FORK_URL,
                        ws_url=None,
                    )

                    self.generator.pricing = self.pricing_engine
                    self.executor.pricing = self.pricing_engine

                    logger.info("⏳ Waiting 3 seconds before hitting new RPC...")
                    await asyncio.sleep(3)
                    retry_count += 1

        elif self.is_test_mode:
            logger.info("Test mode active. Using math formula for prices.")
            self.pricing_engine = None
            self.generator.pricing = None

        if self.running:
            await self._sync_balances()

        wss_active = False

        if not self.is_test_mode and self.pricing_engine:
            fallback_wss = [
                getattr(app_config, "WSS_URL", None),
                "wss://eth-mainnet.g.alchemy.com/v2/jxDSx087keRFn7BxxVeoV",
                "wss://eth.drpc.org",
                "wss://rpc.ankr.com/eth",
                "wss://1rpc.io/eth",
                "wss://llamarpc.com",
            ]

            fallback_wss = [ws for ws in fallback_wss if ws]

            from src.pricing.mempool import MempoolMonitor

            for ws_url in fallback_wss:
                if not self.running:
                    break

                try:
                    logger.info(f"🔌 Спроба підключення до WebSocket: {ws_url}")
                    self.pricing_engine.monitor = MempoolMonitor(ws_url)

                    async def on_price_update(pool_addr):
                        try:
                            await self._tick()
                        except Exception as e:
                            logger.error(f"WS Tick error: {e}")

                    self.pricing_engine.monitor.callback = on_price_update

                    pool_addrs = [
                        p.checksum if hasattr(p, "checksum") else str(p)
                        for p in self.dex_pools
                    ]

                    test_monitor = MempoolMonitor(ws_url)
                    try:
                        block = await asyncio.wait_for(
                            test_monitor.w3.eth.block_number, timeout=5.0
                        )
                        logger.info(f"   WSS підключено, поточний блок: {block}")
                    except Exception as conn_err:
                        raise ConnectionError(f"WSS не відповідає: {conn_err}")

                    remaining_wss = fallback_wss[fallback_wss.index(ws_url) :]

                    def make_task_error_handler(remaining):
                        def handler(task):
                            if task.cancelled():
                                return
                            exc = task.exception()
                            if exc:
                                logger.error(f"❌ WSS task впав: {exc}")
                                logger.warning(
                                    "🔄 WSS відключився, бот продовжує на HTTP polling"
                                )

                        return handler

                    task = asyncio.create_task(
                        self.pricing_engine.monitor.start_price_feed(pool_addrs)
                    )
                    task.add_done_callback(make_task_error_handler(remaining_wss))

                    wss_active = True
                    logger.info(f"✅ WSS моніторинг запущено: {ws_url}")
                    break

                except asyncio.TimeoutError:
                    logger.error(f"❌ WebSocket {ws_url} — timeout підключення (5s)")
                    logger.warning("🔄 Перемикаємось на наступний WSS вузол...")
                    wss_active = False
                    await asyncio.sleep(1)
                except Exception as e:
                    logger.error(f"❌ WebSocket {ws_url} впав або не підключився: {e}")
                    logger.warning("🔄 Перемикаємось на наступний WSS вузол...")
                    wss_active = False
                    await asyncio.sleep(1)

        if (
            not wss_active or self.is_test_mode or not self.pricing_engine
        ) and self.running:
            if not self.is_test_mode and self.pricing_engine:
                logger.critical("⚠️ Всі WebSocket з'єднання провалилися!")
            logger.info("Starting reliable HTTP Polling loop (1 req/sec)...")
            while self.running:
                try:
                    await self._tick()

                    await asyncio.sleep(1.0)
                except Exception as e:
                    logger.error(f"HTTP Tick error: {e}")
                    await asyncio.sleep(5.0)

    async def _tick(self):
        if self._tick_lock.locked():
            return

        async with self._tick_lock:
            if self.executor.circuit_breaker.is_open():
                logger.info("Circuit breaker open")
                return

            for pair in self.pairs:
                # FIX: Prevent blocking the asyncio event loop with synchronous calls (e.g. fetch_order_book)
                loop = asyncio.get_event_loop()
                signal = await loop.run_in_executor(
                    None, self.generator.generate, pair, self.trade_size
                )

                if signal is None:
                    if self.verbose:
                        logger.info(
                            f"🔕 {pair}: No profitable spread found (Ignored by Generator)"
                        )
                    continue

                signal.score = float(
                    self.scorer.score(signal, self.inventory.get_skews())
                )

                if signal.score < 60.0:
                    if self.verbose:
                        logger.info(
                            f"📉 {pair}: Spread={signal.spread_bps:.1f} bps | Score={signal.score:.1f} -> REJECTED (Score too low)"
                        )
                    continue

                logger.info(
                    f"🚀 ACTIONABLE SIGNAL: {pair} spread={signal.spread_bps:.1f}bps score={signal.score}"
                )

                ctx = await self.executor.execute(signal)

                self.scorer.record_result(pair, ctx.state == ExecutorState.DONE)

                if ctx.state == ExecutorState.DONE and ctx.actual_net_pnl is not None:
                    arb_record = execution_to_arb_record(ctx)
                    self.pnl_engine.record(arb_record)
                    logger.info(f"SUCCESS: PnL=${ctx.actual_net_pnl:.2f}")
                else:
                    logger.warning(f"FAILED: {ctx.error}")

                await self._sync_balances()

    def stop(self):
        self.running = False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Arbitrage Bot - Peanut Internship")
    parser.add_argument(
        "--mode",
        type=str,
        choices=["test", "prod"],
        default="test",
        help="Run mode: 'test' (simulation with fake DEX funds) or 'prod' (real trading)",
    )
    # 2. Checkbox to display all signals, even the rejected ones
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print all evaluated signals, even rejected ones",
    )
    args = parser.parse_args()

    is_test_mode = args.mode == "test"

    logger.info(
        f" INITIALIZING BOT IN {'TEST (SIMULATION)' if is_test_mode else 'PRODUCTION'} MODE ⚙️"
    )
    if args.verbose:
        logger.info(" VERBOSE MODE ENABLED: Showing all rejected signals.")

    MAINNET_TOKENS = {
        "ETH": {
            "address": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
            "decimals": 18,
        },
        "USDT": {
            "address": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
            "decimals": 6,
        },
    }

    UNISWAP_V2_WETH_USDT = "0x0d4a11d5EEaaC28EC3F61d100daF4d40471f1852"

    bot_config = app_config.binance_config
    bot_config.update(
        {
            "pairs": ["ETH/USDT"],
            "dex_pools": [UNISWAP_V2_WETH_USDT],
            "trade_size": "1.0",
            "simulation": is_test_mode,
            "verbose": args.verbose,
            "signal_config": {
                "min_spread_bps": Decimal("40"),
                "min_profit_usd": Decimal("2.0"),
                "token_map": MAINNET_TOKENS,
            },
        }
    )

    bot = ArbBot(bot_config)

    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
