from __future__ import annotations
import asyncio, time, math, json, sqlite3, logging
from typing import Optional, List, Dict, Any, Tuple
import aiohttp

# Expected to exist in project
try:
    from ..config import SETTINGS
except Exception:
    class _S:  # fallback stub if direct run
        DEXSCREENER_URL = "https://api.dexscreener.com/latest/dex/tokens/USDC"
        AUTOSCAN_INTERVAL_SEC = 300
        AUTOSCAN_MIN_LIQ_USD = 20000
        AUTOSCAN_MIN_VOL24H_USD = 100000
        AUTOSCAN_SEND_LIMIT = 5
        AUTOSCAN_SCORE_MIN = 0.5
        DISCORD_GEMS_CHANNEL_ID = 0
        EXCH_LATENCY_WARN_MS = 1200
        TZ = "Europe/Brussels"
    SETTINGS = _S()

log = logging.getLogger("gems")

DEX_ENDPOINT = "https://api.dexscreener.com/latest/dex/search?q="

def _score(pair: Dict[str, Any]) -> float:
    try:
        liq = float(pair.get("liquidity", {}).get("usd", 0) or 0)
        vol = float(pair.get("volume", {}).get("h24", 0) or 0)
        fdv = float(pair.get("fdv", 0) or 0)
        txns = pair.get("txns", {}).get("h1", {})
        buys = float(txns.get("buys", 0) or 0)
        sells = float(txns.get("sells", 0) or 0)
        ratio = (buys + 1) / (sells + 1)
        # heuristic score
        s = 0.0
        s += min(liq / 100_000, 1.0) * 0.4
        s += min(vol / 500_000, 1.0) * 0.3
        s += min(max(ratio, 0) / 3.0, 1.0) * 0.2
        if fdv > 0:
            s += min(200_000_000 / fdv, 1.0) * 0.1
        return max(0.0, min(1.0, s))
    except Exception:
        return 0.0

async def fetch_pairs(session: aiohttp.ClientSession, query: str) -> List[Dict[str, Any]]:
    url = DEX_ENDPOINT + query
    async with session.get(url, timeout=15) as r:
        r.raise_for_status()
        data = await r.json()
        return data.get("pairs", []) or []

def _kv_get(conn: sqlite3.Connection, key: str, default: str = "0") -> str:
    conn.execute("""CREATE TABLE IF NOT EXISTS kv_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at INTEGER NOT NULL
    )""")
    conn.commit()
    cur = conn.execute("SELECT value FROM kv_settings WHERE key=?", (key,))
    row = cur.fetchone()
    return row[0] if row else default

def _kv_set(conn: sqlite3.Connection, key: str, value: str):
    conn.execute("""INSERT INTO kv_settings(key,value,updated_at) VALUES(?,?,?)
    ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                 (key, value, int(time.time())))
    conn.commit()

class GemsAutoscan:
    def __init__(self, bot, conn: sqlite3.Connection, reporter, interval_sec: int = None):
        self.bot = bot
        self.conn = conn
        self.reporter = reporter
        self.interval = interval_sec or getattr(SETTINGS, "AUTOSCAN_INTERVAL_SEC", 300)
        self.min_liq = getattr(SETTINGS, "AUTOSCAN_MIN_LIQ_USD", 20000)
        self.min_vol = getattr(SETTINGS, "AUTOSCAN_MIN_VOL24H_USD", 100000)
        self.limit = getattr(SETTINGS, "AUTOSCAN_SEND_LIMIT", 5)
        self.min_score = getattr(SETTINGS, "AUTOSCAN_SCORE_MIN", 0.5)
        self.channel_id = getattr(SETTINGS, "DISCORD_GEMS_CHANNEL_ID", 0)
        self._task: Optional[asyncio.Task] = None

    def is_enabled(self) -> bool:
        try:
            v = _kv_get(self.conn, "AUTOSCAN_ENABLED", "1")
            return v == "1"
        except Exception:
            return True

    def set_enabled(self, enabled: bool):
        try:
            _kv_set(self.conn, "AUTOSCAN_ENABLED", "1" if enabled else "0")
        except Exception:
            pass

    async def start(self):
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="GemsAutoscan")

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass

    async def _loop(self):
        await asyncio.sleep(3)
        log.info("Gems autoscan loop started")
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    if not self.is_enabled():
                        await asyncio.sleep(self.interval)
                        continue
                    await self._scan_once(session)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.exception("Autoscan error: %s", e)
                await asyncio.sleep(self.interval)

    async def _scan_once(self, session: aiohttp.ClientSession):
        # Simple seed queries; can be extended or pulled from SETTINGS
        queries = ["USDC", "WETH", "WBNB"]
        found: List[Tuple[float, Dict[str, Any]]] = []
        for q in queries:
            pairs = await fetch_pairs(session, q)
            for p in pairs:
                liq = float((p.get("liquidity", {}) or {}).get("usd", 0) or 0)
                vol = float((p.get("volume", {}) or {}).get("h24", 0) or 0)
                if liq < self.min_liq or vol < self.min_vol:
                    continue
                s = _score(p)
                if s >= self.min_score:
                    found.append((s, p))
        found.sort(key=lambda x: x[0], reverse=True)
        top = found[: self.limit]
        if not top:
            return
        # send embeds
        for s, p in top:
            await self._send_pair(s, p)

    async def _send_pair(self, score: float, p: Dict[str, Any]):
        # Build a minimal embed using reporter (assumed to have send_gem)
        try:
            symbol = f"{p.get('baseToken',{}).get('symbol','?')}/{p.get('quoteToken',{}).get('symbol','?')}"
            chain = p.get("chainId","?")
            url = p.get("url","")
            price = p.get("priceUsd","?")
            liq = (p.get("liquidity",{}) or {}).get("usd", 0)
            vol = (p.get("volume",{}) or {}).get("h24", 0)

            if hasattr(self.reporter, "send_gem"):
                await self.reporter.send_gem(symbol=symbol, chain=chain, url=url, score=score,
                                             price=price, liq=liq, vol=vol, channel_id=self.channel_id)
            elif hasattr(self.reporter, "send_info"):
                await self.reporter.send_info(title=f"💎 Gem: {symbol} ({chain})",
                                              description=f"score={score:.2f} • price=${price} • liq=${liq:,} • vol24h=${vol:,}\n{url}",
                                              channel_id=self.channel_id)
        except Exception:
            logging.exception("send_gem failed")
