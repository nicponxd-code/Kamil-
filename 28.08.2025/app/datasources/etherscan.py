import aiohttp
from typing import Tuple

async def score_onchain(api_key: str) -> Tuple[float, bool]:
    if not api_key:
        return 0.5, False
    # very rough proxy: gas price regime
    url = f"https://api.etherscan.io/api?module=gastracker&action=gasoracle&apikey={api_key}"
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(url, timeout=10) as r:
                ok = r.status == 200
                data = await r.json()
                result = data.get('result', {})
                safe = float(result.get('SafeGasPrice', 20))
                fast = float(result.get('FastGasPrice', 20))
                # If fast >> safe → elevated activity → trendiness (0.6+)
                ratio = (fast+1e-6)/(safe+1e-6)
                score = max(0.0, min(1.0, (ratio-0.8)/1.2))
                return score, ok
    except Exception:
        return 0.5, False
