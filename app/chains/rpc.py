from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
import websockets

logger = logging.getLogger(__name__)

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a9df523b3ef"


class RpcError(RuntimeError):
    pass


class JsonRpcClient:
    def __init__(self, http_url: str, ws_url: str, timeout: float = 20.0) -> None:
        self.http_url = http_url
        self.ws_url = ws_url
        self.timeout = timeout
        self._request_id = 0

    async def call(self, method: str, params: list[Any] | None = None) -> Any:
        if not self.http_url:
            raise RpcError("RPC URL is not configured")
        self._request_id += 1
        payload = {"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params or []}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(self.http_url, json=payload)
            response.raise_for_status()
            body = response.json()
        if "error" in body:
            raise RpcError(str(body["error"]))
        return body.get("result")

    async def health(self) -> dict[str, Any]:
        chain_id, block = await asyncio.gather(
            self.call("eth_chainId"), self.call("eth_blockNumber")
        )
        return {"chain_id": int(chain_id, 16), "latest_block": int(block, 16)}

    async def get_block(self, block_number: int) -> dict[str, Any]:
        block = hex(block_number)
        return await self.call("eth_getBlockByNumber", [block, True])

    async def get_receipt(self, tx_hash: str) -> dict[str, Any]:
        return await self.call("eth_getTransactionReceipt", [tx_hash])

    async def subscribe_new_heads(self):
        if not self.ws_url:
            raise RpcError("WebSocket URL is not configured")
        async with websockets.connect(self.ws_url, ping_interval=20, ping_timeout=20) as socket:
            await socket.send(json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "eth_subscribe", "params": ["newHeads"]
            }))
            confirmation = json.loads(await socket.recv())
            if "error" in confirmation:
                raise RpcError(str(confirmation["error"]))
            while True:
                message = json.loads(await socket.recv())
                if message.get("method") == "eth_subscription":
                    yield message["params"]["result"]
