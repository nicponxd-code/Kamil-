from typing import Tuple, List
import math

def atr(ohlcv: List[List[float]], period: int=14) -> float:
    trs = []
    for i in range(1, min(len(ohlcv), period+1)):
        prev = ohlcv[-i-1]
        cur = ohlcv[-i]
        high, low, close_prev = cur[2], cur[3], prev[4]
        tr = max(high-low, abs(high-close_prev), abs(low-close_prev))
        trs.append(tr)
    if not trs:
        return 0.0
    return sum(trs)/len(trs)

def fvg_scores(ohlcv: List[List[float]]) -> Tuple[float, float]:
    """Return (fvg_long, fvg_short) in [0..1].
    Very compact heuristic: look for most recent gap vs ATR.
    """
    if len(ohlcv) < 5:
        return 0.5, 0.5
    a = atr(ohlcv, 14) or 1e-6
    # Use last 3 candles for 'freshness'
    long_gap = 0.0
    short_gap = 0.0
    for i in range(2, 5):
        c0 = ohlcv[-i]     # older
        c1 = ohlcv[-i+1]   # newer
        # Bull FVG when c0.high < c1.low (gap up) → long context
        gap_up = max(0.0, c1[1] - c0[2])
        # Bear FVG when c0.low > c1.high (gap down) → short context
        gap_dn = max(0.0, c0[3] - c1[2])
        long_gap = max(long_gap, gap_up)
        short_gap = max(short_gap, gap_dn)
    f_long = max(0.0, min(1.0, long_gap / a))
    f_short = max(0.0, min(1.0, short_gap / a))
    # light normalization bias to 0.5 if tiny
    f_long = 0.5 + (f_long-0.5)*0.8
    f_short = 0.5 + (f_short-0.5)*0.8
    return f_long, f_short
