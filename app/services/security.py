from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.models import SecuritySnapshot, WalletActivity

logger = logging.getLogger(__name__)


class GoPlusSecurityClient:
    """Best-effort read-only Token security lookup for BSC."""

    def __init__(self, timeout: float = 12.0, cache_ttl_seconds: int = 300) -> None:
        self.timeout = timeout
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cache: dict[str, tuple[float, SecuritySnapshot]] = {}

    async def snapshot(self, chain: str, token_address: str) -> SecuritySnapshot | None:
        if chain.lower() != "bsc":
            return None
        key = token_address.lower()
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < self.cache_ttl_seconds:
            return cached[1]
        url = f"https://api.gopluslabs.io/api/v1/token_security/56?contract_addresses={key}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("GoPlus request failed for %s: %s", key, exc)
            return None
        result_map = payload.get("result") or {}
        result = result_map.get(key) or result_map.get(token_address)
        if not isinstance(result, dict):
            return None
        snapshot = SecuritySnapshot(
            is_open_source=_bool(result.get("is_open_source")),
            is_proxy=_bool(result.get("is_proxy")),
            is_honeypot=_bool(result.get("is_honeypot")),
            cannot_sell_all=_bool(result.get("cannot_sell_all")),
            hidden_owner=_bool(result.get("hidden_owner")),
            owner_change_balance=_bool(result.get("owner_change_balance")),
            buy_tax=_percent(result.get("buy_tax")),
            sell_tax=_percent(result.get("sell_tax")),
            top_holder_percent=_top_holder_percent(result.get("holders")),
            creator_address=result.get("creator_address") or result.get("owner_address"),
            source="GoPlus",
        )
        snapshot.risk_flags = _risk_flags(snapshot)
        self._cache[key] = (time.monotonic(), snapshot)
        return snapshot

    async def enrich(self, activity: WalletActivity) -> WalletActivity:
        if activity.action_token:
            activity.security = await self.snapshot(activity.chain, activity.action_token)
        return activity


def _bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    return str(value).lower() in {"1", "true", "yes"}


def _percent(value: Any) -> float | None:
    try:
        number = float(value)
        return number * 100 if number <= 1 else number
    except (TypeError, ValueError):
        return None


def _top_holder_percent(holders: Any) -> float | None:
    if not isinstance(holders, list):
        return None
    values = []
    for holder in holders:
        if isinstance(holder, dict):
            try:
                values.append(float(holder.get("percent", 0)))
            except (TypeError, ValueError):
                pass
    return max(values) * 100 if values and max(values) <= 1 else max(values, default=None)


def _risk_flags(snapshot: SecuritySnapshot) -> list[str]:
    flags: list[str] = []
    if snapshot.is_honeypot:
        flags.append("honeypot")
    if snapshot.cannot_sell_all:
        flags.append("cannot_sell_all")
    if snapshot.hidden_owner:
        flags.append("hidden_owner")
    if snapshot.owner_change_balance:
        flags.append("owner_change_balance")
    if snapshot.sell_tax is not None and snapshot.sell_tax > 10:
        flags.append("high_sell_tax")
    return flags
