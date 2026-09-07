from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.chains.rpc import TRANSFER_TOPIC, JsonRpcClient
from app.config import Wallet
from app.models import TokenTransfer, WalletActivity

logger = logging.getLogger(__name__)


def _address(topic: str) -> str:
    return "0x" + topic[-40:]


def parse_transfers(receipt: dict[str, Any]) -> list[TokenTransfer]:
    transfers: list[TokenTransfer] = []
    for log in receipt.get("logs", []):
        topics = log.get("topics", [])
        if len(topics) < 3 or topics[0].lower() != TRANSFER_TOPIC:
            continue
        try:
            transfers.append(TokenTransfer(
                token=log["address"],
                from_address=_address(topics[1]),
                to_address=_address(topics[2]),
                raw_amount=int(log.get("data", "0x0"), 16),
                log_index=int(log.get("logIndex", "0x0"), 16),
            ))
        except (KeyError, ValueError):
            logger.warning("Skipping malformed Transfer log: %s", log)
    return transfers


def classify(wallet: Wallet, tx: dict[str, Any], transfers: list[TokenTransfer]) -> str:
    wallet_address = wallet.normalized_address
    outgoing = any(t.from_address.lower() == wallet_address for t in transfers)
    incoming = any(t.to_address.lower() == wallet_address for t in transfers)
    # This is deliberately conservative: a real BUY/SELL parser needs DEX-specific
    # router/pair decoding and price context. V0.1 only labels unambiguous flows.
    if outgoing and incoming:
        return "UNKNOWN"
    if incoming:
        return "BUY"
    if outgoing:
        return "SELL"
    return "TRANSFER"


async def activity_from_transaction(
    chain: str,
    wallet: Wallet,
    tx: dict[str, Any],
    block_timestamp: int,
    rpc: JsonRpcClient,
) -> WalletActivity | None:
    tx_from = str(tx.get("from", ""))
    tx_to = tx.get("to")
    if tx_from.lower() != wallet.normalized_address:
        return None
    tx_hash = str(tx.get("hash"))
    receipt = await rpc.get_receipt(tx_hash)
    transfers = parse_transfers(receipt or {})
    return WalletActivity(
        chain=chain,
        wallet_label=wallet.label,
        wallet=wallet.address,
        tx_hash=tx_hash,
        block_number=int(tx.get("blockNumber", "0x0"), 16),
        timestamp=datetime.fromtimestamp(block_timestamp, tz=timezone.utc),
        tx_from=tx_from,
        tx_to=tx_to,
        native_value_wei=int(tx.get("value", "0x0"), 16),
        transfers=transfers,
        event_type=classify(wallet, tx, transfers),
    )

