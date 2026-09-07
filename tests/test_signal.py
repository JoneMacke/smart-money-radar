from datetime import datetime, timedelta, timezone

from app.models import MarketSnapshot, SecuritySnapshot, WalletActivity
from app.services.aggregation import SmartMoneyAggregator
from app.services.scoring import calculate_signal


def activity(label: str, wallet: str, tx: str, when: datetime) -> WalletActivity:
    return WalletActivity(
        chain="bsc", wallet_label=label, wallet=wallet, tx_hash=tx,
        block_number=1, timestamp=when, tx_from=wallet, tx_to=None,
        native_value_wei=0, event_type="BUY", action_token="0x" + "a" * 40,
        confidence=.9, wallet_weight=1.0,
    )


def test_smart_money_aggregation_groups_wallets_in_window():
    now = datetime.now(timezone.utc)
    aggregator = SmartMoneyAggregator(window_minutes=60)
    first = activity("KOL-A", "0x" + "1" * 40, "0x" + "1" * 64, now - timedelta(minutes=5))
    second = activity("KOL-B", "0x" + "2" * 40, "0x" + "2" * 64, now)
    first.market = MarketSnapshot(trade_value_usd=1000)
    second.market = MarketSnapshot(trade_value_usd=2500)
    aggregator.add(first)
    result = aggregator.add(second)
    assert result is not None
    assert len(result.participants) == 2
    assert result.total_value_usd == 3500


def test_signal_score_combines_market_consensus_and_security():
    now = datetime.now(timezone.utc)
    item = activity("KOL-A", "0x" + "1" * 40, "0x" + "1" * 64, now)
    item.market = MarketSnapshot(trade_value_usd=25000, liquidity_usd=200000)
    item.security = SecuritySnapshot(is_open_source=True, top_holder_percent=8)
    item.smart_money = SmartMoneyAggregator().aggregate(item)
    score = calculate_signal(item)
    assert 0 < score.score <= 100
    assert score.components["liquidity"] > 0
    assert score.components["risk"] == 10
