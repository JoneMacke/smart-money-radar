from __future__ import annotations

import asyncio

from app.config import Wallet
from app.listeners import blocks

WALLET = Wallet(
    label="test",
    address="0x1111111111111111111111111111111111111111",
    chains=("bsc",),
)
TOPIC = "0x" + "0" * 24 + WALLET.address[2:]
TX_HASH = "0x" + "a" * 64


class LogRpc:
    def __init__(self, logs=None):
        self.logs = logs or []
        self.calls = []

    async def get_logs(self, start, end, topics):
        self.calls.append(("logs", start, end, topics))
        return self.logs

    async def get_transaction(self, tx_hash):
        self.calls.append(("tx", tx_hash))
        return {
            "hash": tx_hash,
            "from": WALLET.address,
            "blockNumber": "0x10",
            "value": "0x0",
        }

    async def get_block(self, block_number, full_transactions=True):
        self.calls.append(("block", block_number, full_transactions))
        return {"timestamp": "0x64"}


def test_range_scan_stops_after_logs_when_no_wallet_activity():
    async def run():
        rpc = LogRpc()
        result = await blocks.activities_for_range("bsc", 10, 20, [WALLET], rpc)
        assert result == []
        assert [call[0] for call in rpc.calls] == ["logs", "logs"]
        assert rpc.calls[0][1:3] == (10, 20)

    asyncio.run(run())


def test_range_scan_fetches_only_candidate_transaction(monkeypatch):
    async def fake_activity(chain, wallet, tx, timestamp, rpc):
        return (chain, wallet.label, tx["hash"], timestamp)

    monkeypatch.setattr(blocks, "activity_from_transaction", fake_activity)

    async def run():
        receipt_log = {
            "transactionHash": TX_HASH,
            "topics": [blocks.TRANSFER_TOPIC, TOPIC, "0x" + "2" * 64],
        }
        rpc = LogRpc([receipt_log])
        result = await blocks.activities_for_range("bsc", 10, 20, [WALLET], rpc)
        assert result == [("bsc", "test", TX_HASH, 100)]
        assert sum(call[0] == "tx" for call in rpc.calls) == 1
        assert sum(call[0] == "block" for call in rpc.calls) == 1
        assert all(call[0] != "block" or call[2] is False for call in rpc.calls)

    asyncio.run(run())
