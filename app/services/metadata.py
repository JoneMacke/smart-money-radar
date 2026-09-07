from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from weakref import WeakKeyDictionary

import httpx

from app.chains.rpc import JsonRpcClient, RpcError

SYMBOL_SELECTOR = "0x95d89b41"
NAME_SELECTOR = "0x06fdde03"
DECIMALS_SELECTOR = "0x313ce567"
TOKEN0_SELECTOR = "0x0dfe1681"
TOKEN1_SELECTOR = "0xd21220a7"
FACTORY_SELECTOR = "0xc45a0155"


def decode_address(data: str | None) -> str | None:
    raw = str(data or "").removeprefix("0x")
    if len(raw) < 40:
        return None
    address = "0x" + raw[-40:]
    return None if address == "0x" + "0" * 40 else address.lower()


def decode_uint(data: str | None) -> int | None:
    raw = str(data or "").removeprefix("0x")
    if not raw:
        return None
    try:
        return int(raw, 16)
    except ValueError:
        return None


def decode_abi_string(data: str | None) -> str | None:
    raw = str(data or "").removeprefix("0x")
    if not raw:
        return None
    try:
        payload = bytes.fromhex(raw)
    except ValueError:
        return None

    candidates: list[bytes] = []
    if len(payload) >= 64:
        offset = int.from_bytes(payload[:32], "big")
        if offset + 32 <= len(payload):
            length = int.from_bytes(payload[offset : offset + 32], "big")
            end = offset + 32 + length
            if end <= len(payload):
                candidates.append(payload[offset + 32 : end])
    candidates.append(payload[:32].rstrip(b"\x00"))

    for candidate in candidates:
        if not candidate:
            continue
        try:
            value = candidate.decode("utf-8").strip("\x00 ")
        except UnicodeDecodeError:
            continue
        if value:
            return value
    return None


@dataclass(frozen=True)
class TokenMetadata:
    address: str
    symbol: str | None = None
    name: str | None = None
    decimals: int | None = None

    def amount(self, raw_amount: int) -> Decimal | None:
        if self.decimals is None:
            return None
        return Decimal(raw_amount) / (Decimal(10) ** self.decimals)


@dataclass(frozen=True)
class PairMetadata:
    address: str
    token0: str
    token1: str
    factory: str | None = None
    token0_metadata: TokenMetadata | None = None
    token1_metadata: TokenMetadata | None = None


class OnchainMetadataService:
    """RPC-backed metadata resolver with in-memory caching and request coalescing."""

    def __init__(self, rpc: JsonRpcClient, max_concurrency: int = 8) -> None:
        self.rpc = rpc
        self._tokens: dict[str, TokenMetadata] = {}
        self._pairs: dict[str, PairMetadata | None] = {}
        self._token_tasks: dict[str, asyncio.Task[TokenMetadata]] = {}
        self._pair_tasks: dict[str, asyncio.Task[PairMetadata | None]] = {}
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def _eth_call(self, address: str, selector: str) -> str | None:
        try:
            async with self._semaphore:
                return await self.rpc.call(
                    "eth_call", [{"to": address, "data": selector}, "latest"]
                )
        except (RpcError, httpx.HTTPError, TypeError, ValueError):
            return None

    async def token(self, address: str) -> TokenMetadata:
        normalized = address.lower()
        cached = self._tokens.get(normalized)
        if cached is not None:
            return cached
        task = self._token_tasks.get(normalized)
        if task is None:
            task = asyncio.create_task(self._load_token(normalized))
            self._token_tasks[normalized] = task
        try:
            return await task
        finally:
            if task.done():
                self._token_tasks.pop(normalized, None)

    async def _load_token(self, normalized: str) -> TokenMetadata:
        symbol_raw, name_raw, decimals_raw = await asyncio.gather(
            self._eth_call(normalized, SYMBOL_SELECTOR),
            self._eth_call(normalized, NAME_SELECTOR),
            self._eth_call(normalized, DECIMALS_SELECTOR),
        )
        metadata = TokenMetadata(
            address=normalized,
            symbol=decode_abi_string(symbol_raw),
            name=decode_abi_string(name_raw),
            decimals=decode_uint(decimals_raw),
        )
        self._tokens[normalized] = metadata
        return metadata

    async def pair(self, address: str) -> PairMetadata | None:
        normalized = address.lower()
        if normalized in self._pairs:
            return self._pairs[normalized]
        task = self._pair_tasks.get(normalized)
        if task is None:
            task = asyncio.create_task(self._load_pair(normalized))
            self._pair_tasks[normalized] = task
        try:
            return await task
        finally:
            if task.done():
                self._pair_tasks.pop(normalized, None)

    async def _load_pair(self, normalized: str) -> PairMetadata | None:
        token0_raw, token1_raw, factory_raw = await asyncio.gather(
            self._eth_call(normalized, TOKEN0_SELECTOR),
            self._eth_call(normalized, TOKEN1_SELECTOR),
            self._eth_call(normalized, FACTORY_SELECTOR),
        )
        token0 = decode_address(token0_raw)
        token1 = decode_address(token1_raw)
        if not token0 or not token1:
            self._pairs[normalized] = None
            return None
        meta0, meta1 = await asyncio.gather(self.token(token0), self.token(token1))
        pair = PairMetadata(
            address=normalized,
            token0=token0,
            token1=token1,
            factory=decode_address(factory_raw),
            token0_metadata=meta0,
            token1_metadata=meta1,
        )
        self._pairs[normalized] = pair
        return pair


_SERVICES: WeakKeyDictionary[JsonRpcClient, OnchainMetadataService] = WeakKeyDictionary()


def metadata_service_for(rpc: JsonRpcClient) -> OnchainMetadataService:
    service = _SERVICES.get(rpc)
    if service is None:
        service = OnchainMetadataService(rpc)
        _SERVICES[rpc] = service
    return service
