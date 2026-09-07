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
        return f"{minutes} 分钟"
    hours, remaining = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} 小时" if not remaining else f"{hours} 小时 {remaining} 分钟"
    days, remaining_hours = divmod(hours, 24)
    return f"{days} 天" if not remaining_hours else f"{days} 天 {remaining_hours} 小时"


def _security_status(snapshot: SecuritySnapshot | None) -> list[str]:
    """Return compact, Chinese-first risk rows for the Telegram card."""
    if snapshot is None:
        return [
            "流动性       ⏳ 暂无数据",
            "持仓集中     ⏳ 暂未检测",
            "开发者       ⏳ 暂未识别",
            "合约安全     ⏳ 暂未检测",
        ]

    liquidity = "⚠️ 偏低/有风险" if snapshot.high_risk else "✅ 正常"
    holders = "⏳ 暂未检测"
    if snapshot.top_holder_percent is not None:
        holder_icon = "⚠️" if snapshot.top_holder_percent >= 30 else "✅"
        holders = f"{holder_icon} {snapshot.top_holder_percent:.1f}%"
    dev = "✅ 已识别" if snapshot.creator_address else "⏳ 暂未识别"
    if snapshot.high_risk:
        contract = "⚠️ 存在风险"
    elif snapshot.is_open_source is not None:
        contract = "✅ 暂无明显风险"
    else:
        contract = "⏳ 暂未检测"
    return [
        f"流动性       {liquidity}",
        f"持仓集中     {holders}",
        f"开发者       {dev}",
        f"合约安全     {contract}",
    ]


def _event_label(event: str) -> tuple[str, str]:
    return {
        "BUY": ("买入", "BUY"),
        "SELL": ("卖出", "SELL"),
        "SWAP": ("交换", "SWAP"),
    }.get(event, ("交易", event))


def _score_bar(score: int) -> str:
    filled = max(0, min(10, round(score / 10)))
    return "🟩" * filled + "⬜" * (10 - filled)


def format_alert(activity: WalletActivity) -> str:
    """Format a compact, polished, Chinese-first Telegram alert.

    The transaction hash is intentionally omitted from the body and remains
    available through the transaction button in ``alert_buttons``.
    """
    event = activity.event_type
    icon = {"BUY": "🟢", "SELL": "🔴", "SWAP": "🔄"}.get(event, "🔔")
    event_cn, event_en = _event_label(event)
    token = activity.symbol_for(activity.action_token) or "未知 Token"
    quote = activity.symbol_for(activity.quote_token) if activity.quote_token else None
    score = round(activity.signal.score if activity.signal else activity.confidence * 100)
    score = max(0, min(100, score))
    market = activity.market
    smart_money = activity.smart_money

    flow_lines: list[str] = []
    for index, transfer in enumerate(activity.transfers[:8]):
        if transfer.from_address.lower() == activity.wallet.lower():
            direction, branch = "支付", "├"
        elif transfer.to_address.lower() == activity.wallet.lower():
            direction, branch = "收到", "├"
        else:
            direction, branch = "路径", "├"
        if index == min(len(activity.transfers), 8) - 1:
            branch = "└"
        flow_lines.append(
            f"{branch} <b>{direction}</b>  <code>{_esc(transfer.display_asset)}</code> "
            f"<code>{_esc(_fmt_number(activity.format_amount(transfer)))}</code>"
        )
    flow = "\n".join(flow_lines) or "└ 暂无 Token 转账明细"

    token_line = f"<b>${_esc(token)}</b>"
    if quote:
        token_line += f"  ↔  <b>{_esc(quote)}</b>"

    smart_lines: list[str] = []
    if smart_money and smart_money.participants:
        for index, participant in enumerate(smart_money.participants[:6]):
            prefix = "└" if index == min(len(smart_money.participants), 6) - 1 else "├"
            value = f"  {_fmt_usd(participant.trade_value_usd)}" if participant.trade_value_usd else "  —"
            smart_lines.append(f"{prefix} {_esc(participant.wallet_label)} <b>{value}</b>")
        smart_total = _fmt_usd(smart_money.total_value_usd)
        window = f"{smart_money.window_minutes} 分钟窗口"
    else:
        smart_lines.append(f"└ {_esc(activity.wallet_label)}")
        smart_total = "—"
        window = "当前窗口"

    chain = _esc(activity.chain.upper())
    dex = _esc(activity.dex) if activity.dex else "未知 DEX"
    route_line = f"🧭 <b>路径：</b>{dex}"
    if activity.router_name:
        route_line += f" · {_esc(activity.router_name)}"
    source_line = f" · 来源：{_esc(activity.wallet_source)}" if activity.wallet_source else ""
    reason_line = _esc(activity.analysis_reason) if activity.analysis_reason else "暂无补充判断"

    market_lines = "\n".join([
        f"💰 <b>交易金额</b>    {_fmt_usd(market.trade_value_usd) if market else '—'}",
        f"📊 <b>当前市值</b>    {_fmt_usd(market.market_cap_usd) if market else '—'}",
        f"💧 <b>流动性</b>      {_fmt_usd(market.liquidity_usd) if market else '—'}",
        f"⏱ <b>交易对年龄</b>  {_fmt_age(market.token_age_minutes) if market else '—'}",
    ])
    risk_lines = "\n".join(_security_status(activity.security))

    return (
        "🚨 <b>SMART MONEY ALERT</b>\n"
        "<i>智能资金异动提醒</i>\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"{icon} <b>{event_cn}信号</b>  ·  <code>{_esc(event_en)}</code>\n\n"
        f"{token_line}\n"
        f"<code>{chain}</code>  ·  {route_line}\n"
        f"👤 <b>{_esc(activity.wallet_label)}</b>{source_line}\n"
        f"📝 <b>判断：</b>{reason_line}\n\n"
        f"{market_lines}\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "📡 <b>交易明细</b>\n"
        f"{flow}\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "👥 <b>Smart Money 共识</b>\n"
        f"{chr(10).join(smart_lines)}\n"
        f"<b>合计：</b> {smart_total}  ·  {window}\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🛡 <b>风险概览</b>\n"
        f"<code>{_esc(risk_lines)}</code>\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"⭐ <b>信号评分  {score} / 100</b>\n"
        f"{_score_bar(score)}"
    )


def alert_buttons(activity: WalletActivity) -> dict[str, Any]:
    buttons: list[list[dict[str, str]]] = []
    if activity.action_token and activity.chain.lower() == "bsc":
        buttons.append([
            {"text": "📈 看图", "url": f"https://dexscreener.com/bsc/{activity.action_token}"},
            {"text": "📄 合约", "url": f"https://bscscan.com/token/{activity.action_token}"},
        ])
    buttons.append([
        {"text": "👛 钱包", "url": activity.wallet_url},
        {"text": "🔗 交易详情", "url": activity.explorer_url},
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
