from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, bot_token: str = "", chat_id: str = "") -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    async def send(self, text: str) -> None:
        if not self.enabled:
            logger.info("Telegram disabled; event follows:\n%s", text)
            return
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(url, json={
                "chat_id": self.chat_id,
                "text": text,
                "disable_web_page_preview": True,
            })
            response.raise_for_status()
