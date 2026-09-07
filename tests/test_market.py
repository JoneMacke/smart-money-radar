from datetime import datetime, timezone

from app.models import MarketSnapshot, WalletActivity
from app.parsers.token import TokenTransfer
from app.services.market import estimate_trade_value


def test_trade_value_uses_action_token_amount_for_buy():
    wallet = "0x" + "1" * 40
    token = "0x" + "2" * 40
    item = WalletActivity(
        chain="bsc", wallet_label="test", wallet=wallet, tx_hash="0x" + "a" * 64,
        block_number=1, timestamp=datetime.now(timezone.utc), tx_from=wallet,
        tx_to=None, native_value_wei=0, event_type="BUY", action_token=token,
        market=MarketSnapshot(price_usd=2.5), transfers=[
            TokenTransfer(token, "0x" + "3" * 40, wallet, 4_000_000, 1, "TEST", decimals=6),
        ],
    )
    assert estimate_trade_value(item) == 10
