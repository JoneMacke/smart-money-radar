from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from app.parsers.token import TokenTransfer

EventType = Literal[
    "BUY", "SELL", "SWAP", "TOKEN_IN", "TOKEN_OUT", "TRANSFER",
    "CONTRACT_CALL", "NATIVE_SEND", "UNKNOWN",
]


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
    dex: str | None = None
    confidence: float = 0.0
    analysis_reason: str = ""

    @property
    def explorer_url(self) -> str:
        if self.chain == "bsc":
            return f"https://bscscan.com/tx/{self.tx_hash}"
        return f"https://explorer.mainnet.rpc.robinhood.com/tx/{self.tx_hash}"

    def short_text(self) -> str:
        transfer_lines = []
        for item in self.transfers[:8]:
            direction = "→" if item.from_address.lower() == self.wallet.lower() else "←"
            transfer_lines.append(
                f"{direction} {item.token} amount(raw)={item.raw_amount}"
            )
        transfers = "\n".join(transfer_lines) or "无 ERC-20 Transfer 日志"
        dex_line = f"DEX: {self.dex}\n" if self.dex else ""
        confidence_line = f"Confidence: {self.confidence:.0%}\n" if self.confidence else ""
        reason_line = f"判断: {self.analysis_reason}\n" if self.analysis_reason else ""
        return (
            f"{self.event_type} | {self.wallet_label} | {self.chain}\n"
            f"{dex_line}{confidence_line}{reason_line}"
            f"Tx: {self.tx_hash}\n"
            f"Block: {self.block_number}\n"
            f"{transfers}\n"
            f"{self.explorer_url}"
        )
