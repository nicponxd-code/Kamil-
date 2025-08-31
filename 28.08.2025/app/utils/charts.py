# app/utils/charts.py
from __future__ import annotations
from io import BytesIO
from typing import List, Dict
import matplotlib
matplotlib.use("Agg")  # render bez okna
import matplotlib.pyplot as plt

def render_candles_png(candles: List[Dict], width: int = 900, height: int = 300) -> bytes:
    """
    Prosty wykres linii na podstawie close z listy świec Dexscreener (t,o,h,l,c).
    Nie ustawiamy kolorów ani stylów – zgodnie z wytycznymi.
    """
    if not candles:
        return b""

    closes = [float(c["c"]) for c in candles if "c" in c]
    if len(closes) < 3:
        return b""

    fig = plt.figure(figsize=(width/100, height/100), dpi=100)
    ax = fig.add_subplot(111)
    ax.plot(range(len(closes)), closes)
    ax.set_title("DEX • 15m • ostatnie 12h")
    ax.set_xlabel("świece")
    ax.set_ylabel("cena")
    ax.grid(True, which="both", linestyle="--", linewidth=0.5)

    buf = BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return buf.read()
