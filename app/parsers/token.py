from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

# ERC-20 Transfer(address,address,uint256) topic used by standard contracts.
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# Kept as a set so chain-specific exact aliases can be added deliberately later.
TRANSFER_TOPIC_ALIASES = frozenset({TRANSFER_TOPIC})


def _address(topic: str) -> str:
    return "0x" + topic[-40:]


@dataclass
class TokenTransfer:
    token: str
    from_address: str
    to_address: str
    raw_amount: int
    log_index: int
    symbol: str | None = None
    name: str | None = None
    decimals: int | None = None

    @property
    def amount(self) -> Decimal | None:
        if self.decimals is None:
            return None
        return Decimal(self.raw_amount) / (Decimal(10) ** self.decimals)

    @property
    def display_asset(self) -> str:
        return self.symbol or self.token


def parse_transfers(receipt: dict[str, Any]) -> list[TokenTransfer]:
    transfers: list[TokenTransfer] = []
    for log in receipt.get("logs", []):
        topics = log.get("topics", [])
        if len(topics) < 3 or str(topics[0]).lower() not in TRANSFER_TOPIC_ALIASES:
            continue
        try:
            transfers.append(
                TokenTransfer(
                    token=str(log["address"]).lower(),
                    from_address=_address(str(topics[1])).lower(),
                    to_address=_address(str(topics[2])).lower(),
                    raw_amount=int(str(log.get("data", "0x0")), 16),
                    log_index=int(str(log.get("logIndex", "0x0")), 16),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return transfers
