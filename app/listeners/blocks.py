from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from app.chains.rpc import JsonRpcClient
from app.config import Wallet
from app.models import WalletActivity
from app.parsers.evm import activity_from_transaction

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
    """Watch a chain using WebSocket heads with HTTP polling as a backstop.

    The polling producer runs continuously, not only after a WebSocket failure.
    That gives us a way to catch up from the last processed block whenever a
    provider resets or silently drops the subscription.
    """
    chain_wallets = [w for w in wallets if chain in w.chains]
    if not chain_wallets:
        logger.warning("No wallets configured for %s", chain)
        return

    try:
        last_processed = await rpc.latest_block_number()
        logger.info("%s watcher starting at block %s", chain, last_processed)
    except Exception as exc:  # noqa: BLE001
        # The first HTTP check can fail while a provider is coming up. Start
        # from -1 and let the normal polling/reconnect loop recover.
        logger.warning("%s initial block check failed: %r", chain, exc)
        last_processed = -1

    state = {"target": last_processed}
    target_changed = asyncio.Event()

    async def publish_target(block_number: int) -> None:
        if block_number > state["target"]:
            state["target"] = block_number
            target_changed.set()

    async def websocket_source() -> None:
        backoff = max(1, retry_seconds)
        while True:
            try:
                logger.info("%s watcher connecting via WebSocket", chain)
                async for head in rpc.subscribe_new_heads():
                    block_number = int(str(head["number"]), 16)
                    await publish_target(block_number)
                    backoff = max(1, retry_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "%s WebSocket watcher disconnected: %r; retrying in %ss",
                    chain, exc, backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def polling_source() -> None:
        while True:
            try:
                latest = await rpc.latest_block_number()
                await publish_target(latest)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("%s HTTP polling fallback failed: %r", chain, exc)
            await asyncio.sleep(max(1, retry_seconds))

    websocket_task = asyncio.create_task(websocket_source(), name=f"{chain}-websocket")
    polling_task = asyncio.create_task(polling_source(), name=f"{chain}-polling")
    try:
        while True:
            await target_changed.wait()
            target = state["target"]
            if target > last_processed:
                logger.debug(
                    "%s processing blocks %s-%s", chain, last_processed + 1, target
                )
                for block_number in range(last_processed + 1, target + 1):
                    for activity in await activities_for_block(
                        chain, block_number, chain_wallets, rpc
                    ):
                        yield activity
                    last_processed = block_number
            # A producer may have published a newer target while the range was
            # being processed. Keep the event set so the next loop catches up.
            if state["target"] <= target:
                target_changed.clear()
    finally:
        for task in (websocket_task, polling_task):
            task.cancel()
        await asyncio.gather(websocket_task, polling_task, return_exceptions=True)
