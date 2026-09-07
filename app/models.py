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
class PoolActivity:
    address: str
    version: str
    sender: str
    recipient: str
    amount0_in: int
    amount1_in: int
    amount0_out: int
    amount1_out: int
    token0: str | None = None
    token1: str | None = None
    token0_symbol: str | None = None
    token1_symbol: str | None = None
    factory: str | None = None
    protocol: str | None = None


@dataclass
class MarketSnapshot:
    price_usd: float | None = None
    market_cap_usd: float | None = None
    liquidity_usd: float | None = None
    trade_value_usd: float | None = None
    pair_created_at: datetime | None = None
    pair_address: str | None = None
    dex_id: str | None = None
    source: str = ""

    @property
    def token_age_minutes(self) -> int | None:
        if not self.pair_created_at:
            return None
        seconds = max(0, (datetime.now(self.pair_created_at.tzinfo) - self.pair_created_at).total_seconds())
        return int(seconds // 60)


@dataclass
class SecuritySnapshot:
    is_open_source: bool | None = None
    is_proxy: bool | None = None
    is_honeypot: bool | None = None
    cannot_sell_all: bool | None = None
    hidden_owner: bool | None = None
    owner_change_balance: bool | None = None
    buy_tax: float | None = None
    sell_tax: float | None = None
    top_holder_percent: float | None = None
    creator_address: str | None = None
    risk_flags: list[str] = field(default_factory=list)
    source: str = ""

    @property
    def high_risk(self) -> bool:
        return bool(
            self.is_honeypot
            or self.cannot_sell_all
            or self.hidden_owner
            or self.owner_change_balance
            or (self.sell_tax is not None and self.sell_tax > 10)
        )


@dataclass
class SmartMoneyParticipant:
    wallet_label: str
    wallet_address: str
    event_type: str
    trade_value_usd: float = 0.0


@dataclass
class SmartMoneyAggregate:
    token: str
    event_type: str
    participants: list[SmartMoneyParticipant] = field(default_factory=list)
    total_value_usd: float = 0.0
    window_minutes: int = 60


@dataclass
class SignalScore:
    score: float
    components: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)


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
    wallet_source: str = ""
    wallet_weight: float = 1.0
    transfers: list[TokenTransfer] = field(default_factory=list)
    pair_swaps: list[PoolActivity] = field(default_factory=list)
    route_inputs: list[str] = field(default_factory=list)
    route_outputs: list[str] = field(default_factory=list)
    event_type: EventType = "UNKNOWN"
    dex: str | None = None
    router_name: str | None = None
    action_token: str | None = None
    quote_token: str | None = None
    confidence: float = 0.0
    analysis_reason: str = ""
    market: MarketSnapshot | None = None
    security: SecuritySnapshot | None = None
    smart_money: SmartMoneyAggregate | None = None
    signal: SignalScore | None = None

    @property
    def explorer_url(self) -> str:
        if self.chain == "bsc":
            return f"https://bscscan.com/tx/{self.tx_hash}"
        return f"https://explorer.mainnet.rpc.robinhood.com/tx/{self.tx_hash}"

    @property
    def wallet_url(self) -> str:
        if self.chain == "bsc":
            return f"https://bscscan.com/address/{self.wallet}"
        return f"https://explorer.mainnet.rpc.robinhood.com/address/{self.wallet}"

    @staticmethod
    def format_amount(transfer: TokenTransfer) -> str:
        amount = transfer.amount
        if amount is None:
            return f"raw={transfer.raw_amount}"
        return format(amount.normalize(), "f") if amount else "0"

    def symbol_for(self, token: str | None) -> str | None:
        if not token:
            return None
        normalized = token.lower()
        transfer = next((item for item in self.transfers if item.token.lower() == normalized), None)
        if transfer and transfer.symbol:
            return transfer.symbol
        for pool in self.pair_swaps:
            if pool.token0 and pool.token0.lower() == normalized:
                return pool.token0_symbol or token
            if pool.token1 and pool.token1.lower() == normalized:
                return pool.token1_symbol or token
        if transfer:
            return transfer.display_asset
        return token

    def wallet_flows(self) -> tuple[list[TokenTransfer], list[TokenTransfer]]:
        address = self.wallet.lower()
        incoming = [item for item in self.transfers if item.to_address.lower() == address]
        outgoing = [item for item in self.transfers if item.from_address.lower() == address]
        return incoming, outgoing

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
                f"{direction} {item.display_asset} {self.format_amount(item)}"
            )
        transfers = "\n".join(transfer_lines) or "无 ERC-20 Transfer 日志"
        score = self.signal.score if self.signal else self.confidence * 100
        dex_line = f"DEX: {self.dex}\n" if self.dex else ""
        router_line = f"Router: {self.router_name}\n" if self.router_name else ""
        token_line = ""
        if self.action_token:
            token_line = f"Token: {self.symbol_for(self.action_token)}"
            if self.quote_token:
                token_line += f" / Quote: {self.symbol_for(self.quote_token)}"
            token_line += "\n"
        return (
            f"{self.event_type} | {self.wallet_label} | {self.chain}\n"
            f"{dex_line}{router_line}{token_line}Score: {score:.0f}/100\n"
            f"判断: {self.analysis_reason}\n"
            f"Tx: {self.tx_hash}\n"
            f"Block: {self.block_number}\n"
            f"{transfers}\n"
            f"{self.explorer_url}"
        )
