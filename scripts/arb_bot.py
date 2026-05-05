import argparse
import asyncio
import os
import logging
import time
from decimal import Decimal
from datetime import datetime
from pathlib import Path
import tempfile

from src.exchange.client import ExchangeClient
from src.inventory.tracker import InventoryTracker, Venue
from src.inventory.pnl import PnLEngine, ArbRecord, TradeLeg
from src.strategy.fees import FeeStructure
from src.strategy.generator import SignalGenerator
from src.strategy.scorer import SignalScorer
from src.executor.engine import (
    ExecutionContext,
    Executor,
    ExecutorConfig,
    ExecutorState,
)
from src.config.config import config as app_config
from src.config.logger import setup_logger
from src.chain.client import ChainClient
from src.pricing.engine import PricingEngine
from src.core.types import Address
from src.safety.alerts import TelegramAlert
from src.safety.killswitch import activate_kill_switch, is_kill_switch_active
from src.safety.limits import RiskLimits, RiskManager
from src.safety.validator import PreTradeValidator, ValidatorConfig

logger = logging.getLogger("ArbBot")

STABLE_ASSETS = {"USD", "USDT", "USDC", "DAI"}


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
        self.dry_run = config.get("dry_run", False)

        self.exchange = ExchangeClient(config)

        self.chain_client = ChainClient([app_config.RPC_URL])

        wss_url = None

        self.pricing_engine = PricingEngine(
            chain_client=self.chain_client, fork_url=app_config.FORK_URL, ws_url=wss_url
        )

        self.inventory = InventoryTracker([Venue.BINANCE, Venue.WALLET])
        self.pnl_engine = PnLEngine()
        fee_config = config.get("fee_config", {})
        self.fees = FeeStructure.from_config(fee_config)

        risk_config = config.get("risk_config", {})
        self.risk_manager = RiskManager(RiskLimits.from_config(risk_config))
        self.pre_trade_validator = PreTradeValidator(
            ValidatorConfig.from_config(risk_config)
        )
        self.alerts = TelegramAlert.from_env()

        self.generator = SignalGenerator(
            self.exchange,
            self.pricing_engine,
            self.inventory,
            self.fees,
            config.get("signal_config", {}),
        )
        self.scorer = SignalScorer()

        addr_str = os.getenv("ADDRESS")
        self.wallet_address = Address(addr_str) if addr_str else None

        exec_config = ExecutorConfig(
            simulation_mode=config.get("simulation", True), use_flashbots=False
        )
        self.executor = Executor(
            self.exchange,
            self.pricing_engine,
            self.inventory,
            exec_config,
            token_map=config.get("signal_config", {}).get("token_map", {}),
            wallet_address=self.wallet_address,
        )

        self.pairs = config.get("pairs", ["ETH/USDT"])
        self.trade_size = Decimal(str(config.get("trade_size", "0.1")))

        raw_pools = config.get("dex_pools", [])
        self.dex_pools = [Address(p) if isinstance(p, str) else p for p in raw_pools]

        self.running = False
        self._tick_lock = asyncio.Lock()

    def _heartbeat_path(self) -> Path:
        override = os.getenv("HEARTBEAT_FILE")
        if override:
            return Path(override)
        if os.name == "nt":
            return Path(tempfile.gettempdir()) / "arb_bot_heartbeat"
        return Path("/tmp/arb_bot_heartbeat")

    def _emit_heartbeat(self) -> None:
        try:
            path = self._heartbeat_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(time.time()), encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to write heartbeat: %s", exc)

    def _is_stable_asset(self, asset: str) -> bool:
        return asset.upper() in STABLE_ASSETS

    def _price_asset_usd(self, asset: str) -> Decimal | None:
        for pair in self.pairs:
            try:
                base, quote = pair.split("/")
            except ValueError:
                continue
            if base != asset or not self._is_stable_asset(quote):
                continue
            try:
                order_book = self.exchange.fetch_order_book(pair)
            except Exception as exc:
                logger.warning("Failed to fetch order book for %s: %s", pair, exc)
                return None
            best_bid = order_book["best_bid"][0]
            best_ask = order_book["best_ask"][0]
            return (best_bid + best_ask) / Decimal("2")
        return None

    def _position_state(self, signal) -> tuple[Decimal, int, bool]:
        snapshot = self.inventory.snapshot()
        totals = snapshot.get("totals", {})
        base, _ = signal.pair.split("/")
        min_open_usd = self.risk_manager.limits.min_open_position_usd

        price_cache: dict[str, Decimal] = {}
        open_assets: set[str] = set()

        for asset, amount in totals.items():
            if self._is_stable_asset(asset):
                continue
            price = price_cache.get(asset)
            if price is None:
                if asset == base:
                    price = signal.cex_price
                else:
                    price = self._price_asset_usd(asset)
                if price is None:
                    if self.verbose:
                        logger.info(
                            "Open position check skipped unpriced asset: %s", asset
                        )
                    continue
                price_cache[asset] = price
            asset_usd = amount * price
            if abs(asset_usd) >= min_open_usd:
                open_assets.add(asset)

        if self._is_stable_asset(base):
            current_position_usd = totals.get(base, Decimal("0"))
            is_new_position = False
        else:
            current_position_usd = totals.get(base, Decimal("0")) * signal.cex_price
            is_new_position = base not in open_assets

        return current_position_usd, len(open_assets), is_new_position

    async def _sync_balances(self):
        """Sync balances from both CEX and on-chain wallet."""
        try:
            # CEX Balances
            cex_balances = await asyncio.to_thread(self.exchange.fetch_balance)
            gmx_cex = cex_balances.get("GMX", {}).get("free", "0")
            usdt_cex = cex_balances.get("USDT", {}).get("free", "0")
            logger.info(f"CEX Balances | GMX: {gmx_cex} | USDT: {usdt_cex}")
            self.inventory.update_from_cex(Venue.BINANCE, cex_balances)

            # DEX Balances
            if self.is_test_mode:
                # TEST MODE (Fake money for simulating transactions)
                logger.info(
                    "DEX Balances (TEST MODE) | GMX: 1.0 | USDT: 10000.0 | USDC: 10000.0"
                )
                self.inventory.update_from_wallet(
                    Venue.WALLET,
                    {
                        "GMX": Decimal("1.0"),
                        "USDT": Decimal("10000.0"),
                        "USDC": Decimal("10000.0"),
                    },
                )
            elif self.wallet_address and self.pricing_engine is not None:
                eth_wallet_bal = await asyncio.to_thread(
                    self.chain_client.get_balance, self.wallet_address
                )
                eth_decimal = eth_wallet_bal.human

                usdt_address = self.generator.token_map["USDT"]["address"]
                usdc_address = self.generator.token_map["USDC"]["address"]
                calldata = "0x70a08231" + self.wallet_address.checksum[2:].zfill(64)

                try:
                    res = await asyncio.to_thread(
                        self.chain_client.w3.eth.call,
                        {"to": usdt_address, "data": calldata},
                    )
                    usdt_raw = int.from_bytes(res, "big") if res else 0
                    usdt_decimal = Decimal(usdt_raw) / Decimal(10**6)
                except Exception:
                    usdt_decimal = Decimal("0")

                try:
                    res = await asyncio.to_thread(
                        self.chain_client.w3.eth.call,
                        {"to": usdc_address, "data": calldata},
                    )
                    usdc_raw = int.from_bytes(res, "big") if res else 0
                    usdc_decimal = Decimal(usdc_raw) / Decimal(10**6)
                except Exception:
                    usdc_decimal = Decimal("0")

                logger.info(
                    f"DEX Balances (PROD) | ETH: {eth_decimal} | USDT: {usdt_decimal} | USDC: {usdc_decimal}"
                )
                self.inventory.update_from_wallet(
                    Venue.WALLET,
                    {
                        "ETH": eth_decimal,
                        "USDT": usdt_decimal,
                        "USDC": usdc_decimal,
                    },
                )
        except Exception as e:
            logger.error(f"Error syncing balances: {e}")

    def _estimate_total_capital_usd(self, signal) -> Decimal | None:
        snapshot = self.inventory.snapshot()
        totals = snapshot.get("totals", {})
        if not totals:
            return None

        base, quote = signal.pair.split("/")
        total_usd = Decimal("0")
        unpriced_assets: list[str] = []

        for asset, amount in totals.items():
            if asset in {"USD", "USDT", "USDC"}:
                total_usd += amount
            elif asset == quote:
                total_usd += amount
            elif asset == base:
                total_usd += amount * signal.cex_price
            else:
                unpriced_assets.append(asset)

        if unpriced_assets and self.verbose:
            logger.info(
                f"Unpriced assets skipped in capital calc: {', '.join(unpriced_assets)}"
            )

        return total_usd if total_usd > Decimal("0") else None

    async def run(self):
        self.running = True

        if is_kill_switch_active():
            logger.critical("Kill switch active at startup. Exiting.")
            self.alerts.send("Kill switch active at startup. Bot stopped.")
            self.running = False
            return

        mode_name = (
            "SIMULATION (TEST)" if self.is_test_mode else "REAL BLOCKCHAIN (PROD)"
        )
        logger.info(f"Bot starting in {mode_name} mode...")
        if self.dry_run:
            logger.info("DRY RUN enabled: execution will be skipped.")
        self.alerts.send(f"Bot started in {mode_name} | dry_run={self.dry_run}")

        if not self.is_test_mode and self.pricing_engine and self.dex_pools:
            logger.info("📡 Loading REAL DEX liquidity pools...")
            pools_loaded = False
            retry_count = 1
            max_retries = 6

            fallback_rpcs = [
                app_config.RPC_URL,
                "https://arbiscan.io",
                "wss://arbitrum-one-rpc.publicnode.com",
                "https://quick-virulent-dream.arbitrum-mainnet.quiknode.pro/f0ee513de8890b4e313d3739b55639fd9bd4b6c6/",
                "https://rpc.owlracle.info/arb/70d38ce1826c4a60bb2a8e05a6c8b20f",
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
            configured_wss = getattr(app_config, "WSS_URL", None)
            fallback_wss = [configured_wss] if configured_wss else []

            if not fallback_wss:
                logger.info(
                    "WSS monitoring is disabled because no valid WSS_URL is configured."
                )
            else:
                from src.pricing.mempool import MempoolMonitor

                for ws_url in fallback_wss:
                    if not self.running:
                        break

                    try:
                        logger.info(f"🔌 Спроба підключення до WebSocket: {ws_url}")

                        async def on_price_update(pool_addr):
                            try:
                                await self._tick()
                            except Exception as e:
                                logger.error(f"WS Tick error: {e}")

                        monitor = MempoolMonitor(
                            ws_url,
                            http_url=app_config.RPC_URL,
                            callback=on_price_update,
                        )
                        self.pricing_engine.monitor = monitor

                        await asyncio.wait_for(
                            self.pricing_engine.monitor.connect(), timeout=10.0
                        )
                        block = await asyncio.wait_for(
                            self.pricing_engine.monitor.w3.eth.block_number,
                            timeout=5.0,
                        )
                        logger.info(f"   WSS підключено, поточний блок: {block}")

                        pool_addrs = [
                            p.checksum if hasattr(p, "checksum") else str(p)
                            for p in self.dex_pools
                        ]
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
                        logger.error(
                            f"❌ WebSocket {ws_url} — timeout підключення (10s)"
                        )
                        logger.warning("🔄 Перемикаємось на наступний WSS вузол...")
                        wss_active = False
                        await asyncio.sleep(1)
                    except Exception as e:
                        logger.error(
                            f"❌ WebSocket {ws_url} впав або не підключився: {e}"
                        )
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
        elif wss_active and self.running:
            logger.info("👀 Основний цикл запущено (WSS + HTTP Backup)...")
            while self.running:
                try:
                    await self._tick()
                except Exception as e:
                    logger.error(f"Tick error: {e}")
                # Опитуємо ціни кожні 2.5 секунди (щоб не зловити бан від RPC та Binance)
                await asyncio.sleep(2.5)

    async def _tick(self):
        if self._tick_lock.locked():
            return

        async with self._tick_lock:
            self._emit_heartbeat()
            if self.executor.circuit_breaker.is_open():
                logger.info("Circuit breaker open")
                return

            if is_kill_switch_active():
                logger.critical("Kill switch active. Stopping bot.")
                self.alerts.send("Kill switch active. Bot stopped.")
                self.stop()
                return

            for pair in self.pairs:
                try:
                    # ДОДАНО ТАЙМАУТ: Захист від зависання мережі
                    loop = asyncio.get_running_loop()
                    signal = await asyncio.wait_for(
                        loop.run_in_executor(
                            None, self.generator.generate, pair, self.trade_size
                        ),
                        timeout=10.0,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        f"⏳ Timeout waiting for price feed on {pair}. Skipping tick."
                    )
                    continue
                except Exception as e:
                    logger.error(f"❌ Error generating signal for {pair}: {e}")
                    continue

                if signal is None:
                    if self.verbose:
                        logger.info(
                            f"🔕 {pair}: No profitable spread found (Ignored by Generator)"
                        )
                    continue

                signal.score = self.scorer.score(signal, self.inventory.get_skews())

                if signal.score < 60.0:
                    if self.verbose:
                        logger.info(
                            f"📉 {pair}: Spread={signal.spread_bps:.1f} bps | Score={signal.score:.1f} -> REJECTED (Score too low)"
                        )
                    continue

                logger.info(
                    f"🚀 ACTIONABLE SIGNAL: {pair} spread={signal.spread_bps:.1f}bps score={signal.score}"
                )

                valid, reason = self.pre_trade_validator.validate(signal)
                if not valid:
                    if self.verbose:
                        logger.info(f"Signal rejected by validator: {reason}")
                    continue

                trade_notional_usd = signal.size * signal.cex_price
                total_capital_usd = self._estimate_total_capital_usd(signal)
                (
                    current_position_usd,
                    open_positions,
                    is_new_position,
                ) = self._position_state(signal)
                risk_result = self.risk_manager.pre_trade_check(
                    trade_notional_usd,
                    total_capital_usd,
                    current_position_usd=current_position_usd,
                    open_positions=open_positions,
                    is_new_position=is_new_position,
                )
                if not risk_result.allowed:
                    logger.warning(f"Risk check failed: {risk_result.reason}")
                    if risk_result.hard_stop:
                        activate_kill_switch(risk_result.reason)
                        self.alerts.send(f"Kill switch activated: {risk_result.reason}")
                        self.stop()
                        return
                    continue

                self.risk_manager.record_trade_attempt()

                if self.dry_run:
                    logger.info(
                        "DRY RUN: signal accepted but execution skipped "
                        f"pair={pair} notional_usd={trade_notional_usd}"
                    )
                    ctx = ExecutionContext(
                        signal=signal,
                        state=ExecutorState.DONE,
                        leg1_venue="cex",
                        leg2_venue="dex",
                        leg1_fill_price=signal.cex_price,
                        leg1_fill_size=signal.size,
                        leg2_fill_price=signal.dex_price,
                        leg2_fill_size=signal.size,
                        actual_net_pnl=signal.expected_net_pnl,
                        finished_at=time.time(),
                    )
                else:
                    ctx = await self.executor.execute(signal)

                self.scorer.record_result(pair, ctx.state == ExecutorState.DONE)

                if ctx.state == ExecutorState.DONE and ctx.actual_net_pnl is not None:
                    arb_record = execution_to_arb_record(ctx)
                    self.pnl_engine.record(arb_record)
                    logger.info(f"SUCCESS: PnL=${ctx.actual_net_pnl:.2f}")
                    self.risk_manager.record_trade_result(
                        ctx.actual_net_pnl,
                        success=True,
                        total_capital_usd=total_capital_usd,
                    )
                    self.alerts.send(
                        f"Trade completed: {pair} net_pnl={ctx.actual_net_pnl}"
                    )
                else:
                    logger.warning(f"FAILED: {ctx.error}")
                    self.risk_manager.record_trade_result(
                        Decimal("0"),
                        success=False,
                        total_capital_usd=total_capital_usd,
                    )
                    if ctx.error:
                        self.alerts.send(f"Trade failed: {pair} error={ctx.error}")

                await self._sync_balances()

    def stop(self):
        self.running = False


if __name__ == "__main__":
    import signal
    import sys

    parser = argparse.ArgumentParser(description="Arbitrage Bot - Peanut Internship")
    parser.add_argument(
        "--mode",
        type=str,
        choices=["test", "prod"],
        default="test",
        help="Run mode: 'test' (simulation with fake DEX funds) or 'prod' (real trading)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run full checks but skip trade execution",
    )
    # 2. Checkbox to display all signals, even the rejected ones
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print all evaluated signals, even rejected ones",
    )
    args = parser.parse_args()

    log_prefix = "bot_dry_run" if args.dry_run else "bot"
    log_path = setup_logger(
        log_dir=app_config.LOG_DIR,
        filename_prefix=log_prefix,
        level=app_config.LOG_LEVEL,
    )

    is_test_mode = args.mode == "test"

    logger.info(
        f" INITIALIZING BOT IN {'TEST (SIMULATION)' if is_test_mode else 'PRODUCTION'} MODE ⚙️"
    )
    logger.info(f"Logging to {log_path}")
    if args.verbose:
        logger.info(" VERBOSE MODE ENABLED: Showing all rejected signals.")

    ARBITRUM_TOKENS = {
        "GMX": {
            "address": "0xfc5A1A6eb076a2C7aD06eD22C90d7E710E35ad0a",
            "decimals": 18,
        },
        "USDT": {
            "address": "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
            "decimals": 6,
        },
        "USDC": {
            "address": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
            "decimals": 6,
        },
    }

    GMX_USDC_POOL_V3 = "0x80a9ae39310abf666f873be423e2c28b58403d1c"

    bot_config = app_config.binance_config
    bot_config.update(
        {
            "pairs": ["GMX/USDT"],
            "dex_pools": [],
            "trade_size": "1.0",
            "simulation": is_test_mode,
            "dry_run": args.dry_run,
            "verbose": args.verbose,
            "signal_config": {
                "min_spread_bps": Decimal("150"),
                "min_profit_usd": Decimal("0.5"),
                "token_map": ARBITRUM_TOKENS,
                "dex_quote_map": {"GMX/USDT": "USDC"},
                "verbose": args.verbose,
            },
            "risk_config": {
                "max_trade_usd": "5.0",
                "max_position_usd": "50",
                "max_daily_loss_usd": "15",
                "max_drawdown_pct": "0.15",
                "max_trades_per_hour": 20,
                "max_consecutive_losses": 3,
                "max_open_positions": 2,
                "min_open_position_usd": "1",
                "max_signal_age_seconds": "5",
                "min_spread_bps": "50",
                "max_spread_bps": "1000",
            },
        }
    )

    bot = ArbBot(bot_config)

    def handle_sigterm(signum, frame):
        logger.info("Received SIGTERM from Docker. Shutting down gracefully...")
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_sigterm)

    try:
        asyncio.run(bot.run())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped by user / system signal.")
    except Exception as e:
        logger.critical(f"Bot crashed with error: {e}")
    finally:
        logger.info("Sending stop alert to Telegram...")
        bot.alerts.send("Peanut Arb Bot has stopped working / Shut down.")
        try:
            hb_path = bot._heartbeat_path()
            if hb_path.exists():
                hb_path.unlink()
                logger.info("Heartbeat file cleaned up successfully.")
        except Exception as e:
            logger.error(f"Could not delete heartbeat file: {e}")
