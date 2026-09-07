from __future__ import annotations

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS radar_transactions (
    tx_hash TEXT PRIMARY KEY,
    chain TEXT NOT NULL,
    wallet_address TEXT NOT NULL,
    wallet_label TEXT NOT NULL,
    block_number BIGINT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    tx_from TEXT NOT NULL,
    tx_to TEXT,
    native_value_wei NUMERIC(78, 0) NOT NULL DEFAULT 0,
    event_type TEXT NOT NULL,
    dex TEXT,
    router_name TEXT,
    action_token TEXT,
    quote_token TEXT,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
    analysis_reason TEXT NOT NULL DEFAULT '',
    explorer_url TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS radar_tokens (
    chain TEXT NOT NULL,
    address TEXT NOT NULL,
    symbol TEXT,
    name TEXT,
    decimals INTEGER,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (chain, address)
);

CREATE TABLE IF NOT EXISTS radar_pairs (
    chain TEXT NOT NULL,
    address TEXT NOT NULL,
    token0 TEXT NOT NULL,
    token1 TEXT NOT NULL,
    factory TEXT,
    protocol TEXT,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (chain, address)
);

CREATE TABLE IF NOT EXISTS radar_transfers (
    tx_hash TEXT NOT NULL REFERENCES radar_transactions(tx_hash) ON DELETE CASCADE,
    log_index INTEGER NOT NULL,
    token_address TEXT NOT NULL,
    from_address TEXT NOT NULL,
    to_address TEXT NOT NULL,
    raw_amount NUMERIC(78, 0) NOT NULL,
    symbol TEXT,
    name TEXT,
    decimals INTEGER,
    PRIMARY KEY (tx_hash, log_index)
);

CREATE TABLE IF NOT EXISTS radar_pair_swaps (
    tx_hash TEXT NOT NULL REFERENCES radar_transactions(tx_hash) ON DELETE CASCADE,
    swap_index INTEGER NOT NULL,
    version TEXT NOT NULL,
    pair_address TEXT NOT NULL,
    sender TEXT NOT NULL,
    recipient TEXT NOT NULL,
    amount0_in NUMERIC(78, 0),
    amount1_in NUMERIC(78, 0),
    amount0_out NUMERIC(78, 0),
    amount1_out NUMERIC(78, 0),
    token0 TEXT,
    token1 TEXT,
    token0_symbol TEXT,
    token1_symbol TEXT,
    factory TEXT,
    protocol TEXT,
    PRIMARY KEY (tx_hash, swap_index)
);

CREATE TABLE IF NOT EXISTS radar_signals (
    id BIGSERIAL PRIMARY KEY,
    tx_hash TEXT NOT NULL REFERENCES radar_transactions(tx_hash) ON DELETE CASCADE,
    wallet_label TEXT NOT NULL,
    event_type TEXT NOT NULL,
    score DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tx_hash, event_type)
);

CREATE INDEX IF NOT EXISTS idx_radar_transactions_wallet_time
    ON radar_transactions (wallet_address, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_radar_signals_created_at
    ON radar_signals (created_at DESC);
"""
