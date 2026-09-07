from datetime import datetime, timezone

from app.models import SignalScore, WalletActivity
from app.services.alerts import parse_alert_event_types, should_send_alert


def activity(event_type: str, score: float) -> WalletActivity:
    return WalletActivity(
        chain="robinhood",
        wallet_label="test02",
        wallet="0x" + "1" * 40,
        tx_hash="0x" + "a" * 64,
        block_number=1,
        timestamp=datetime.now(timezone.utc),
        tx_from="0x" + "1" * 40,
        tx_to=None,
        native_value_wei=0,
        event_type=event_type,
        signal=SignalScore(score=score),
    )


def test_default_alert_filter_only_allows_actionable_events():
    assert should_send_alert(activity("BUY", 60))
    assert should_send_alert(activity("SELL", 85))
    assert should_send_alert(activity("SWAP", 70))
    assert not should_send_alert(activity("CONTRACT_CALL", 99))
    assert not should_send_alert(activity("BUY", 59.9))


def test_alert_filter_can_be_configured():
    assert parse_alert_event_types(" buy, sell ") == frozenset({"BUY", "SELL"})
    assert should_send_alert(activity("CONTRACT_CALL", 90), 60, "CONTRACT_CALL")
    assert not should_send_alert(activity("SWAP", 90), 60, "BUY,SELL")
