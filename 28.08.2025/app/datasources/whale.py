import aiohttp
from typing import Tuple

async def score_whales(api_key: str) -> Tuple[float, bool]:
    if not api_key:
        return 0.5, False
    url = f"https://api.whale-alert.io/v1/transactions?api_key={api_key}&min_value=500000"
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(url, timeout=10) as r:
                ok = r.status == 200
                data = await r.json()
                txs = data.get('transactions', [])
                # crude: more inflow → bullish-ish
                inflow = sum(1 for t in txs if t.get('to'))
                outflow = sum(1 for t in txs if t.get('from'))
                total = inflow + outflow + 1e-6
                score = (inflow - outflow + total) / (2*total)
                score = max(0.0, min(1.0, score))
                return score, ok
    except Exception:
        return 0.5, False
