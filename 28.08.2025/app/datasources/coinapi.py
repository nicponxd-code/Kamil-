# app/datasources/coinapi.py
import aiohttp
from typing import Tuple
from ..config import SETTINGS

COINAPI_URL = "https://rest.coinapi.io/v1/exchangerate/BTC/USDT"

async def ping_coinapi() -> Tuple[float, bool]:
    """
    Prosty ping do CoinAPI – próbuje pobrać kurs BTC/USDT. Sukces => zwraca (0.6, True),
    porażka/brak klucza => (0.5, False). Score nie wchodzi do EDGE; to check zdrowia.
    """
    if not SETTINGS.coinapi_key:
        return 0.5, False
    headers = {"X-CoinAPI-Key": SETTINGS.coinapi_key}
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(COINAPI_URL, headers=headers, timeout=10) as r:
                ok = r.status == 200
                return (0.6 if ok else 0.5), ok
    except Exception:
        return 0.5, False
