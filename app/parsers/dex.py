from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.config import Wallet
from app.parsers.token import TokenTransfer, parse_transfers

# Standard V2 Pair Swap event (PancakeSwap V2-compatible pools).
V2_SWAP_TOPIC = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"

BSC_QUOTE_TOKENS = {
    "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c": "WBNB",
    "0x55d398326f99059ff775485246999027b3197955": "USDT",
    "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": "USDC",
    "0xe9e7cea3dedca5984780bafc599bd69add087d56": "BUSD",
}


@dataclass
class PairSwap:
    pair: str
    sender: str
    recipient: str
    amount0_in: int
    amount1_in: int
    amount0_out: int
    amount1_out: int


@dataclass
class DexAnalysis:
    event_type: str = "UNKNOWN"
    router: str | None = None
    protocol: str = "Unknown DEX"
    transfers: list[TokenTransfer] = field(default_factory=list)
    wallet_in: list[TokenTransfer] = field(default_factory=list)
    wallet_out: list[TokenTransfer] = field(default_factory=list)
    pair_swaps: list[PairSwap] = field(default_factory=list)
    quote_in: list[TokenTransfer] = field(default_factory=list)
    quote_out: list[TokenTransfer] = field(default_factory=list)
    asset_in: list[TokenTransfer] = field(default_factory=list)
    asset_out: list[TokenTransfer] = field(default_factory=list)
    confidence: float = 0.0
    reason: str = ""


def _uint(data: str, offset: int) -> int:
    raw = str(data).removeprefix("0x")
    start = offset * 64
    return int(raw[start : start + 64], 16)


def _topic_address(topic: Any) -> str:
    return "0x" + str(topic)[-40:]


def parse_v2_swaps(receipt: dict[str, Any]) -> list[PairSwap]:
    swaps: list[PairSwap] = []
    for log in receipt.get("logs", []):
        topics = log.get("topics", [])
        if len(topics) < 3 or str(topics[0]).lower() != V2_SWAP_TOPIC:
            continue
        data = str(log.get("data", "0x"))
        if len(data.removeprefix("0x")) < 256:
            continue
        try:
            swaps.append(
                PairSwap(
                    pair=str(log["address"]),
                    sender=_topic_address(topics[1]),
                    recipient=_topic_address(topics[2]),
                    amount0_in=_uint(data, 0),
                    amount1_in=_uint(data, 1),
                    amount0_out=_uint(data, 2),
                    amount1_out=_uint(data, 3),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return swaps


def _is_quote(token: str, quote_tokens: dict[str, str] | None = None) -> bool:
    known_quotes = BSC_QUOTE_TOKENS if quote_tokens is None else quote_tokens
    return token.lower() in known_quotes


def _native_value(tx: dict[str, Any]) -> int:
    raw = tx.get("value", "0x0")
    if isinstance(raw, int):
        return raw
    return int(str(raw), 16)


def _wallet_flow_transfers(
    wallet: Wallet,
    transfers: list[TokenTransfer],
) -> tuple[list[TokenTransfer], list[TokenTransfer]]:
    address = wallet.normalized_address
    wallet_in = [t for t in transfers if t.to_address.lower() == address]
    wallet_out = [t for t in transfers if t.from_address.lower() == address]
    return wallet_in, wallet_out


def analyze_bsc_transaction(
    wallet: Wallet,
    tx: dict[str, Any],
    receipt: dict[str, Any],
    quote_tokens: dict[str, str] | None = None,
) -> DexAnalysis:
    """Infer a BSC DEX action from wallet-facing flows and V2 Pair Swap logs.

    BUY/SELL are emitted only when the wallet-facing flow supports that
    conclusion. Complex aggregators may be reported as SWAP until their router
    ABI and pair metadata are decoded.
    """
    transfers = parse_transfers(receipt)
    wallet_in, wallet_out = _wallet_flow_transfers(wallet, transfers)
    native_value = _native_value(tx)
    quote_in = [t for t in wallet_in if _is_quote(t.token, quote_tokens)]
    quote_out = [t for t in wallet_out if _is_quote(t.token, quote_tokens)]
    asset_in = [t for t in wallet_in if not _is_quote(t.token, quote_tokens)]
    asset_out = [t for t in wallet_out if not _is_quote(t.token, quote_tokens)]
    pair_swaps = parse_v2_swaps(receipt)
    router = tx.get("to")

    # BUY: quote/native value leaves the wallet and a non-quote token arrives.
    # SELL: a non-quote token leaves and a known quote token arrives.
    if asset_in and (quote_out or native_value > 0):
        event_type, confidence, reason = (
            "BUY", 0.95, "非报价 Token 流入钱包，同时报价 Token 或原生 BNB 流出"
        )
    elif asset_out and quote_in:
        event_type, confidence, reason = (
            "SELL", 0.95, "非报价 Token 流出钱包，同时报价 Token 流入"
        )
    elif asset_in and asset_out:
        event_type, confidence, reason = (
            "SWAP", 0.8, "钱包同时有非报价 Token 流入和流出"
        )
    elif pair_swaps and (asset_in or asset_out):
        event_type, confidence, reason = (
            "SWAP", 0.65, "检测到 V2 Pair Swap，但钱包流向不完整"
        )
    elif transfers or native_value:
        event_type, confidence, reason = (
            "TRANSFER", 0.5, "检测到钱包转账，但缺少完整 Swap 对手流"
        )
    else:
        event_type, confidence, reason = (
            "CONTRACT_CALL", 0.2, "合约调用未产生可识别 Token 流"
        )

    protocol = "V2-compatible DEX" if pair_swaps else (
        "Unknown BSC DEX / aggregator" if router else "Unknown DEX"
    )
    return DexAnalysis(
        event_type=event_type,
        router=str(router) if router else None,
        protocol=protocol,
        transfers=transfers,
        wallet_in=wallet_in,
        wallet_out=wallet_out,
        pair_swaps=pair_swaps,
        quote_in=quote_in,
        quote_out=quote_out,
        asset_in=asset_in,
        asset_out=asset_out,
        confidence=confidence,
        reason=reason,
    )
