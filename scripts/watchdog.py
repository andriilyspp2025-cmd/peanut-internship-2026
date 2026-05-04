import logging
import os
import subprocess
import tempfile
import time
import sys
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.safety.alerts import TelegramAlert

logging.basicConfig(level=logging.INFO)

TIMEOUT_SECONDS = 30.0
CHECK_INTERVAL_SECONDS = 5.0


def heartbeat_path() -> Path:
    override = os.getenv("HEARTBEAT_FILE")
    if override:
        return Path(override)
    if os.name == "nt":
        return Path(tempfile.gettempdir()) / "arb_bot_heartbeat"
    return Path("/tmp/arb_bot_heartbeat")


def kill_bot_process() -> None:
    if os.name == "nt":
        command = (
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.CommandLine -like '*arb_bot.py*' } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
        )
        subprocess.run(["powershell", "-Command", command], check=False)
        return

    subprocess.run(["pkill", "-f", "arb_bot.py"], check=False)


def run_watchdog() -> None:
    logging.info("Dead man's switch watchdog started.")
    alerts = TelegramAlert.from_env()

    while True:
        time.sleep(CHECK_INTERVAL_SECONDS)
        path = heartbeat_path()
        if not path.exists():
            logging.warning("Heartbeat file not found: %s", path)
            continue

        last_heartbeat = path.stat().st_mtime
        age = time.time() - last_heartbeat
        if age > TIMEOUT_SECONDS:
            message = (
                f"WATCHDOG: Heartbeat stale ({age:.1f}s). Killing arb_bot.py process."
            )
            logging.critical(message)
            alerts.send(message)
            kill_bot_process()
            break


if __name__ == "__main__":
    run_watchdog()
