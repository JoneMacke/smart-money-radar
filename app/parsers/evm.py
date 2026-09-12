from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.chains.rpc import JsonRpcClient
from app.config import Wallet
from app.models import PoolActivity, WalletActivity
from app.parsers.dex import analyze_bsc_transaction, enrich_bsc_analysis
from app.parsers.token import parse_transfers

logger = logging.getLogger(__name__)


def classify(wallet: Wallet, tx: dict[str, Any], transfers: list[Any]) -> str:
    wallet_address = wallet.normalized_address
    outgoing = any(t.from_address.lower() == wallet_address for t in transfers)
    incoming = any(t.to_address.lower() == wallet_address for t in transfers)
    if outgoing and incoming:
        return "SWAP"
    if incoming:
        return "TOKEN_IN"
    if outgoing:
        return "TOKEN_OUT"
    if int(str(tx.get("value", "0x0")), 16) > 0:
        return "NATIVE_SEND"
    return "CONTRACT_CALL"


async def activity_from_transaction(
    chain: str,
    wallet: Wallet,
    tx: dict[str, Any],
    block_timestamp: int,
    rpc: JsonRpcClient,
) -> WalletActivity | None:
    tx_from = str(tx.get("from", ""))
    if tx_from.lower() != wallet.normalized_address:
        return None
    tx_hash = str(tx.get("hash"))
    receipt = await rpc.get_receipt(tx_hash) or {}
    transfers = parse_transfers(receipt)
    dex = None
    router_name = None
    confidence = 0.0
    reason = ""
    action_token = None
    quote_token = None
    pair_swaps = []
    route_inputs = []
    route_outputs = []

    if chain.lower() == "bsc":
        analysis = analyze_bsc_transaction(wallet, tx, receipt)
        # Metadata and Pair/Router lookups are more expensive than the first-pass log decode.
        # Skip them for ordinary transfers and contract calls to reduce RPC usage.
        if analysis.event_type in {"BUY", "SELL", "SWAP"} or analysis.pair_swaps:
            analysis = await enrich_bsc_analysis(analysis, tx, rpc)
        transfers = analysis.transfers
        event_type = analysis.event_type
        dex = analysis.protocol
        router_name = analysis.router_name
        confidence = analysis.confidence
        reason = analysis.reason
        action_token = analysis.action_token
        quote_token = analysis.quote_token
        pair_swaps = [PoolActivity(
            address=swap.pair, version=swap.version, sender=swap.sender, recipient=swap.recipient,
            amount0_in=swap.amount0_in, amount1_in=swap.amount1_in,
            amount0_out=swap.amount0_out, amount1_out=swap.amount1_out,
            token0=swap.token0, token1=swap.token1,
            token0_symbol=swap.token0_symbol, token1_symbol=swap.token1_symbol,
            factory=swap.factory, protocol=swap.protocol,
        ) for swap in analysis.pair_swaps]
        route_inputs = analysis.route_inputs
        route_outputs = analysis.route_outputs
    else:
        event_type = classify(wallet, tx, transfers)

    return WalletActivity(
        chain=chain,
        wallet_label=wallet.label,
        wallet=wallet.address,
        tx_hash=tx_hash,
        block_number=int(str(tx.get("blockNumber", "0x0")), 16),
        timestamp=datetime.fromtimestamp(block_timestamp, tz=timezone.utc),
        tx_from=tx_from,
        tx_to=tx.get("to"),
        native_value_wei=int(str(tx.get("value", "0x0")), 16),
        wallet_source=wallet.source,
        wallet_weight=wallet.weight,
        transfers=transfers,
        pair_swaps=pair_swaps,
        route_inputs=route_inputs,
        route_outputs=route_outputs,
        event_type=event_type,
        dex=dex,
        router_name=router_name,
        action_token=action_token,
        quote_token=quote_token,
        confidence=confidence,
        analysis_reason=reason,
    )
