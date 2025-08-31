from typing import Tuple
def rr_coeff(entry: float, sl: float, tp1: float) -> Tuple[float, float]:
    risk = abs(entry - sl)
    reward = abs(tp1 - entry)
    rr = (reward / (risk+1e-9)) if risk > 0 else 0.0
    # Map RR to [0..1] with soft cap around 3.0
    coeff = max(0.0, min(1.0, rr/3.0))
    return rr, coeff
