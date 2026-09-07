from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class DexContract:
    address: str
    name: str
    kind: str


@dataclass(frozen=True)
class DexRegistry:
    routers: dict[str, DexContract]
    factories: dict[str, DexContract]
    quote_tokens: dict[str, str]

    def router(self, address: str | None) -> DexContract | None:
        return self.routers.get((address or "").lower())

    def factory(self, address: str | None) -> DexContract | None:
        return self.factories.get((address or "").lower())


def _contracts(items: list[dict[str, Any]]) -> dict[str, DexContract]:
    result: dict[str, DexContract] = {}
    for item in items:
        address = str(item.get("address", "")).lower()
        if address:
            result[address] = DexContract(
                address=address,
                name=str(item.get("name", address)),
                kind=str(item.get("kind", "unknown")),
            )
    return result


@lru_cache(maxsize=4)
def load_dex_registry(path: str = "config/dexes.yaml") -> DexRegistry:
    file_path = Path(path)
    data = yaml.safe_load(file_path.read_text(encoding="utf-8")) if file_path.exists() else {}
    data = data or {}
    quote_tokens = {
        str(item.get("address", "")).lower(): str(item.get("symbol", "QUOTE"))
        for item in data.get("quote_tokens", [])
        if item.get("address")
    }
    return DexRegistry(
        routers=_contracts(data.get("routers", [])),
        factories=_contracts(data.get("factories", [])),
        quote_tokens=quote_tokens,
    )
