from __future__ import annotations

import asyncio
import logging
import os
from urllib import request, parse

logger = logging.getLogger("Safety.Alerts")


class TelegramAlert:
    def __init__(self, token: str, chat_id: str, enabled: bool = True):
        self.token = token
        self.chat_id = chat_id
        self.enabled = enabled and bool(token) and bool(chat_id)

    @classmethod
    def from_env(cls) -> "TelegramAlert":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        enabled = os.getenv("TELEGRAM_ENABLED", "true").lower() in {"1", "true", "yes"}
        return cls(token=token, chat_id=chat_id, enabled=enabled)

    def send(self, message: str) -> bool:
        if not self.enabled:
            return False

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = parse.urlencode({"chat_id": self.chat_id, "text": message})
        data = payload.encode("utf-8")

        def _send() -> bool:
            try:
                with request.urlopen(url, data=data, timeout=5) as response:
                    return 200 <= response.status < 300
            except Exception as exc:
                logger.warning(f"Failed to send Telegram alert: {exc}")
                return False

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(asyncio.to_thread(_send))
            return True
        except RuntimeError:
            return _send()
