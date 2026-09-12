from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter
from typing import Any

import httpx
import websockets

logger = logging.getLogger(__name__)

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


class RpcError(RuntimeError):
    """A sanitized RPC error that never contains the configured endpoint URL."""


class RpcMethodUnsupportedError(RpcError):
    """The provider does not implement the requested JSON-RPC method."""


class RpcRateLimitError(RpcError):
    def __init__(self, message: str, retry_after: float) -> None:
        super().__init__(message)
        self.retry_after = max(0.0, retry_after)


class RpcCircuitOpenError(RpcRateLimitError):
    pass


class JsonRpcClient:
    """Small JSON-RPC client with connection reuse, metrics and 429 circuit breaking."""

    def __init__(
        self,
        http_url: str,
        ws_url: str,
        timeout: float = 20.0,
        rate_limit_cooldown_seconds: int = 300,
        max_backoff_seconds: int = 60,
    ) -> None:
        self.http_url = http_url
        self.ws_url = ws_url
        self.timeout = timeout
        self.rate_limit_cooldown_seconds = max(1, rate_limit_cooldown_seconds)
        self.max_backoff_seconds = max(1, max_backoff_seconds)
        self._request_id = 0
        self._client: httpx.AsyncClient | None = None
        self._circuit_open_until = 0.0
        self._rate_limit_logged = False
        # None means unknown, True means supported, False means permanently
        # unavailable for this provider. This avoids retrying unsupported methods
        # on every scan interval.
        self._logs_supported: bool | None = None
        self._stats: Counter[str] = Counter()
        self._stats_lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def _count(self, key: str) -> None:
        async with self._stats_lock:
            self._stats[key] += 1

    def _circuit_remaining(self) -> float:
        return max(0.0, self._circuit_open_until - time.monotonic())

    async def call(self, method: str, params: list[Any] | None = None) -> Any:
        if not self.http_url:
            raise RpcError("RPC URL is not configured")
        remaining = self._circuit_remaining()
        if remaining:
            await self._count("circuit_open")
            raise RpcCircuitOpenError(
                "RPC circuit breaker is cooling down after rate limiting",
                remaining,
            )

        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params or [],
        }
        await self._count("requests")
        await self._count(f"method:{method}")
        client = await self._get_client()
        try:
            response = await client.post(self.http_url, json=payload)
            if response.status_code == 429:
                await self._open_circuit("HTTP 429 Too Many Requests")
                raise RpcRateLimitError(
                    "RPC provider rate limited the request",
                    self.rate_limit_cooldown_seconds,
                )
            if response.status_code >= 400:
                await self._count("http_errors")
                raise RpcError(f"RPC HTTP request failed with status {response.status_code}")
            body = response.json()
        except RpcError:
            raise
        except httpx.TimeoutException as exc:
            await self._count("timeouts")
            raise RpcError("RPC request timed out") from exc
        except httpx.HTTPError as exc:
            await self._count("http_errors")
            raise RpcError("RPC HTTP request failed") from exc
        except ValueError as exc:
            await self._count("invalid_json")
            raise RpcError("RPC returned invalid JSON") from exc

        if "error" in body:
            error_text = str(body.get("error", "RPC provider error"))
            lowered = error_text.lower()
            if "-32601" in lowered or "method not found" in lowered or "method does not exist" in lowered or "not implemented" in lowered or "unsupported method" in lowered or "method is not supported" in lowered:
                await self._count("unsupported_methods")
                raise RpcMethodUnsupportedError("RPC provider does not support this method")
            if "429" in lowered or "too many requests" in lowered or "capacity limit" in lowered or "rate limit" in lowered:
                await self._open_circuit("RPC provider capacity/rate limit reached")
                raise RpcRateLimitError(
                    "RPC provider rate limited the request",
                    self.rate_limit_cooldown_seconds,
                )
            await self._count("rpc_errors")
            raise RpcError("RPC provider returned an error")

        self._circuit_open_until = 0.0
        self._rate_limit_logged = False
        return body.get("result")

    async def _open_circuit(self, reason: str) -> None:
        self._circuit_open_until = max(
            self._circuit_open_until,
            time.monotonic() + self.rate_limit_cooldown_seconds,
        )
        await self._count("rate_limited")
        if not self._rate_limit_logged:
            logger.error(
                "RPC rate limit detected; pausing requests for %ss (%s)",
                self.rate_limit_cooldown_seconds,
                reason,
            )
            self._rate_limit_logged = True

    async def latest_block_number(self) -> int:
        block = await self.call("eth_blockNumber")
        return int(block, 16)

    async def health(self) -> dict[str, Any]:
        chain_id, block = await asyncio.gather(
            self.call("eth_chainId"), self.call("eth_blockNumber")
        )
        return {"chain_id": int(chain_id, 16), "latest_block": int(block, 16)}

    async def get_block(self, block_number: int, full_transactions: bool = True) -> dict[str, Any]:
        block = hex(block_number)
        return await self.call("eth_getBlockByNumber", [block, full_transactions])

    async def get_logs(self, from_block: int, to_block: int, topics: list[Any]) -> list[dict[str, Any]]:
        if self._logs_supported is False:
            raise RpcMethodUnsupportedError("RPC provider does not support eth_getLogs")
        try:
            result = await self.call(
                "eth_getLogs",
                [{"fromBlock": hex(from_block), "toBlock": hex(to_block), "topics": topics}],
            )
        except RpcMethodUnsupportedError:
            self._logs_supported = False
            raise
        self._logs_supported = True
        return result if isinstance(result, list) else []

    async def get_transaction(self, tx_hash: str) -> dict[str, Any] | None:
        return await self.call("eth_getTransactionByHash", [tx_hash])

    async def get_receipt(self, tx_hash: str) -> dict[str, Any]:
        return await self.call("eth_getTransactionReceipt", [tx_hash])

    async def stats(self) -> dict[str, int]:
        async with self._stats_lock:
            return dict(self._stats)

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def subscribe_new_heads(self):
        if not self.ws_url:
            raise RpcError("WebSocket URL is not configured")
        async with websockets.connect(self.ws_url, ping_interval=20, ping_timeout=None) as socket:
            await socket.send(json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "eth_subscribe", "params": ["newHeads"]
            }))
            confirmation = json.loads(await socket.recv())
            if "error" in confirmation:
                raise RpcError("WebSocket subscription was rejected")
            while True:
                message = json.loads(await socket.recv())
                if message.get("method") == "eth_subscription":
                    yield message["params"]["result"]
