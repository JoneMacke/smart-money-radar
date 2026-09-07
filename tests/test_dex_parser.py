from app.config import Wallet
from app.parsers.dex import (
    PANCAKE_V3_SWAP_TOPIC, V2_SWAP_TOPIC, analyze_bsc_transaction, parse_v2_swaps, parse_v3_swaps,
)
from app.parsers.token import TRANSFER_TOPIC, parse_transfers

WALLET = "0x1111111111111111111111111111111111111111"
OTHER = "0x3333333333333333333333333333333333333333"
ROUTER = "0x4444444444444444444444444444444444444444"
WBNB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
TOKEN = "0x2222222222222222222222222222222222222222"
V3_PAIR = "0x7777777777777777777777777777777777777777"


def word(value: int) -> str:
    return f"{value:064x}"


def transfer_log(token, sender, recipient, amount, topic=TRANSFER_TOPIC):
    return {
        "address": token,
        "topics": [
            topic,
            "0x" + "0" * 24 + sender[2:],
            "0x" + "0" * 24 + recipient[2:],
        ],
        "data": "0x" + word(amount),
    }


def v2_swap_log(pair, sender, recipient, amount0_in, amount1_in, amount0_out, amount1_out):
    data = "0x" + "".join(word(x) for x in (
        amount0_in, amount1_in, amount0_out, amount1_out,
    ))
    return {
        "address": pair,
        "topics": [
            V2_SWAP_TOPIC,
            "0x" + "0" * 24 + sender[2:],
            "0x" + "0" * 24 + recipient[2:],
        ],
        "data": data,
    }


def wallet():
    return Wallet(label="test", address=WALLET, chains=("bsc",))


def test_buy_inferred_from_quote_out_and_asset_in():
    receipt = {"logs": [
        transfer_log(WBNB, WALLET, OTHER, 10),
        transfer_log(TOKEN, OTHER, WALLET, 1000),
    ]}
    analysis = analyze_bsc_transaction(wallet(), {"to": ROUTER, "value": "0x0"}, receipt)
    assert analysis.event_type == "BUY"
    assert analysis.confidence == 0.95
    assert len(analysis.transfers) == 2


def test_buy_inferred_from_native_bnb_and_asset_in():
    receipt = {"logs": [transfer_log(TOKEN, OTHER, WALLET, 1000)]}
    analysis = analyze_bsc_transaction(wallet(), {"to": ROUTER, "value": hex(10)}, receipt)
    assert analysis.event_type == "BUY"


def test_sell_inferred_from_asset_out_and_quote_in():
    receipt = {"logs": [
        transfer_log(TOKEN, WALLET, OTHER, 1000),
        transfer_log(WBNB, OTHER, WALLET, 10),
    ]}
    analysis = analyze_bsc_transaction(wallet(), {"to": ROUTER, "value": "0x0"}, receipt)
    assert analysis.event_type == "SELL"


def test_complex_aggregator_flow_is_swap_not_contract_call():
    alias = TRANSFER_TOPIC
    receipt = {"logs": [
        transfer_log(TOKEN, OTHER, WALLET, 1000, topic=alias),
        transfer_log(TOKEN, WALLET, ROUTER, 100, topic=alias),
        v2_swap_log(
            "0x5555555555555555555555555555555555555555",
            ROUTER,
            OTHER,
            0, 10, 1000, 0,
        ),
    ]}
    analysis = analyze_bsc_transaction(wallet(), {"to": ROUTER, "value": "0x0"}, receipt)
    assert len(analysis.transfers) == 2
    assert len(analysis.pair_swaps) == 1
    assert analysis.event_type == "SWAP"


def test_v2_swap_payload_is_decoded():
    receipt = {"logs": [v2_swap_log(
        "0x5555555555555555555555555555555555555555",
        ROUTER,
        OTHER,
        1, 2, 3, 4,
    )]}
    swaps = parse_v2_swaps(receipt)
    assert len(swaps) == 1
    assert (swaps[0].amount0_in, swaps[0].amount1_in) == (1, 2)
    assert (swaps[0].amount0_out, swaps[0].amount1_out) == (3, 4)


def test_canonical_and_observed_transfer_topics_are_parsed():
    alias = TRANSFER_TOPIC
    receipt = {"logs": [
        transfer_log(TOKEN, WALLET, OTHER, 1000),
        transfer_log(TOKEN, OTHER, WALLET, 2000, topic=alias),
    ]}
    assert len(parse_transfers(receipt)) == 2
def signed_word(value: int) -> str:
    return f"{value % (1 << 256):064x}"

def test_pancakeswap_v3_swap_payload_is_decoded():
    receipt = {"logs": [{
        "address": V3_PAIR,
        "topics": [
            PANCAKE_V3_SWAP_TOPIC,
            "0x" + "0" * 24 + ROUTER[2:],
            "0x" + "0" * 24 + OTHER[2:],
        ],
        "data": "0x" + "".join([
            signed_word(1000), signed_word(-10), word(1), word(2),
            word(3), word(4), signed_word(-5), word(6), word(7),
        ]),
    }]}
    swaps = parse_v3_swaps(receipt)
    assert len(swaps) == 1
    assert swaps[0].version == "v3"
    assert (swaps[0].amount0_in, swaps[0].amount1_out) == (1000, 10)
    assert (swaps[0].amount1_in, swaps[0].amount0_out) == (0, 0)
