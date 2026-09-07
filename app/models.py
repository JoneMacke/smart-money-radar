from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

EventType = Literal["SWAP", "TOKEN_IN", "TOKEN_OUT", "CONTRACT_CALL", "NATIVE_SEND", "UNKNOWN"]


@dataclass
class TokenTransfer:
    token: str
    from_address: str
    to_address: str
    raw_amount: int
    log_index: int


@dataclass
class WalletActivity:
    chain: str
    wallet_label: str
    wallet: str
    tx_hash: str
    block_number: int
    timestamp: datetime
    tx_from: str
    tx_to: str | None
    native_value_wei: int
    transfers: list[TokenTransfer] = field(default_factory=list)
    event_type: EventType = "UNKNOWN"

    @property
    def explorer_url(self) -> str:
        if self.chain == "bsc":
            return f"https://bscscan.com/tx/{self.tx_hash}"
        return f"https://explorer.mainnet.rpc.robinhood.com/tx/{self.tx_hash}"

    def short_text(self) -> str:
        transfer_lines = []
        for item in self.transfers[:6]:
            direction = "→" if item.from_address.lower() == self.wallet.lower() else "←"
            transfer_lines.append(
                f"{direction} {item.token[:10]}… amount(raw)={item.raw_amount}"
            )
        transfers = "\n".join(transfer_lines) or "无 ERC-20 Transfer 日志"
        return (
            f"{self.event_type} | {self.wallet_label} | {self.chain}\n"
            f"Tx: {self.tx_hash}\n"
            f"Block: {self.block_number}\n"
            f"{transfers}\n"
            f"{self.explorer_url}"
        )


