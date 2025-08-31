import math, time, os, random
from typing import Dict, Tuple
from ..features.rr import rr_coeff as rr_calc

def plan_trade(side: str, last: float, vola: float) -> Dict:
    """Heurystyka generująca entry/SL/TPx. Vola to ATR-ish (w % ceny)."""
    # scale basic distances by vola
    step = max(0.003, min(0.02, vola / max(last,1e-6)))
    if side == 'LONG':
        entry = last * (1 - 0.2*step)
        sl    = last * (1 - 2.5*step)
        tp1   = last * (1 + 1.2*step)
        tp2   = last * (1 + 2.5*step)
        tp3   = last * (1 + 4.0*step)
    else:
        entry = last * (1 + 0.2*step)
        sl    = last * (1 + 2.5*step)
        tp1   = last * (1 - 1.2*step)
        tp2   = last * (1 - 2.5*step)
        tp3   = last * (1 - 4.0*step)
    rr, rr_c = rr_calc(entry, sl, tp1)
    conf = 0.65 + 0.2*random.random()   # 0.65..0.85
    succ = 0.60 + 0.25*random.random()  # 0.60..0.85
    return dict(entry=entry, sl=sl, tp1=tp1, tp2=tp2, tp3=tp3, rr=rr, conf=conf, success=succ)
