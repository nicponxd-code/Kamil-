-- paczka1: KV + cache tables for gems autoscan
CREATE TABLE IF NOT EXISTS kv_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS gems_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT,
    chain TEXT,
    url TEXT,
    score REAL,
    price_usd REAL,
    liq_usd REAL,
    vol24h_usd REAL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gems_cache_created ON gems_cache(created_at);
