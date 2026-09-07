from datetime import datetime, timezone

from app.models import WalletActivity
from app.notifiers.telegram import alert_buttons, format_alert
from app.parsers.token import TokenTransfer

WALLET = "0x1111111111111111111111111111111111111111"
OTHER = "0x4444444444444444444444444444444444444444"


def test_alert_uses_actionable_flow_and_buttons():
    activity = WalletActivity(
        chain="bsc", wallet_label="test", wallet=WALLET,
        tx_hash="0x" + "a" * 64, block_number=123, timestamp=datetime.now(timezone.utc),
        tx_from=WALLET, tx_to="0x2222222222222222222222222222222222222222",
        native_value_wei=0, wallet_source="GMGN", transfers=[
            TokenTransfer("0x3333333333333333333333333333333333333333", WALLET, OTHER, 250000000000000000, 1, "WBNB", decimals=18),
            TokenTransfer("0x5555555555555555555555555555555555555555", OTHER, WALLET, 1234500000, 2, "BREW", decimals=6),
        ], event_type="BUY", dex="PancakeSwap V3", router_name="PancakeSwap Smart Router",
        action_token="0x5555555555555555555555555555555555555555", quote_token="0x3333333333333333333333333333333333333333", confidence=.92,
    )
    text = format_alert(activity)
    assert "SMART MONEY ALERT" in text
    assert "BUY" in text and "BREW" in text and "0.25" in text
    assert alert_buttons(activity)["inline_keyboard"][0][0]["url"].endswith(activity.action_token)
