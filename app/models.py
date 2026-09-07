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
    router_name: str | None = None
    action_token: str | None = None
    quote_token: str | None = None
    confidence: float = 0.0
    analysis_reason: str = ""

    @property
    def explorer_url(self) -> str:
        if self.chain == "bsc":
            return f"https://bscscan.com/tx/{self.tx_hash}"
        return f"https://explorer.mainnet.rpc.robinhood.com/tx/{self.tx_hash}"

    @staticmethod
    def _format_amount(transfer: TokenTransfer) -> str:
        amount = transfer.amount
        if amount is None:
            return f"raw={transfer.raw_amount}"
        return format(amount.normalize(), "f") if amount else "0"

    def _symbol_for(self, token: str | None) -> str | None:
        if not token:
            return None
        transfer = next((item for item in self.transfers if item.token == token), None)
        return transfer.display_asset if transfer else token

    def short_text(self) -> str:
        transfer_lines = []
        for item in self.transfers[:8]:
            if item.from_address.lower() == self.wallet.lower():
                direction = "→"
            elif item.to_address.lower() == self.wallet.lower():
                direction = "←"
            else:
                direction = "·"
            transfer_lines.append(
                f"{direction} {item.display_asset} {self._format_amount(item)}"
            )
        transfers = "\n".join(transfer_lines) or "无 ERC-20 Transfer 日志"
        dex_line = f"DEX: {self.dex}\n" if self.dex else ""
        router_line = f"Router: {self.router_name}\n" if self.router_name else ""
        token_line = ""
        if self.action_token:
            token_line = f"Token: {self._symbol_for(self.action_token)}"
            if self.quote_token:
                token_line += f" / Quote: {self._symbol_for(self.quote_token)}"
            token_line += "\n"
        confidence_line = f"Confidence: {self.confidence:.0%}\n" if self.confidence else ""
        reason_line = f"判断: {self.analysis_reason}\n" if self.analysis_reason else ""
        return (
            f"{self.event_type} | {self.wallet_label} | {self.chain}\n"
            f"{dex_line}{router_line}{token_line}{confidence_line}{reason_line}"
            f"Tx: {self.tx_hash}\n"
            f"Block: {self.block_number}\n"
            f"{transfers}\n"
            f"{self.explorer_url}"
        )
