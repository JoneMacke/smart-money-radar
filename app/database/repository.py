from __future__ import annotations

import json
import logging
from dataclasses import asdict, is_dataclass
from typing import Any

from app.models import WalletActivity

logger = logging.getLogger(__name__)


def _json(value: Any) -> str | None:
    if value is None:
        return None
    payload = asdict(value) if is_dataclass(value) else value
    return json.dumps(payload, default=str)

class PostgresRepository:
    """Optional async PostgreSQL persistence; no database means the radar still runs."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool

    @classmethod
    async def create(cls, database_url: str = "") -> "PostgresRepository | None":
        if not database_url:
            logger.info("PostgreSQL disabled; DATABASE_URL is empty")
            return None
        try:
            import asyncpg
            from app.database.models import SCHEMA_SQL

            pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)
            async with pool.acquire() as connection:
                await connection.execute(SCHEMA_SQL)
            logger.info("PostgreSQL persistence enabled")
            return cls(pool)
        except Exception:  # noqa: BLE001
            logger.exception("Could not initialize PostgreSQL; continuing without persistence")
            return None

    async def close(self) -> None:
        await self.pool.close()

    async def save_activity(self, activity: WalletActivity) -> None:
        signal = activity.signal
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    INSERT INTO radar_transactions
                      (tx_hash, chain, wallet_address, wallet_label, block_number,
                       observed_at, tx_from, tx_to, native_value_wei, event_type, dex,
                       router_name, action_token, quote_token, confidence, score,
                       score_components, market_json, security_json, smart_money_json,
                       analysis_reason, explorer_url)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22)
                    ON CONFLICT (tx_hash) DO UPDATE SET
                      event_type=EXCLUDED.event_type, dex=EXCLUDED.dex,
                      router_name=EXCLUDED.router_name, action_token=EXCLUDED.action_token,
                      quote_token=EXCLUDED.quote_token, confidence=EXCLUDED.confidence,
                      score=EXCLUDED.score, score_components=EXCLUDED.score_components,
                      market_json=EXCLUDED.market_json, security_json=EXCLUDED.security_json,
                      smart_money_json=EXCLUDED.smart_money_json,
                      analysis_reason=EXCLUDED.analysis_reason
                    """,
                    activity.tx_hash, activity.chain, activity.wallet.lower(), activity.wallet_label,
                    activity.block_number, activity.timestamp, activity.tx_from.lower(),
                    activity.tx_to.lower() if activity.tx_to else None,
                    str(activity.native_value_wei), activity.event_type, activity.dex,
                    activity.router_name, activity.action_token, activity.quote_token,
                    activity.confidence, signal.score if signal else None,
                    _json(signal.components) if signal else None,
                    _json(activity.market), _json(activity.security), _json(activity.smart_money),
                    activity.analysis_reason, activity.explorer_url,
                )
                for transfer in activity.transfers:
                    await connection.execute(
                        """
                        INSERT INTO radar_tokens (chain,address,symbol,name,decimals)
                        VALUES ($1,$2,$3,$4,$5)
                        ON CONFLICT (chain,address) DO UPDATE SET
                          symbol=COALESCE(EXCLUDED.symbol,radar_tokens.symbol),
                          name=COALESCE(EXCLUDED.name,radar_tokens.name),
                          decimals=COALESCE(EXCLUDED.decimals,radar_tokens.decimals)
                        """,
                        activity.chain, transfer.token, transfer.symbol, transfer.name, transfer.decimals,
                    )
                    await connection.execute(
                        """
                        INSERT INTO radar_transfers
                          (tx_hash,log_index,token_address,from_address,to_address,raw_amount,
                           symbol,name,decimals)
                        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                        ON CONFLICT (tx_hash,log_index) DO UPDATE SET
                          symbol=EXCLUDED.symbol,name=EXCLUDED.name,decimals=EXCLUDED.decimals
                        """,
                        activity.tx_hash, transfer.log_index, transfer.token,
                        transfer.from_address, transfer.to_address, str(transfer.raw_amount),
                        transfer.symbol, transfer.name, transfer.decimals,
                    )
                for pool in activity.pair_swaps:
                    if not pool.token0 or not pool.token1:
                        continue
                    await connection.execute(
                        """
                        INSERT INTO radar_pairs (chain,address,token0,token1,factory,protocol)
                        VALUES ($1,$2,$3,$4,$5,$6)
                        ON CONFLICT (chain,address) DO UPDATE SET
                          token0=EXCLUDED.token0, token1=EXCLUDED.token1,
                          factory=EXCLUDED.factory, protocol=EXCLUDED.protocol
                        """,
                        activity.chain, pool.address, pool.token0, pool.token1,
                        pool.factory, pool.protocol,
                    )
                    for token_address, symbol in (
                        (pool.token0, pool.token0_symbol), (pool.token1, pool.token1_symbol)
                    ):
                        if token_address:
                            await connection.execute(
                                """
                                INSERT INTO radar_tokens (chain,address,symbol)
                                VALUES ($1,$2,$3)
                                ON CONFLICT (chain,address) DO UPDATE SET
                                  symbol=COALESCE(EXCLUDED.symbol,radar_tokens.symbol)
                                """,
                                activity.chain, token_address, symbol,
                            )
                for index, pool in enumerate(activity.pair_swaps):
                    await connection.execute(
                        """
                        INSERT INTO radar_pair_swaps
                          (tx_hash,swap_index,version,pair_address,sender,recipient,
                           amount0_in,amount1_in,amount0_out,amount1_out,token0,token1,
                           token0_symbol,token1_symbol,factory,protocol)
                        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
                        ON CONFLICT (tx_hash,swap_index) DO UPDATE SET
                          version=EXCLUDED.version, pair_address=EXCLUDED.pair_address,
                          token0=EXCLUDED.token0, token1=EXCLUDED.token1,
                          token0_symbol=EXCLUDED.token0_symbol, token1_symbol=EXCLUDED.token1_symbol,
                          factory=EXCLUDED.factory, protocol=EXCLUDED.protocol
                        """,
                        activity.tx_hash, index, pool.version, pool.address, pool.sender,
                        pool.recipient, str(pool.amount0_in), str(pool.amount1_in),
                        str(pool.amount0_out), str(pool.amount1_out), pool.token0, pool.token1,
                        pool.token0_symbol, pool.token1_symbol, pool.factory, pool.protocol,
                    )
                await connection.execute(
                    """
                    INSERT INTO radar_signals
                      (tx_hash,wallet_label,token_address,event_type,score,components,aggregate_json)
                    VALUES ($1,$2,$3,$4,$5,$6,$7)
                    ON CONFLICT (tx_hash,event_type) DO UPDATE SET
                      score=EXCLUDED.score, components=EXCLUDED.components,
                      aggregate_json=EXCLUDED.aggregate_json
                    """,
                    activity.tx_hash, activity.wallet_label, activity.action_token,
                    activity.event_type, signal.score if signal else activity.confidence * 100,
                    _json(signal.components) if signal else None, _json(activity.smart_money),
                )


async def persist_safely(repository: PostgresRepository | None, activity: WalletActivity) -> None:
    if repository is None:
        return
    try:
        await repository.save_activity(activity)
    except Exception:  # noqa: BLE001
        logger.exception("Could not persist transaction %s", activity.tx_hash)
