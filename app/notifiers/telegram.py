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
        return f"{minutes} 分钟 / {minutes}m"
    if minutes < 1440:
        hours = minutes // 60
        return f"{hours} 小时 / {hours}h"
    days = minutes // 1440
    return f"{days} 天 / {days}d"


def _security_status(snapshot: SecuritySnapshot | None) -> list[str]:
    if snapshot is None:
        return [
            "流动性 / Liquidity     ⏳ 暂无市场数据",
            "持仓集中 / Top Holders  ⏳ 暂无安全数据",
            "开发者 / Dev            ⏳ 未检测",
            "合约 / Contract         ⏳ 未检测",
        ]
    liquidity = "⚠️" if snapshot.high_risk else "✅"
    holders = "—"
    if snapshot.top_holder_percent is not None:
        holders = "⚠️" if snapshot.top_holder_percent >= 30 else "✅"
        holders += f" {snapshot.top_holder_percent:.1f}%"
    dev = "✅ 已识别" if snapshot.creator_address else "⏳ 未识别"
    contract = "⚠️ 有风险" if snapshot.high_risk else "✅" if snapshot.is_open_source is not None else "⏳ 未检测"
    return [
        f"流动性 / Liquidity     {liquidity}",
        f"持仓集中 / Top Holders  {holders}",
        f"开发者 / Dev            {dev}",
        f"合约 / Contract         {contract}",
    ]


def _event_label(event: str) -> tuple[str, str]:
    return {
        "BUY": ("买入", "BUY"),
        "SELL": ("卖出", "SELL"),
        "SWAP": ("交换", "SWAP"),
    }.get(event, ("交易", event))


def format_alert(activity: WalletActivity) -> str:
    """Format a Chinese-first, bilingual Telegram alert from verified data."""
    event = activity.event_type
    icon = {"BUY": "🟢", "SELL": "🔴", "SWAP": "🔄"}.get(event, "🔔")
    event_cn, event_en = _event_label(event)
    token = activity.symbol_for(activity.action_token) or "未知 Token / Unknown token"
    quote = activity.symbol_for(activity.quote_token) if activity.quote_token else None
    score = round((activity.signal.score if activity.signal else activity.confidence * 100))
    market = activity.market
    smart_money = activity.smart_money

    flow_lines: list[str] = []
    for transfer in activity.transfers[:8]:
        direction = (
            "→ 发送 / Sent"
            if transfer.from_address.lower() == activity.wallet.lower()
            else "← 收到 / Received"
            if transfer.to_address.lower() == activity.wallet.lower()
            else "· 路径 / Route"
        )
        flow_lines.append(
            f"{direction}: <code>{_esc(transfer.display_asset)}</code> "
            f"{_esc(_fmt_number(activity.format_amount(transfer)))}"
        )
    flow = "\n".join(flow_lines) or "· 暂无 ERC-20 Transfer 日志 / No ERC-20 transfers"

    token_line = f"<b>${_esc(token)}</b>"
    if quote:
        token_line += f"  /  <b>{_esc(quote)}</b>"

    smart_lines: list[str] = []
    if smart_money and smart_money.participants:
        for participant in smart_money.participants[:6]:
            value = f"  {_fmt_usd(participant.trade_value_usd)}" if participant.trade_value_usd else ""
            smart_lines.append(f"• {_esc(participant.wallet_label)}{value}")
        smart_lines.append(
            f"<b>合计 / Total:</b> {_fmt_usd(smart_money.total_value_usd)} "
            f"({smart_money.window_minutes} 分钟窗口 / {smart_money.window_minutes}m window)"
        )
    else:
        smart_lines.append(f"• {_esc(activity.wallet_label)}")
        smart_lines.append("<b>合计 / Total:</b> —")

    route = _esc(activity.dex or "未知 DEX / Unknown DEX")
    if activity.router_name:
        route += f"\n<b>路由 / Router:</b> {_esc(activity.router_name)}"
    market_lines = (
        f"💰 <b>交易金额 / Trade Value:</b> {_fmt_usd(market.trade_value_usd) if market else '—'}\n"
        f"📊 <b>市值 / Market Cap:</b> {_fmt_usd(market.market_cap_usd) if market else '—'}\n"
        f"💧 <b>流动性 / Liquidity:</b> {_fmt_usd(market.liquidity_usd) if market else '—'}\n"
        f"⏱ <b>交易对年龄 / Pair Age:</b> {_fmt_age(market.token_age_minutes) if market else '—'}"
    )
    source_line = f"\n<b>来源 / Source:</b> {_esc(activity.wallet_source)}" if activity.wallet_source else ""
    reason_line = _esc(activity.analysis_reason) if activity.analysis_reason else "—"

    return (
        "🚨 <b>SMART MONEY ALERT / 交易提醒</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🧠 <b>钱包 / Wallet: {_esc(activity.wallet_label)}</b>{source_line}\n\n"
        f"{icon} <b>{event_cn} / {event_en}</b>\n\n"
        f"{token_line}\n\n"
        f"<b>链 / Chain:</b> {_esc(activity.chain.upper())}\n"
        f"<b>DEX:</b> {route}\n"
        f"<b>判断 / Analysis:</b> {reason_line}\n\n"
        f"{market_lines}\n\n"
        "📡 <b>资金流向 / Trade Flow</b>\n"
        f"{flow}\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "👥 <b>Smart Money 聚合 / Aggregation</b>\n\n"
        f"{chr(10).join(smart_lines)}\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "🛡 <b>风险检查 / Risk Check</b>\n\n"
        f"<code>{_esc(chr(10).join(_security_status(activity.security)))}</code>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"⭐ <b>信号评分 / SIGNAL SCORE: {score} / 100</b>\n"
        f"<b>交易哈希 / Tx:</b> <code>{_esc(activity.tx_hash)}</code>"
    )


def alert_buttons(activity: WalletActivity) -> dict[str, Any]:
    buttons: list[list[dict[str, str]]] = []
    if activity.action_token and activity.chain.lower() == "bsc":
        buttons.append([
            {"text": "📊 图表 / Chart", "url": f"https://dexscreener.com/bsc/{activity.action_token}"},
            {"text": "📄 合约 / Contract", "url": f"https://bscscan.com/token/{activity.action_token}"},
        ])
    buttons.append([
        {"text": "👛 钱包 / Wallet", "url": activity.wallet_url},
        {"text": "🔗 交易 / Tx", "url": activity.explorer_url},
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
        if response.is_error:
            try:
                description = response.json().get("description")
            except (TypeError, ValueError):
                description = None
            detail = description or response.reason_phrase or "unknown error"
            # Do not call raise_for_status(): httpx includes the Bot URL in its
            # exception text, which would leak the Bot Token into logs.
            raise RuntimeError(
                f"Telegram API request failed ({response.status_code}): {detail}"
            )

    async def send_alert(self, activity: WalletActivity) -> None:
        await self.send(format_alert(activity), alert_buttons(activity))
