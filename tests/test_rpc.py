from __future__ import annotations

import asyncio

import pytest

from app.chains.rpc import JsonRpcClient, RpcMethodUnsupportedError


def test_eth_get_logs_unsupported_is_cached():
    async def run():
        client = JsonRpcClient("http://rpc.invalid", "")
        calls = []

        async def unsupported(method, params=None):
            calls.append(method)
            raise RpcMethodUnsupportedError("not supported")

        client.call = unsupported
        with pytest.raises(RpcMethodUnsupportedError):
            await client.get_logs(1, 2, [])
        with pytest.raises(RpcMethodUnsupportedError):
            await client.get_logs(3, 4, [])
        assert calls == ["eth_getLogs"]

    asyncio.run(run())
