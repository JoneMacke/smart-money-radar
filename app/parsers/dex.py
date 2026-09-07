from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from app.chains.rpc import JsonRpcClient
from app.config import Wallet
from app.parsers.router import DexRegistry, load_dex_registry
from app.parsers.token import TokenTransfer, parse_transfers
from app.services.metadata import OnchainMetadataService, metadata_service_for

V2_SWAP_TOPIC = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
PANCAKE_V3_SWAP_TOPIC = "0x19b47279256b2a23a1665c810c8d55a1758940ee09377d4f8d26497a3577dc83"
UNISWAP_V3_SWAP_TOPIC = "0xc42079f94a6350d7e6235f291749249f928cc2ac818eb64e9e47a7331990c366"
V3_SWAP_TOPICS = frozenset({PANCAKE_V3_SWAP_TOPIC, UNISWAP_V3_SWAP_TOPIC})
WBNB_ADDRESS = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"

# Fallback when config/dexes.yaml is unavailable.
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
    token0: str | None = None
    token1: str | None = None
    token0_symbol: str | None = None
    token1_symbol: str | None = None
    factory: str | None = None
    protocol: str | None = None
    version: str = "v2"

    def input_tokens(self) -> list[str]:
        result: list[str] = []
        if self.amount0_in and self.token0:
            result.append(self.token0)
        if self.amount1_in and self.token1:
            result.append(self.token1)
        return result

    def output_tokens(self) -> list[str]:
        result: list[str] = []
        if self.amount0_out and self.token0:
            result.append(self.token0)
        if self.amount1_out and self.token1:
            result.append(self.token1)
        return result


@dataclass
class DexAnalysis:
    event_type: str = "UNKNOWN"
    router: str | None = None
    router_name: str | None = None
    protocol: str = "Unknown DEX"
    transfers: list[TokenTransfer] = field(default_factory=list)
    wallet_in: list[TokenTransfer] = field(default_factory=list)
    wallet_out: list[TokenTransfer] = field(default_factory=list)
    pair_swaps: list[PairSwap] = field(default_factory=list)
    quote_in: list[TokenTransfer] = field(default_factory=list)
    quote_out: list[TokenTransfer] = field(default_factory=list)
    asset_in: list[TokenTransfer] = field(default_factory=list)
    asset_out: list[TokenTransfer] = field(default_factory=list)
    route_inputs: list[str] = field(default_factory=list)
    route_outputs: list[str] = field(default_factory=list)
    action_token: str | None = None
    quote_token: str | None = None
    confidence: float = 0.0
    reason: str = ""


def _uint(data: str, offset: int) -> int:
    raw = str(data).removeprefix("0x")
    start = offset * 64
    return int(raw[start : start + 64], 16)


def _topic_address(topic: Any) -> str:
    return ("0x" + str(topic)[-40:]).lower()


def _signed_uint(data: str, offset: int) -> int:
    value = _uint(data, offset)
    return value - (1 << 256) if value >= (1 << 255) else value


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
                    pair=str(log["address"]).lower(),
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


def parse_v3_swaps(receipt: dict[str, Any]) -> list[PairSwap]:
    """Decode Uniswap-compatible and PancakeSwap V3 Swap logs."""
    swaps: list[PairSwap] = []
    for log in receipt.get("logs", []):
        topics = log.get("topics", [])
        if len(topics) < 3 or str(topics[0]).lower() not in V3_SWAP_TOPICS:
            continue
        data = str(log.get("data", "0x"))
        if len(data.removeprefix("0x")) < 128:
            continue
        try:
            amount0 = _signed_uint(data, 0)
            amount1 = _signed_uint(data, 1)
            swaps.append(
                PairSwap(
                    pair=str(log["address"]).lower(),
                    sender=_topic_address(topics[1]),
                    recipient=_topic_address(topics[2]),
                    amount0_in=max(amount0, 0),
                    amount1_in=max(amount1, 0),
                    amount0_out=abs(min(amount0, 0)),
                    amount1_out=abs(min(amount1, 0)),
                    version="v3",
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return swaps


def parse_pool_swaps(receipt: dict[str, Any]) -> list[PairSwap]:
    return sorted(
        [*parse_v2_swaps(receipt), *parse_v3_swaps(receipt)],
        key=lambda swap: next(
            (
                int(str(log.get("logIndex", "0x0")), 16)
                for log in receipt.get("logs", [])
                if str(log.get("address", "")).lower() == swap.pair
                and str((log.get("topics") or [""])[0]).lower()
                in {V2_SWAP_TOPIC, *V3_SWAP_TOPICS}
            ),
            0,
        ),
    )


def _native_value(tx: dict[str, Any]) -> int:
    raw = tx.get("value", "0x0")
    return raw if isinstance(raw, int) else int(str(raw), 16)


def _wallet_flows(wallet: Wallet, transfers: list[TokenTransfer]) -> tuple[list[TokenTransfer], list[TokenTransfer]]:
    address = wallet.normalized_address
    return (
        [t for t in transfers if t.to_address == address],
        [t for t in transfers if t.from_address == address],
    )


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.lower() for value in values if value))


def _route_boundaries(swaps: list[PairSwap]) -> tuple[list[str], list[str]]:
    inputs = [token for swap in swaps for token in swap.input_tokens()]
    outputs = [token for swap in swaps for token in swap.output_tokens()]
    input_set, output_set = set(inputs), set(outputs)
    starts = [token for token in inputs if token not in output_set]
    ends = [token for token in outputs if token not in input_set]
    return _unique(starts or inputs[:1]), _unique(ends or outputs[-1:])


def _classify(
    analysis: DexAnalysis,
    tx: dict[str, Any],
    quote_tokens: dict[str, str],
) -> None:
    quote_set = set(quote_tokens)
    native_value = _native_value(tx)
    route_input_quotes = [t for t in analysis.route_inputs if t in quote_set]
    route_output_quotes = [t for t in analysis.route_outputs if t in quote_set]
    route_input_assets = [t for t in analysis.route_inputs if t not in quote_set]
    route_output_assets = [t for t in analysis.route_outputs if t not in quote_set]

    if analysis.asset_in and (analysis.quote_out or native_value > 0):
        analysis.event_type, analysis.confidence = "BUY", 0.95
        analysis.reason = "非报价 Token 流入钱包，同时报价 Token 或原生 BNB 流出"
        analysis.action_token = analysis.asset_in[0].token
        analysis.quote_token = analysis.quote_out[0].token if analysis.quote_out else WBNB_ADDRESS
    elif analysis.asset_out and analysis.quote_in:
        analysis.event_type, analysis.confidence = "SELL", 0.95
        analysis.reason = "非报价 Token 流出钱包，同时报价 Token 流入"
        analysis.action_token = analysis.asset_out[0].token
        analysis.quote_token = analysis.quote_in[0].token
    elif analysis.asset_out and route_output_quotes:
        analysis.event_type, analysis.confidence = "SELL", 0.88
        analysis.reason = "钱包卖出非报价 Token，DEX 路径最终输出报价 Token"
        analysis.action_token = analysis.asset_out[0].token
        analysis.quote_token = route_output_quotes[0]
    elif native_value > 0 and route_output_assets:
        analysis.event_type, analysis.confidence = "BUY", 0.88
        analysis.reason = "交易支付原生 BNB，DEX 路径最终输出非报价 Token"
        analysis.action_token = route_output_assets[-1]
        analysis.quote_token = next((t for t in analysis.route_inputs if t in quote_set), None)
    elif route_input_quotes and route_output_assets:
        analysis.event_type, analysis.confidence = "BUY", 0.82
        analysis.reason = "DEX 路径从报价 Token 转为非报价 Token"
        analysis.action_token = route_output_assets[-1]
        analysis.quote_token = route_input_quotes[0]
    elif route_input_assets and route_output_quotes:
        analysis.event_type, analysis.confidence = "SELL", 0.82
        analysis.reason = "DEX 路径从非报价 Token 转为报价 Token"
        analysis.action_token = route_input_assets[0]
        analysis.quote_token = route_output_quotes[-1]
    elif analysis.asset_in and analysis.asset_out:
        analysis.event_type, analysis.confidence = "SWAP", 0.8
        analysis.reason = "钱包同时有非报价 Token 流入和流出"
    elif analysis.pair_swaps:
        analysis.event_type, analysis.confidence = "SWAP", 0.65
        analysis.reason = "检测到 DEX Pool Swap，但无法确定完整报价方向"
    elif analysis.transfers or native_value:
        analysis.event_type, analysis.confidence = "TRANSFER", 0.5
        analysis.reason = "检测到钱包转账，但缺少完整 Swap 对手流"
    else:
        analysis.event_type, analysis.confidence = "CONTRACT_CALL", 0.2
        analysis.reason = "合约调用未产生可识别 Token 流"


def analyze_bsc_transaction(
    wallet: Wallet,
    tx: dict[str, Any],
    receipt: dict[str, Any],
    quote_tokens: dict[str, str] | None = None,
) -> DexAnalysis:
    """Perform synchronous first-pass parsing before on-chain metadata enrichment."""
    quotes = quote_tokens or BSC_QUOTE_TOKENS
    transfers = parse_transfers(receipt)
    wallet_in, wallet_out = _wallet_flows(wallet, transfers)
    analysis = DexAnalysis(
        router=str(tx.get("to")).lower() if tx.get("to") else None,
        transfers=transfers,
        wallet_in=wallet_in,
        wallet_out=wallet_out,
        pair_swaps=parse_pool_swaps(receipt),
        quote_in=[t for t in wallet_in if t.token in quotes],
        quote_out=[t for t in wallet_out if t.token in quotes],
        asset_in=[t for t in wallet_in if t.token not in quotes],
        asset_out=[t for t in wallet_out if t.token not in quotes],
    )
    _classify(analysis, tx, quotes)
    return analysis


async def enrich_bsc_analysis(
    analysis: DexAnalysis,
    tx: dict[str, Any],
    rpc: JsonRpcClient,
    registry: DexRegistry | None = None,
    metadata: OnchainMetadataService | None = None,
) -> DexAnalysis:
    """Resolve Token metadata, Router identity, and Pair token0/token1 data."""
    registry = registry or load_dex_registry()
    metadata = metadata or metadata_service_for(rpc)
    quote_tokens = registry.quote_tokens or BSC_QUOTE_TOKENS

    token_addresses = _unique([transfer.token for transfer in analysis.transfers])
    token_metadata = await asyncio.gather(*(metadata.token(address) for address in token_addresses))
    metadata_by_address = {item.address: item for item in token_metadata}
    for transfer in analysis.transfers:
        item = metadata_by_address.get(transfer.token)
        if item:
            transfer.symbol = item.symbol
            transfer.name = item.name
            transfer.decimals = item.decimals

    pair_metadata = await asyncio.gather(*(metadata.pair(swap.pair) for swap in analysis.pair_swaps))
    protocols: list[str] = []
    for swap, pair in zip(analysis.pair_swaps, pair_metadata, strict=False):
        if not pair:
            continue
        swap.token0, swap.token1, swap.factory = pair.token0, pair.token1, pair.factory
        swap.token0_symbol = pair.token0_metadata.symbol if pair.token0_metadata else None
        swap.token1_symbol = pair.token1_metadata.symbol if pair.token1_metadata else None
        factory = registry.factory(pair.factory)
        fallback = f"{swap.version.upper()}-compatible DEX"
        swap.protocol = factory.name if factory else fallback
        protocols.append(swap.protocol)

    router = registry.router(analysis.router)
    analysis.router_name = router.name if router else None
    analysis.route_inputs, analysis.route_outputs = _route_boundaries(analysis.pair_swaps)
    if protocols:
        analysis.protocol = protocols[0] if len(set(protocols)) == 1 else "Multi-DEX route"
    elif router:
        analysis.protocol = router.name
    elif analysis.router:
        analysis.protocol = "Unknown BSC DEX / aggregator"

    # Rebuild quote/non-quote buckets using configured quote tokens, then classify again.
    analysis.quote_in = [t for t in analysis.wallet_in if t.token in quote_tokens]
    analysis.quote_out = [t for t in analysis.wallet_out if t.token in quote_tokens]
    analysis.asset_in = [t for t in analysis.wallet_in if t.token not in quote_tokens]
    analysis.asset_out = [t for t in analysis.wallet_out if t.token not in quote_tokens]
    _classify(analysis, tx, quote_tokens)
    return analysis
