from app.chains.rpc import TRANSFER_TOPIC
from app.parsers.token import TRANSFER_TOPIC as TOKEN_TRANSFER_TOPIC
from app.parsers.token import TRANSFER_TOPIC_ALIASES


def test_transfer_topic_is_canonical():
    expected = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    assert TRANSFER_TOPIC == expected
    assert TOKEN_TRANSFER_TOPIC == expected
    assert expected in TRANSFER_TOPIC_ALIASES
