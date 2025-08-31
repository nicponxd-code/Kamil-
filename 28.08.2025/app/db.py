# app/db.py
import os
import sqlite3
from pathlib import Path
import time
from datetime import datetime, timezone
from typing import Optional

DEFAULT_DB_PATH = os.getenv("DB_PATH", "data/bot.db")


def connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    """
    Otwiera (i tworzy) bazę SQLite z włączonym foreign_keys.
    """
    path = db_path or DEFAULT_DB_PATH
    Path(os.path.dirname(path) or ".").mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """
    Tworzy tabele, jeśli nie istnieją, oraz wykonuje lekkie migracje (ALTER).
    """
    cur = conn.cursor()

    # ---------- signals ----------
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS signals(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            side TEXT,
            entry REAL, sl REAL, tp1 REAL, tp2 REAL, tp3 REAL,
            rr REAL, edge REAL,
            confidence REAL, success REAL,
            reason TEXT,
            ts INTEGER,
            status TEXT,
            auto_ttl INTEGER,
            msg_id TEXT
        );
        """
    )

    # ---------- positions ----------
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS positions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER,
            symbol TEXT, side TEXT,
            qty REAL, entry REAL, sl REAL, tp1 REAL, tp2 REAL, tp3 REAL,
            closed INTEGER DEFAULT 0,
            pnl REAL DEFAULT 0,
            ts_open INTEGER DEFAULT (strftime('%s','now')),
            FOREIGN KEY(signal_id) REFERENCES signals(id) ON DELETE SET NULL
        );
        """
    )

    # ---------- trades ----------
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS trades(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER,
            symbol TEXT,
            side TEXT,
            qty REAL,
            price REAL,
            pnl REAL DEFAULT 0,
            ts INTEGER DEFAULT (strftime('%s','now')),
            FOREIGN KEY(signal_id) REFERENCES signals(id) ON DELETE SET NULL
        );
        """
    )

    # ---------- gems ----------
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS gems(
            symbol TEXT PRIMARY KEY,
            status TEXT,       -- 'watch' | 'sandbox'
            chain TEXT,        -- np. 'base', 'arbitrum'
            pair_addr TEXT     -- Dexscreener pairAddress
        );
        """
    )

    # MIGRACJE dla starych tabel
    cur.execute("PRAGMA table_info(gems);")
    cols = {row[1] for row in cur.fetchall()}
    if "status" not in cols:
        cur.execute("ALTER TABLE gems ADD COLUMN status TEXT;")
    if "chain" not in cols:
        cur.execute("ALTER TABLE gems ADD COLUMN chain TEXT;")
    if "pair_addr" not in cols:
        cur.execute("ALTER TABLE gems ADD COLUMN pair_addr TEXT;")

    conn.commit()


# --- helpers używane przez RiskManager i inne moduły ---

def now_ts() -> int:
    """Aktualny timestamp (UTC)."""
    return int(time.time())


def today_key(tz: timezone = timezone.utc) -> str:
    """Klucz dzienny w formacie YYYY-MM-DD (UTC)."""
    return datetime.now(tz).strftime("%Y-%m-%d")
