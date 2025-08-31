# app/datasources/groq.py
import aiohttp
from typing import Tuple
from ..config import SETTINGS

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"

async def score_groq_sentiment() -> Tuple[float, bool]:
    """
    Lekki test integracji: prosimy model o krótką ocenę "bull/bear/neutral" i mapujemy na 0..1.
    Zwraca (score, ok). Brak klucza => (0.5, False).
    """
    if not SETTINGS.groq_key:
        return 0.5, False

    headers = {
        "Authorization": f"Bearer {SETTINGS.groq_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "llama3-8b-8192",  # popularny, tani do ping testów
        "messages": [
            {"role": "system", "content": "Respond with one word: bull, bear, or neutral."},
            {"role": "user", "content": "Crypto market near-term sentiment?"}
        ],
        "temperature": 0.0,
        "max_tokens": 4
    }
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(GROQ_CHAT_URL, headers=headers, json=payload, timeout=12) as r:
                ok = r.status == 200
                data = await r.json()
                txt = (data.get("choices", [{}])[0]
                         .get("message", {})
                         .get("content", "")
                         .strip().lower())
                if "bull" in txt:
                    return 0.75, ok
                if "bear" in txt:
                    return 0.25, ok
                return 0.5, ok
    except Exception:
        return 0.5, False
