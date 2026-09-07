from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from app.chains.rpc import JsonRpcClient
from app.config import Wallet
from app.parsers.evm import activity_from_transaction
from app.models import WalletActivity

logger = logging.getLogger(__name__)


async def activities_for_block(
    chain: str,
    block_number: int,
    wallets: list[Wallet],
    rpc: JsonRpcClient,
) -> list[WalletActivity]:
    block = await rpc.get_block(block_number)
    if not block:
        return []
    by_address = {wallet.normalized_address: wallet for wallet in wallets}
    matching = [
        tx for tx in block.get("transactions", [])
        if str(tx.get("from", "")).lower() in by_address
    ]
    result: list[WalletActivity] = []
    for tx in matching:
        activity = await activity_from_transaction(
            chain, by_address[str(tx["from"]).lower()], tx,
            int(block.get("timestamp", "0x0"), 16), rpc,
        )
        if activity:
            result.append(activity)
    return result


async def watch_chain(
    chain: str,
    wallets: list[Wallet],
    rpc: JsonRpcClient,
    retry_seconds: int = 5,
) -> AsyncIterator[WalletActivity]:
    chain_wallets = [w for w in wallets if chain in w.chains]
    if not chain_wallets:
        logger.warning("No wallets configured for %s", chain)
        return
    while True:
        try:
            async for head in rpc.subscribe_new_heads():
                block_number = int(head["number"], 16)
                for activity in await activities_for_block(chain, block_number, chain_wallets, rpc):
                    yield activity
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s watcher disconnected: %s; retrying", chain, exc)
            await asyncio.sleep(retry_seconds)
