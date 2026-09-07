from __future__ import annotations

import math

from app.models import SignalScore, WalletActivity


def calculate_signal(activity: WalletActivity) -> SignalScore:
    """Calculate a 0-100 score from parse, wallet, market, consensus and risk inputs."""
    components: dict[str, float] = {}
    reasons: list[str] = []
    components["parse"] = min(30.0, activity.confidence * 30.0)
    components["wallet"] = min(15.0, max(0.0, activity.wallet_weight) * 15.0)

    trade_value = activity.market.trade_value_usd if activity.market else None
    if trade_value:
        components["size"] = min(15.0, max(0.0, math.log10(max(trade_value, 1)) / 6 * 15))
        reasons.append(f"交易估值约 ${trade_value:,.0f}")
    else:
        components["size"] = 0.0
        reasons.append("交易估值暂不可用")

    participants = len(activity.smart_money.participants) if activity.smart_money else 1
    components["consensus"] = min(15.0, participants * 5.0)
    if participants > 1:
        reasons.append(f"{participants} 个 Smart Money 钱包在窗口内同向交易")

    liquidity = activity.market.liquidity_usd if activity.market else None
    if liquidity:
        components["liquidity"] = min(15.0, max(0.0, math.log10(max(liquidity, 1)) / 6 * 15))
        reasons.append(f"流动性约 ${liquidity:,.0f}")
    else:
        components["liquidity"] = 0.0
        reasons.append("流动性暂不可用")

    security = activity.security
    if security is None:
        components["risk"] = 0.0
        reasons.append("尚未取得合约安全数据")
    elif security.high_risk:
        components["risk"] = 0.0
        reasons.append("合约安全检查发现风险")
    else:
        components["risk"] = 10.0
        reasons.append("合约安全检查未发现高风险项")

    score = round(min(100.0, sum(components.values())), 1)
    if activity.event_type in {"BUY", "SELL"}:
        reasons.insert(0, f"{activity.event_type} 方向识别置信度 {activity.confidence:.0%}")
    return SignalScore(score=score, components=components, reasons=reasons)
