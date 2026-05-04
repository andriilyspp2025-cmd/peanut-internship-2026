import asyncio
import logging
from decimal import Decimal
import os
import sys

# Add project root to sys.path so imports resolve correctly.
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config.logger import setup_logger
from src.safety.alerts import TelegramAlert
from src.safety.limits import RiskLimits, RiskManager
from src.safety.killswitch import clear_kill_switch


async def simulate_bad_trading_day() -> None:
    """
    Simulates a series of losing trades to test RiskManager behavior
    and Telegram alerts delivery.
    """
    setup_logger(filename_prefix="test_alerts", level="DEBUG")
    logger = logging.getLogger("TestAlerts")

    logger.info("Starting Telegram alert and Risk Manager simulator...")

    alerts = TelegramAlert.from_env()
    if not alerts.enabled:
        logger.error("Telegram alerts are disabled or not configured in .env")
        return

    alerts.send("Test: safety system (test_alerts.py) started.")
    logger.info("Test message sent. Check Telegram.")
    await asyncio.sleep(2)

    clear_kill_switch()

    config = {
        "max_trade_usd": "20",
        "max_daily_loss_usd": "15",
        "max_drawdown_pct": "0.15",
        "max_trades_per_hour": 20,
        "max_consecutive_losses": 3,
    }
    limits = RiskLimits.from_config(config)
    risk_manager = RiskManager(limits)

    current_capital = Decimal("100.0")
    logger.info(f"Starting capital: ${current_capital}")

    trades = [
        {
            "notional": Decimal("10.0"),
            "pnl": Decimal("2.0"),
            "desc": "Profitable trade",
        },
        {"notional": Decimal("15.0"), "pnl": Decimal("-5.0"), "desc": "Losing trade 1"},
        {"notional": Decimal("15.0"), "pnl": Decimal("-6.0"), "desc": "Losing trade 2"},
        {
            "notional": Decimal("15.0"),
            "pnl": Decimal("-5.0"),
            "desc": "Losing trade 3 (expect risk trigger)",
        },
    ]

    for i, trade in enumerate(trades, start=1):
        logger.info(f"\n--- Trade {i}: {trade['desc']} ---")

        check_result = risk_manager.pre_trade_check(
            trade_notional_usd=trade["notional"],
            total_capital_usd=current_capital,
        )

        if not check_result.allowed:
            logger.critical(
                "Trade rejected by Risk Manager. Reason: %s",
                check_result.reason,
            )
            alerts.send(
                f"ALERT: Trade rejected by Risk Manager. Reason: {check_result.reason}"
            )

            if check_result.hard_stop:
                logger.critical("Hard stop triggered. Bot should stop.")
                alerts.send("KILL SWITCH activated by risk system.")
            break

        logger.info("Trade allowed. Notional: $%s", trade["notional"])
        risk_manager.record_trade_attempt()

        current_capital += trade["pnl"]
        success = trade["pnl"] > 0
        risk_manager.record_trade_result(
            trade["pnl"],
            success=success,
            total_capital_usd=current_capital,
        )

        logger.info(
            "Result: PnL=%s. Current capital=%s",
            trade["pnl"],
            current_capital,
        )
        alerts.send(f"Simulation result: PnL={trade['pnl']}. Capital={current_capital}")

        await asyncio.sleep(2)

    logger.info("Simulation completed.")
    alerts.send("Safety simulation completed.")


if __name__ == "__main__":
    asyncio.run(simulate_bad_trading_day())
