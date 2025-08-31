from dataclasses import dataclass
from typing import Optional, List

@dataclass
class Signal:
    symbol: str
    side: str          # LONG/SHORT
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp3: float
    rr: float
    edge: float
    confidence: float
    success: float
    reason: str
    status: str = "pending"
    auto_ttl: int = 0
    msg_id: Optional[str] = None
    id: Optional[int] = None
