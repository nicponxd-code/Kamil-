# app/datasources/huggingface.py
import aiohttp
from typing import Tuple
from ..config import SETTINGS

HF_URL = "https://api-inference.huggingface.co/models/distilbert-base-uncased-finetuned-sst-2-english"

async def score_hf_sentiment(text: str = "crypto momentum looks strong") -> Tuple[float, bool]:
    """
    Używa HF Inference API do klasyfikacji sentimentu. Mapuje positive/negative -> 0..1.
    Brak klucza => (0.5, False).
    """
    if not SETTINGS.hf_key:
        return 0.5, False

    headers = {"Authorization": f"Bearer {SETTINGS.hf_key}"}
    payload = {"inputs": text}
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(HF_URL, headers=headers, json=payload, timeout=12) as r:
                ok = r.status == 200
                data = await r.json()
                if not ok or not isinstance(data, list) or not data:
                    return 0.5, ok
                # data: [[{"label":"POSITIVE","score":0.99}, {"label":"NEGATIVE","score":0.01}]]
                opts = data[0]
                score_pos = 0.5
                for el in opts:
                    if el.get("label") == "POSITIVE":
                        score_pos = float(el.get("score", 0.5))
                        break
                return float(score_pos), ok
    except Exception:
        return 0.5, False
