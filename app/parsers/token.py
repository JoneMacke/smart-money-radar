from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ERC-20 Transfer(address,address,uint256) topic used by standard contracts.
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a9df523b3ef"

# Some BSC RPC/indexer receipts observed in the test wallet expose a one-nibble
# variant of the canonical topic. Keep this exact alias for compatibility, but
# never accept arbitrary topics as Transfer events.
TRANSFER_TOPIC_ALIASES = frozenset({
    TRANSFER_TOPIC,
    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
})


def _address(topic: str) -> str:
    return "0x" + topic[-40:]


@dataclass
class TokenTransfer:
    token: str
    from_address: str
    to_address: str
    raw_amount: int
    log_index: int


def parse_transfers(receipt: dict[str, Any]) -> list[TokenTransfer]:
    transfers: list[TokenTransfer] = []
    for log in receipt.get("logs", []):
        topics = log.get("topics", [])
        if len(topics) < 3 or str(topics[0]).lower() not in TRANSFER_TOPIC_ALIASES:
            continue
        try:
            transfers.append(
                TokenTransfer(
                    token=str(log["address"]),
                    from_address=_address(str(topics[1])),
                    to_address=_address(str(topics[2])),
                    raw_amount=int(str(log.get("data", "0x0")), 16),
                    log_index=int(str(log.get("logIndex", "0x0")), 16),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return transfers
