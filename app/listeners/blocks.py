from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable

from app.chains.rpc import JsonRpcClient, RpcCircuitOpenError, RpcRateLimitError
from app.config import Wallet
from app.models import WalletActivity
from app.parsers.evm import activity_from_transaction

logger = logging.getLogger(__name__)

CheckpointLoader = Callable[[str], Awaitable[int | None]]
CheckpointSaver = Callable[[str, int], Awaitable[None]]


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
    retry_seconds: int = 30,
    max_catchup_blocks: int = 20,
    max_backoff_seconds: int = 60,
    load_checkpoint: CheckpointLoader | None = None,
    save_checkpoint: CheckpointSaver | None = None,
) -> AsyncIterator[WalletActivity]:
    """Watch a chain, using WebSocket heads and HTTP only when WS is unavailable."""
    chain_wallets = [w for w in wallets if chain in w.chains]
    if not chain_wallets:
        logger.warning("No wallets configured for %s", chain)
        return

    retry_seconds = max(1, retry_seconds)
    max_catchup_blocks = max(1, max_catchup_blocks)
    max_backoff_seconds = max(retry_seconds, max_backoff_seconds)
    backoff = retry_seconds
    while True:
        try:
            latest = await rpc.latest_block_number()
            checkpoint = await load_checkpoint(chain) if load_checkpoint else None
            if checkpoint is None or checkpoint > latest or latest - checkpoint > max_catchup_blocks:
                last_processed = latest
                if checkpoint is not None and latest - checkpoint > max_catchup_blocks:
                    logger.warning(
                        "%s checkpoint is %s blocks behind; skipping to block %s",
                        chain, latest - checkpoint, latest,
                    )
            else:
                last_processed = checkpoint
            logger.info("%s watcher starting after block %s", chain, last_processed)
            break
        except asyncio.CancelledError:
            raise
        except RpcCircuitOpenError as exc:
            await asyncio.sleep(max(retry_seconds, exc.retry_after))
        except RpcRateLimitError as exc:
            await asyncio.sleep(max(retry_seconds, exc.retry_after))
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s initial block check failed: %s; retrying in %ss", chain, exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff_seconds)

    state = {"target": last_processed}
    target_changed = asyncio.Event()
    ws_connected = asyncio.Event()

    async def publish_target(block_number: int) -> None:
        if block_number > state["target"]:
            state["target"] = block_number
            target_changed.set()

    async def websocket_source() -> None:
        backoff = retry_seconds
        while True:
            try:
                logger.info("%s watcher connecting via WebSocket", chain)
                async for head in rpc.subscribe_new_heads():
                    ws_connected.set()
                    block_number = int(str(head["number"]), 16)
                    await publish_target(block_number)
                    backoff = retry_seconds
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                ws_connected.clear()
                logger.warning("%s WebSocket unavailable: %s; retrying in %ss", chain, exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff_seconds)

    async def polling_source() -> None:
        warned = False
        while True:
            try:
                # A healthy WebSocket already tells us every new block. Avoid a
                # second eth_blockNumber request every few seconds in that case.
                if ws_connected.is_set():
                    warned = False
                    await asyncio.sleep(retry_seconds)
                    continue
                latest = await rpc.latest_block_number()
                await publish_target(latest)
                warned = False
            except asyncio.CancelledError:
                raise
            except (RpcCircuitOpenError, RpcRateLimitError) as exc:
                if not warned:
                    logger.warning("%s polling paused by RPC rate limit for %.0fs", chain, exc.retry_after)
                    warned = True
                await asyncio.sleep(max(retry_seconds, exc.retry_after))
                continue
            except Exception as exc:  # noqa: BLE001
                if not warned:
                    logger.warning("%s HTTP polling fallback failed: %s", chain, exc)
                    warned = True
            await asyncio.sleep(retry_seconds)

    websocket_task = asyncio.create_task(websocket_source(), name=f"{chain}-websocket")
    polling_task = asyncio.create_task(polling_source(), name=f"{chain}-polling")
    try:
        while True:
            await target_changed.wait()
            target = state["target"]
            if target > last_processed:
                if target - last_processed > max_catchup_blocks:
                    skipped = target - max_catchup_blocks
                    logger.warning(
                        "%s is %s blocks behind; limiting catch-up to the newest %s blocks",
                        chain, target - last_processed, max_catchup_blocks,
                    )
                    last_processed = skipped
                logger.debug("%s processing blocks %s-%s", chain, last_processed + 1, target)
                for block_number in range(last_processed + 1, target + 1):
                    while True:
                        try:
                            activities = await activities_for_block(chain, block_number, chain_wallets, rpc)
                            break
                        except asyncio.CancelledError:
                            raise
                        except (RpcCircuitOpenError, RpcRateLimitError) as exc:
                            logger.warning("%s block processing paused by RPC rate limit for %.0fs", chain, exc.retry_after)
                            await asyncio.sleep(max(retry_seconds, exc.retry_after))
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("%s failed to process block %s: %s; retrying in %ss", chain, block_number, exc, retry_seconds)
                            await asyncio.sleep(retry_seconds)
                    for activity in activities:
                        yield activity
                    last_processed = block_number
                    if save_checkpoint:
                        await save_checkpoint(chain, last_processed)
            if state["target"] <= target:
                target_changed.clear()
    finally:
        for task in (websocket_task, polling_task):
            task.cancel()
        await asyncio.gather(websocket_task, polling_task, return_exceptions=True)
