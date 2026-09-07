from __future__ import annotations

import html
import logging
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.models import SecuritySnapshot, WalletActivity

logger = logging.getLogger(__name__)


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _fmt_number(value: str) -> str:
    if value.startswith("raw="):
        return value
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        return value
    if number == 0:
        return "0"
    places = 4 if abs(number) >= 1000 else 6 if abs(number) >= 1 else 8
    result = f"{number:,.{places}f}"
    return result.rstrip("0").rstrip(".")


def _fmt_usd(value: float | None) -> str:
    if value is None:
        return "—"
    if value >= 1000:
        return f"${value:,.0f}"
    if value >= 1:
        return f"${value:,.2f}"
    return f"${value:.4f}"


def _fmt_age(minutes: int | None) -> str:
    if minutes is None:
        return "—"
    if minutes < 60:
        return f"{minutes}m"
    if minutes < 1440:
        return f"{minutes // 60}h"
    return f"{minutes // 1440}d"


def _security_status(snapshot: SecuritySnapshot | None) -> list[str]:
    if snapshot is None:
        return ["Liquidity       ⏳ market data unavailable", "Top Holders     ⏳ security data unavailable", "Dev             ⏳ not evaluated", "Contract        ⏳ not evaluated"]
    liquidity = "✅" if not snapshot.high_risk else "⚠️"
    holders = "—"
    if snapshot.top_holder_percent is not None:
        holders = "⚠️" if snapshot.top_holder_percent >= 30 else "✅"
        holders += f" {snapshot.top_holder_percent:.1f}%"
    dev = "✅" if snapshot.creator_address else "⏳"
    contract = "⚠️" if snapshot.high_risk else "✅" if snapshot.is_open_source is not None else "⏳"
    return [
        f"Liquidity       {liquidity}",
        f"Top Holders     {holders}",
        f"Dev             {dev}",
        f"Contract        {contract}",
    ]


def format_alert(activity: WalletActivity) -> str:
    """Format an actionable Telegram alert using only available verified data."""
    event = activity.event_type
    icon = {"BUY": "🟢", "SELL": "🔴", "SWAP": "🔄"}.get(event, "🔔")
    token = activity.symbol_for(activity.action_token) or "Unknown token"
    quote = activity.symbol_for(activity.quote_token) if activity.quote_token else None
    score = round((activity.signal.score if activity.signal else activity.confidence * 100))
    source = f" · {_esc(activity.wallet_source)}" if activity.wallet_source else ""
    market = activity.market
    smart_money = activity.smart_money
    pair_lines: list[str] = []
    for transfer in activity.transfers[:8]:
        direction = "→" if transfer.from_address.lower() == activity.wallet.lower() else "←" if transfer.to_address.lower() == activity.wallet.lower() else "·"
        pair_lines.append(f"{direction} <code>{_esc(transfer.display_asset)}</code> {_esc(_fmt_number(activity.format_amount(transfer)))}")
    flow = "\n".join(pair_lines) or "· 无 ERC-20 Transfer 日志"
    token_line = f"<b>${_esc(token)}</b>"
    if quote:
        token_line += f"  /  <b>{_esc(quote)}</b>"

    smart_lines = []
    if smart_money and smart_money.participants:
        for participant in smart_money.participants[:6]:
            value = f"  {_fmt_usd(participant.trade_value_usd)}" if participant.trade_value_usd else ""
            smart_lines.append(f"{_esc(participant.wallet_label)}{value}")
        smart_lines.append(f"Total: {_fmt_usd(smart_money.total_value_usd)}")
    else:
        smart_lines.append(f"{_esc(activity.wallet_label)}")
        smart_lines.append("Total: —")

    route = _esc(activity.dex or "Unknown DEX")
    if activity.router_name:
        route += f"\nRouter: {_esc(activity.router_name)}"
    market_lines = (
        f"💰 <b>Investment:</b> {_fmt_usd(market.trade_value_usd) if market else '—'}\n"
        f"📊 <b>Market Cap:</b> {_fmt_usd(market.market_cap_usd) if market else '—'}\n"
        f"💧 <b>Liquidity:</b> {_fmt_usd(market.liquidity_usd) if market else '—'}\n"
        f"⏱ <b>Pair Age:</b> {_fmt_age(market.token_age_minutes) if market else '—'}"
    )
    return (
        "🚨 <b>SMART MONEY ALERT</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🧠 <b>{_esc(activity.wallet_label)}</b>{source}\n\n"
        f"{icon} <b>{_esc(event)}</b>\n\n"
        f"{token_line}\n\n"
        f"<b>Chain:</b> {_esc(activity.chain.upper())}\n"
        f"<b>DEX:</b> {route}\n\n"
        f"{market_lines}\n\n"
        "📡 <b>Trade Flow</b>\n"
        f"{flow}\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "👥 <b>Smart Money</b>\n\n"
        f"{chr(10).join(smart_lines)}\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "🛡 <b>Risk</b>\n\n"
        f"<code>{_esc(chr(10).join(_security_status(activity.security)))}</code>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"⭐ <b>SIGNAL SCORE: {score} / 100</b>\n"
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
        {"text": "👛 Wallet", "url": activity.wallet_url},
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
