from __future__ import annotations

import html
import logging
from typing import Any

import httpx

from app.models import WalletActivity

logger = logging.getLogger(__name__)


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _fmt_number(value: str) -> str:
    if value.startswith("raw="):
        return value
    try:
        number = float(value)
    except ValueError:
        return value
    if number == 0:
        return "0"
    if abs(number) >= 1000:
        return f"{number:,.4f}".rstrip("0").rstrip(".")
    if abs(number) >= 1:
        return f"{number:,.6f}".rstrip("0").rstrip(".")
    return f"{number:.8f}".rstrip("0").rstrip(".")


def format_alert(activity: WalletActivity) -> str:
    """Format an actionable Telegram alert without inventing market-price data."""
    event = activity.event_type
    icon = {"BUY": "🟢", "SELL": "🔴", "SWAP": "🔄"}.get(event, "🔔")
    token = activity.symbol_for(activity.action_token) or "Unknown token"
    quote = activity.symbol_for(activity.quote_token) if activity.quote_token else None
    score = round(activity.confidence * 100)
    source = f" · {_esc(activity.wallet_source)}" if activity.wallet_source else ""
    pair_lines: list[str] = []
    for transfer in activity.transfers[:8]:
        direction = "→" if transfer.from_address.lower() == activity.wallet.lower() else "←" if transfer.to_address.lower() == activity.wallet.lower() else "·"
        pair_lines.append(f"{direction} <code>{_esc(transfer.display_asset)}</code> {_esc(_fmt_number(activity.format_amount(transfer)))}")
    flow = "\n".join(pair_lines) or "· 无 ERC-20 Transfer 日志"

    risk_lines = [
        f"Transfer pattern  {'✅' if event in {'BUY', 'SELL', 'SWAP'} else '⚠️'}",
        f"Token metadata    {'✅' if activity.action_token and token != activity.action_token else '⚠️'}",
        f"DEX route         {'✅' if activity.pair_swaps else '⚠️'}",
        f"Contract identity  {'✅' if activity.dex and not activity.dex.startswith('Unknown') else '⚠️'}",
    ]
    route = f"{_esc(activity.dex or 'Unknown DEX')}"
    if activity.router_name:
        route += f"\nRouter: {_esc(activity.router_name)}"
    token_line = f"<b>{_esc(token)}</b>"
    if quote:
        token_line += f"  /  Quote: <b>{_esc(quote)}</b>"

    return (
        "🚨 <b>SMART MONEY ALERT</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🧠 <b>{_esc(activity.wallet_label)}</b>{source}\n"
        f"⭐ Signal confidence: <b>{score}/100</b>\n\n"
        f"{icon} <b>{_esc(event)}</b>\n\n"
        f"{token_line}\n\n"
        f"<b>Chain:</b> {_esc(activity.chain.upper())}\n"
        f"<b>DEX:</b> {route}\n\n"
        "💰 <b>Trade Flow</b>\n"
        f"{flow}\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "🛡 <b>Risk Snapshot</b>\n\n"
        f"<code>{_esc(chr(10).join(risk_lines))}</code>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"⭐ <b>SIGNAL SCORE: {score} / 100</b>\n\n"
        f"<b>Tx:</b> <code>{_esc(activity.tx_hash)}</code>"
    )


def alert_buttons(activity: WalletActivity) -> dict[str, Any]:
    buttons: list[list[dict[str, str]]] = []
    if activity.action_token and activity.chain.lower() == "bsc":
        buttons.append([
            {"text": "📊 Chart", "url": f"https://dexscreener.com/bsc/{activity.action_token}"},
            {"text": "📄 Contract", "url": f"https://bscscan.com/token/{activity.action_token}"},
        ])
    buttons.append([
        {"text": "👛 Wallet", "url": f"https://bscscan.com/address/{activity.wallet}" if activity.chain.lower() == "bsc" else activity.explorer_url},
        {"text": "🔗 Transaction", "url": activity.explorer_url},
    ])
    return {"inline_keyboard": buttons}


class TelegramNotifier:
    def __init__(self, bot_token: str = "", chat_id: str = "") -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    async def send(self, text: str, reply_markup: dict[str, Any] | None = None) -> None:
        if not self.enabled:
            logger.info("Telegram disabled; event follows:\n%s", text)
            return
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()

    async def send_alert(self, activity: WalletActivity) -> None:
        await self.send(format_alert(activity), alert_buttons(activity))
