from __future__ import annotations

import asyncio

from app.config import Wallet
from app.parsers.dex import analyze_bsc_transaction, enrich_bsc_analysis
from app.parsers.router import DexContract, DexRegistry
from app.parsers.token import TRANSFER_TOPIC
from app.services.metadata import (
    DECIMALS_SELECTOR,
    FACTORY_SELECTOR,
    NAME_SELECTOR,
    SYMBOL_SELECTOR,
    TOKEN0_SELECTOR,
    TOKEN1_SELECTOR,
    OnchainMetadataService,
    decode_abi_string,
)

WALLET = "0x1111111111111111111111111111111111111111"
TOKEN = "0x2222222222222222222222222222222222222222"
WBNB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
PAIR = "0x5555555555555555555555555555555555555555"
ROUTER = "0x4444444444444444444444444444444444444444"
FACTORY = "0x6666666666666666666666666666666666666666"
V2_SWAP_TOPIC = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"


def abi_string(value: str) -> str:
    encoded = value.encode("utf-8")
    padded = encoded + b"\x00" * ((32 - len(encoded) % 32) % 32)
    return "0x" + (32).to_bytes(32, "big").hex() + len(encoded).to_bytes(32, "big").hex() + padded.hex()


def abi_address(value: str) -> str:
    return "0x" + "0" * 24 + value[2:]


def abi_uint(value: int) -> str:
    return "0x" + f"{value:064x}"


def transfer_log(token: str, sender: str, recipient: str, amount: int) -> dict:
    return {
        "address": token,
        "topics": [
            TRANSFER_TOPIC,
            "0x" + "0" * 24 + sender[2:],
            "0x" + "0" * 24 + recipient[2:],
        ],
        "data": abi_uint(amount),
        "logIndex": "0x1",
    }


def swap_log() -> dict:
    values = (1000, 0, 0, 10)
    return {
        "address": PAIR,
        "topics": [
            V2_SWAP_TOPIC,
            "0x" + "0" * 24 + ROUTER[2:],
            "0x" + "0" * 24 + ROUTER[2:],
        ],
        "data": "0x" + "".join(f"{value:064x}" for value in values),
    }


class FakeRpc:
    def __init__(self) -> None:
        self.calls = 0
        self.responses = {
            (TOKEN, SYMBOL_SELECTOR): abi_string("MEME"),
            (TOKEN, NAME_SELECTOR): abi_string("Meme Token"),
            (TOKEN, DECIMALS_SELECTOR): abi_uint(18),
            (WBNB, SYMBOL_SELECTOR): abi_string("WBNB"),
            (WBNB, NAME_SELECTOR): abi_string("Wrapped BNB"),
            (WBNB, DECIMALS_SELECTOR): abi_uint(18),
            (PAIR, TOKEN0_SELECTOR): abi_address(TOKEN),
            (PAIR, TOKEN1_SELECTOR): abi_address(WBNB),
            (PAIR, FACTORY_SELECTOR): abi_address(FACTORY),
        }

    async def call(self, method, params):
        assert method == "eth_call"
        self.calls += 1
        call = params[0]
        return self.responses.get((call["to"].lower(), call["data"]))


def registry() -> DexRegistry:
    return DexRegistry(
        routers={ROUTER: DexContract(ROUTER, "Test Router", "aggregator")},
        factories={FACTORY: DexContract(FACTORY, "Test V2 DEX", "v2")},
        quote_tokens={WBNB: "WBNB"},
    )


def test_dynamic_abi_string_decoding():
    assert decode_abi_string(abi_string("屎币")) == "屎币"


def test_token_metadata_is_cached():
    async def run():
        rpc = FakeRpc()
        service = OnchainMetadataService(rpc)
        first = await service.token(TOKEN)
        second = await service.token(TOKEN)
        assert first.symbol == "MEME"
        assert first.decimals == 18
        assert first is second
        assert rpc.calls == 3

    asyncio.run(run())


def test_pair_metadata_and_route_classify_sell():
    async def run():
        rpc = FakeRpc()
        service = OnchainMetadataService(rpc)
        wallet = Wallet(label="test", address=WALLET, chains=("bsc",))
        receipt = {"logs": [
            transfer_log(TOKEN, WALLET, ROUTER, 1000),
            swap_log(),
        ]}
        tx = {"to": ROUTER, "value": "0x0"}
        analysis = analyze_bsc_transaction(wallet, tx, receipt, registry().quote_tokens)
        analysis = await enrich_bsc_analysis(
            analysis, tx, rpc, registry=registry(), metadata=service
        )
        assert analysis.event_type == "SELL"
        assert analysis.protocol == "Test V2 DEX"
        assert analysis.router_name == "Test Router"
        assert analysis.action_token == TOKEN
        assert analysis.quote_token == WBNB
        assert analysis.transfers[0].symbol == "MEME"
        assert analysis.transfers[0].amount is not None
        assert analysis.route_inputs == [TOKEN]
        assert analysis.route_outputs == [WBNB]
        assert analysis.pair_swaps[0].token0_symbol == "MEME"
        assert analysis.pair_swaps[0].token1_symbol == "WBNB"

    asyncio.run(run())
