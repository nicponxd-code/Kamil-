import aiohttp
from typing import Tuple

async def score_news(api_key: str) -> Tuple[float, bool]:
    """Return (score, ok) where score in [0..1]. If no key/err → (0.5, False)."""
    if not api_key:
        return 0.5, False
    url = f"https://cryptopanic.com/api/v1/posts/?auth_token={api_key}&public=true"
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(url, timeout=10) as r:
                ok = r.status == 200
                # naive scoring: if many 'negative' labels, drop
                data = await r.json()
                posts = data.get('results', [])
                neg = sum(1 for p in posts[:50] if 'negative' in str(p).lower())
                pos = sum(1 for p in posts[:50] if 'positive' in str(p).lower())
                total = pos + neg + 1e-6
                score = (pos - neg + total) / (2*total)  # ~0..1
                score = max(0.0, min(1.0, score))
                return score, ok
    except Exception:
        return 0.5, False
