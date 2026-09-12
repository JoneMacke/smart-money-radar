from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from app.chains.rpc import (
    TRANSFER_TOPIC,
    JsonRpcClient,
    RpcCircuitOpenError,
    RpcMethodUnsupportedError,
    RpcRateLimitError,
)
from app.config import Wallet
from app.models import WalletActivity
from app.parsers.evm import activity_from_transaction

logger = logging.getLogger(__name__)

CheckpointLoader = Callable[[str], Awaitable[int | None]]
CheckpointSaver = Callable[[str, int], Awaitable[None]]


def _wallet_topic(address: str) -> str:
    return "0x" + "0" * 24 + address.lower().removeprefix("0x")


def _log_tx_hash(log: dict[str, Any]) -> str | None:
    value = log.get("transactionHash") or log.get("txHash")
    return str(value) if value else None


async def _activities_for_block_full(
    chain: str,
    block_number: int,
    wallets: list[Wallet],
    rpc: JsonRpcClient,
) -> list[WalletActivity]:
    """Compatibility fallback for providers that do not support eth_getLogs."""
    block = await rpc.get_block(block_number, full_transactions=True)
    if not block:
        return []
    by_address = {wallet.normalized_address: wallet for wallet in wallets}
    result: list[WalletActivity] = []
    timestamp = int(block.get("timestamp", "0x0"), 16)
    for tx in block.get("transactions", []):
        wallet = by_address.get(str(tx.get("from", "")).lower())
        if not wallet:
            continue
        activity = await activity_from_transaction(chain, wallet, tx, timestamp, rpc)
        if activity:
            result.append(activity)
    return result


async def activities_for_range(
    chain: str,
    from_block: int,
    to_block: int,
    wallets: list[Wallet],
    rpc: JsonRpcClient,
    candidate_concurrency: int = 8,
    log_scan_enabled: bool = True,
) -> list[WalletActivity]:
    """Scan a short block range using indexed Transfer logs before tx details."""
    if not log_scan_enabled:
        result: list[WalletActivity] = []
        for block_number in range(from_block, to_block + 1):
            result.extend(await _activities_for_block_full(chain, block_number, wallets, rpc))
        return result
    by_address = {wallet.normalized_address: wallet for wallet in wallets}
    wallet_topics = [_wallet_topic(address) for address in by_address]
    if not wallet_topics:
        return []

    outgoing_topics = [TRANSFER_TOPIC, wallet_topics, None]
    incoming_topics = [TRANSFER_TOPIC, None, wallet_topics]
    try:
        outgoing, incoming = await asyncio.gather(
            rpc.get_logs(from_block, to_block, outgoing_topics),
            rpc.get_logs(from_block, to_block, incoming_topics),
        )
    except (RpcCircuitOpenError, RpcRateLimitError):
        raise
    except RpcMethodUnsupportedError as exc:
        logger.info(
            "%s provider has no eth_getLogs; switching this scan to full-block fallback: %s",
            chain, exc,
        )
        result: list[WalletActivity] = []
        for block_number in range(from_block, to_block + 1):
            result.extend(await _activities_for_block_full(chain, block_number, wallets, rpc))
        return result
    except Exception as exc:  # noqa: BLE001
        # A timeout or provider hiccup must not trigger a full-block scan,
        # which would be much more expensive. Let watch_chain retry the same
        # bounded range instead.
        logger.warning(
            "%s eth_getLogs failed for blocks %s-%s; retrying bounded scan: %s",
            chain, from_block, to_block, exc,
        )
        raise

    tx_hashes = {_log_tx_hash(log) for log in [*outgoing, *incoming]}
    tx_hashes.discard(None)
    if not tx_hashes:
        return []

    timestamp_tasks: dict[int, asyncio.Task[int]] = {}

    async def load_timestamp(block_number: int) -> int:
        block = await rpc.get_block(block_number, full_transactions=False)
        return int((block or {}).get("timestamp", "0x0"), 16)

    async def get_timestamp(block_number: int) -> int:
        # Share the light block-header request for candidates in one block.
        task = timestamp_tasks.get(block_number)
        if task is None:
            task = asyncio.create_task(load_timestamp(block_number))
            timestamp_tasks[block_number] = task
        return await task

    semaphore = asyncio.Semaphore(max(1, candidate_concurrency))

    async def load_candidate(tx_hash: str) -> WalletActivity | None:
        async with semaphore:
            tx = await rpc.get_transaction(tx_hash)
            if not tx:
                return None
            wallet = by_address.get(str(tx.get("from", "")).lower())
            if not wallet:
                # The monitor is sender-oriented. Incoming transfers from
                # routers/other wallets are not wallet actions.
                return None
            block_number = int(str(tx.get("blockNumber", "0x0")), 16)
            return await activity_from_transaction(
                chain, wallet, tx, await get_timestamp(block_number), rpc,
            )

    activities = await asyncio.gather(*(load_candidate(tx_hash) for tx_hash in sorted(tx_hashes)))
    return [activity for activity in activities if activity is not None]


async def activities_for_block(
    chain: str,
    block_number: int,
    wallets: list[Wallet],
    rpc: JsonRpcClient,
    candidate_concurrency: int = 8,
    log_scan_enabled: bool = True,
) -> list[WalletActivity]:
    """Backward-compatible single-block wrapper."""
    return await activities_for_range(
        chain, block_number, block_number, wallets, rpc, candidate_concurrency, log_scan_enabled,
    )


async def watch_chain(
    chain: str,
    wallets: list[Wallet],
    rpc: JsonRpcClient,
    retry_seconds: int = 30,
    max_catchup_blocks: int = 20,
    load_checkpoint: CheckpointLoader | None = None,
    save_checkpoint: CheckpointSaver | None = None,
    *,
    max_backoff_seconds: int = 60,
    candidate_concurrency: int = 8,
    log_scan_enabled: bool = True,
    block_batch_delay_seconds: float = 0.5,
) -> AsyncIterator[WalletActivity]:
    """Watch a chain with WS heads and indexed-log block scanning."""
    chain_wallets = [w for w in wallets if chain in w.chains]
    if not chain_wallets:
        logger.warning("No wallets configured for %s", chain)
        return

    retry_seconds = max(1, retry_seconds)
    max_catchup_blocks = max(1, max_catchup_blocks)
    max_backoff_seconds = max(retry_seconds, max_backoff_seconds)
    block_batch_delay_seconds = max(0.0, block_batch_delay_seconds)
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
        except (RpcCircuitOpenError, RpcRateLimitError) as exc:
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
                    await publish_target(int(str(head["number"]), 16))
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
                if ws_connected.is_set():
                    warned = False
                    await asyncio.sleep(retry_seconds)
                    continue
                await publish_target(await rpc.latest_block_number())
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
            # A short debounce lets several WebSocket heads share one log
            # query during bursts, without materially delaying alerts.
            if block_batch_delay_seconds:
                await asyncio.sleep(block_batch_delay_seconds)
            while True:
                target = state["target"]
                while last_processed < target:
                    if target - last_processed > max_catchup_blocks:
                        last_processed = target - max_catchup_blocks
                        logger.warning(
                            "%s is behind; limiting catch-up to newest %s blocks",
                            chain, max_catchup_blocks,
                        )
                    batch_end = min(target, last_processed + max_catchup_blocks)
                    while True:
                        try:
                            activities = await activities_for_range(
                                chain, last_processed + 1, batch_end,
                                chain_wallets, rpc, candidate_concurrency, log_scan_enabled,
                            )
                            break
                        except asyncio.CancelledError:
                            raise
                        except (RpcCircuitOpenError, RpcRateLimitError) as exc:
                            logger.warning("%s block scan paused by RPC rate limit for %.0fs", chain, exc.retry_after)
                            await asyncio.sleep(max(retry_seconds, exc.retry_after))
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("%s failed to process blocks %s-%s: %s; retrying in %ss", chain, last_processed + 1, batch_end, exc, retry_seconds)
                            await asyncio.sleep(retry_seconds)
                    for activity in activities:
                        yield activity
                    last_processed = batch_end
                    if save_checkpoint:
                        await save_checkpoint(chain, last_processed)
                    target = state["target"]
                # Do not lose a head published while the scan was finishing.
                target_changed.clear()
                if state["target"] <= last_processed:
                    break
                target_changed.set()
    finally:
        for task in (websocket_task, polling_task):
            task.cancel()
        await asyncio.gather(websocket_task, polling_task, return_exceptions=True)
