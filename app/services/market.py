from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from app.models import MarketSnapshot, WalletActivity

logger = logging.getLogger(__name__)


class DexScreenerClient:
    """Read-only market data client using DexScreener's public token-pairs API."""

    def __init__(self, timeout: float = 12.0, cache_ttl_seconds: int = 30) -> None:
        self.timeout = timeout
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cache: dict[tuple[str, str], tuple[float, MarketSnapshot | None]] = {}
        self._inflight: dict[tuple[str, str], asyncio.Task[MarketSnapshot | None]] = {}

    async def snapshot(self, chain: str, token_address: str) -> MarketSnapshot | None:
        key = (chain.lower(), token_address.lower())
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < self.cache_ttl_seconds:
            return cached[1]
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._load(*key))
            self._inflight[key] = task
        try:
            result = await task
            # Cache misses too, avoiding repeated public API calls for the same token.
            self._cache[key] = (time.monotonic(), result)
            return result
        finally:
            if task.done():
                self._inflight.pop(key, None)

    async def _load(self, chain: str, token_address: str) -> MarketSnapshot | None:
        if chain != "bsc":
            return None
        url = f"https://api.dexscreener.com/token-pairs/v1/bsc/{token_address}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("DexScreener request failed for %s: %s", token_address, exc)
            return None
        pairs = payload if isinstance(payload, list) else payload.get("pairs", [])
        if not isinstance(pairs, list):
            return None
        if not pairs:
            return None
        pair = max(
            (item for item in pairs if isinstance(item, dict)),
            key=lambda item: float((item.get("liquidity") or {}).get("usd") or 0),
            default=None,
        )
        if pair is None:
            return None
        created = pair.get("pairCreatedAt")
        created_at = None
        if created:
            try:
                created_at = datetime.fromtimestamp(float(created) / 1000, tz=timezone.utc)
            except (TypeError, ValueError, OverflowError):
                pass
        snapshot = MarketSnapshot(
            price_usd=_float(pair.get("priceUsd")),
            market_cap_usd=_float(pair.get("marketCap")) or _float(pair.get("fdv")),
            liquidity_usd=_float((pair.get("liquidity") or {}).get("usd")),
            pair_created_at=created_at,
            pair_address=pair.get("pairAddress"),
            dex_id=pair.get("dexId"),
            source="DexScreener",
        )
        self._cache[(chain, token_address)] = (time.monotonic(), snapshot)
        return snapshot

    async def enrich(self, activity: WalletActivity) -> WalletActivity:
        if not activity.action_token:
            return activity
        activity.market = await self.snapshot(activity.chain, activity.action_token)
        if activity.market:
            activity.market.trade_value_usd = estimate_trade_value(activity)
        return activity


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def estimate_trade_value(activity: WalletActivity) -> float | None:
    if not activity.market or activity.market.price_usd is None or not activity.action_token:
        return None
    amount = 0.0
    for transfer in activity.transfers:
        if transfer.token != activity.action_token:
            continue
        if activity.event_type == "BUY" and transfer.to_address.lower() == activity.wallet.lower():
            amount += float(transfer.amount or 0)
        elif activity.event_type == "SELL" and transfer.from_address.lower() == activity.wallet.lower():
            amount += float(transfer.amount or 0)
    return amount * activity.market.price_usd if amount else None
