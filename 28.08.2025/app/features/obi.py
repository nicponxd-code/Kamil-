from typing import Dict
def obi_coeff(orderbook: Dict) -> float:
    bids = sum(x[1] for x in orderbook.get('bids', [])[:20]) if orderbook else 0.0
    asks = sum(x[1] for x in orderbook.get('asks', [])[:20]) if orderbook else 0.0
    total = bids + asks + 1e-9
    bias = (bids - asks) / total  # -1..1
    coeff = 0.5 * (bias + 1.0)    # 0..1
    return max(0.0, min(1.0, coeff))
