from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bsc_rpc_url: str = ""
    bsc_wss_url: str = ""
    robinhood_rpc_url: str = ""
    robinhood_wss_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    database_url: str = ""
    market_data_enabled: bool = True
    security_data_enabled: bool = True
    market_data_timeout_seconds: int = 12
    aggregation_window_minutes: int = 60
    min_alert_score: float = 60.0
    alert_event_types: str = "BUY,SELL,SWAP"
    # Polling is a fallback only. A healthy WebSocket does not poll.
    poll_interval_seconds: int = 30
    max_catchup_blocks: int = 20
    rpc_rate_limit_cooldown_seconds: int = 300
    rpc_max_backoff_seconds: int = 60
    log_scan_enabled: bool = True
    candidate_concurrency: int = 8
    block_batch_delay_seconds: float = 0.5
    log_level: str = "INFO"


@dataclass(frozen=True)
class Wallet:
    label: str
    address: str
    chains: tuple[str, ...]
    source: str = ""
    weight: float = 1.0

    @property
    def normalized_address(self) -> str:
        return self.address.lower()


def load_wallets(path: str | Path = "config/wallets.yaml") -> list[Wallet]:
    file_path = Path(path)
    if not file_path.exists():
        return []
    data: dict[str, Any] = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
    result: list[Wallet] = []
    for item in data.get("wallets", []):
        address = str(item.get("address", "")).strip()
        if address.isdigit():
            address = "0x" + format(int(address), "040x")
        if not address or address.lower() == "0x" + "0" * 40:
            continue
        result.append(
            Wallet(
                label=str(item.get("label", address[:10])),
                address=address,
                chains=tuple(str(x).lower() for x in item.get("chains", [])),
                source=str(item.get("source", "")),
                weight=float(item.get("weight", 1.0)),
            )
        )
    return result


def chain_env(settings: Settings, chain: str) -> tuple[str, str, int]:
    chain = chain.lower()
    if chain == "bsc":
        return settings.bsc_rpc_url, settings.bsc_wss_url, 56
    if chain == "robinhood":
        return settings.robinhood_rpc_url, settings.robinhood_wss_url, 4663
    raise ValueError(f"Unsupported chain: {chain}")
