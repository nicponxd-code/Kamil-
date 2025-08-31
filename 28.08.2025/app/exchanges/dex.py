import aiohttp, asyncio
from typing import List, Dict, Any

DEXSCREENER_TRENDING = "https://api.dexscreener.com/latest/dex/tokens"

async def fetch_trending(limit: int=10) -> List[Dict[str, Any]]:
    # Public endpoint – filter locally for safety later
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(DEXSCREENER_TRENDING, timeout=10) as r:
                if r.status == 200:
                    data = await r.json()
                    # Harmonize shape
                    tokens = data.get('pairs') or data.get('tokens') or []
                    return tokens[:limit]
    except Exception:
        return []
    return []
