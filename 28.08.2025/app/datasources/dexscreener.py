# app/datasources/dexscreener.py
from __future__ import annotations
import time
from typing import Any, Dict, List, Tuple

import aiohttp

DEX_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

BASE_BLACKLIST = {"BTC", "ETH", "BNB", "SOL", "MATIC", "TRX"}
QUOTE_BLACKLIST = {"BTC", "ETH", "WETH", "WBNB", "WMATIC", "WBTC"}
STABLES = {"USDT", "USDC", "BUSD", "DAI", "FDUSD", "TUSD"}

def _pair_url(chain: str, pair_addr: str) -> str:
    return f"https://dexscreener.com/{chain}/{pair_addr}"

# app/datasources/dexscreener.py  (TYLKO fragmenty filtra + eksportu)

# ... nagłówki i stałe bez zmian ...

def safe_gem_with_thresholds(
    pair: Dict[str, Any],
    min_liq: int = 20_000,
    min_vol: int = 50_000,
    min_tx: int = 30
) -> tuple[bool, str]:
    """Parametryzowany filtr bezpieczeństwa."""
    base = (pair.get("baseToken") or {}).get("symbol", "?").upper()
    quote = (pair.get("quoteToken") or {}).get("symbol", "?").upper()
    liq = (pair.get("liquidity") or {}).get("usd", 0) or 0
    vol = (pair.get("volume") or {}).get("h24", 0) or 0
    tx = (pair.get("txns") or {}).get("h24") or {}
    buys, sells = int(tx.get("buys", 0) or 0), int(tx.get("sells", 0) or 0)
    total_tx = buys + sells

    if base in BASE_BLACKLIST:
        return False, f"base {base} in blacklist"
    if quote in QUOTE_BLACKLIST:
        return False, f"quote {quote} in blacklist"
    if liq < min_liq:
        return False, f"liquidity < {min_liq} (${liq:,.0f})"
    if vol < min_vol:
        return False, f"volume < {min_vol} (${vol:,.0f})"
    if total_tx < min_tx:
        return False, f"tx < {min_tx} / 24h"
    return True, "ok"


async def fetch_trending_filtered(session, limit: int, min_liq: int, min_vol: int, min_tx: int):
    """Zwrot już przefiltrowanych gemów wg progów."""
    url = "https://api.dexscreener.com/latest/dex/search?q=trending"
    async with session.get(url, headers=DEX_HEADERS, timeout=15) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}: {(await resp.text())[:180]}")
        data = await resp.json(content_type=None)

    pairs = data.get("pairs", []) if isinstance(data, dict) else []
    out: list[dict] = []
    for p in pairs:
        ok, _ = safe_gem_with_thresholds(p, min_liq=min_liq, min_vol=min_vol, min_tx=min_tx)
        if not ok:
            continue
        chain = p.get("chain", "unknown")
        pair_addr = p.get("pairAddress") or p.get("pairAddressV2") or ""
        base = (p.get("baseToken") or {}).get("symbol", "?")
        quote = (p.get("quoteToken") or {}).get("symbol", "?")
        liq = (p.get("liquidity") or {}).get("usd", 0) or 0
        vol = (p.get("volume") or {}).get("h24", 0) or 0

        out.append({
            "display": f"{base}/{quote} ({chain})",
            "chain": chain,
            "pair": pair_addr,
            "base": base,
            "quote": quote,
            "liquidity_usd": liq,
            "volume_h24": vol,
            "url": _pair_url(chain, pair_addr),
        })
    out.sort(key=lambda x: (x["liquidity_usd"], x["volume_h24"]), reverse=True)
    return out[:limit]


async def fetch_candles(session: aiohttp.ClientSession, pair_addr: str, minutes: int = 12*60, resolution: str = "15") -> List[Dict[str, Any]]:
    """
    Pobierz świece (candles) z Dexscreener Chart API.
    """
    now = int(time.time())
    fr = now - minutes * 60
    url = f"https://api.dexscreener.com/chart/candles?pairAddress={pair_addr}&from={fr}&to={now}&resolution={resolution}"
    async with session.get(url, headers=DEX_HEADERS, timeout=20) as resp:
        if resp.status != 200:
            # zwróć pustą listę – pozwoli to pokazać embed bez obrazka
            return []
        data = await resp.json(content_type=None)
    # spodziewamy się listy słowników {"t":ts,"o":..,"h":..,"l":..,"c":..}
    return data if isinstance(data, list) else []
