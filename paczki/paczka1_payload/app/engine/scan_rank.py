from __future__ import annotations
import asyncio, sqlite3, logging, time
from typing import List, Dict, Any, Tuple

log = logging.getLogger("scanrank")

def rank_signals(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    rows: [{'symbol':..., 'rr':..., 'confidence':..., 'edge':..., 'success':...}, ...]
    Returns rows sorted by composite score.
    """
    scored = []
    for r in rows:
        rr = float(r.get("rr", 0) or 0)
        conf = float(r.get("confidence", 0) or 0)
        edge = float(r.get("edge", 0) or 0)
        succ = float(r.get("success", 0) or 0)
        score = (min(rr, 5.0)/5.0)*0.35 + conf*0.30 + edge*0.20 + succ*0.15
        r2 = dict(r)
        r2["score"] = round(score, 4)
        scored.append(r2)
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored

async def run_scan_and_rank(conn: sqlite3.Connection, reporter, limit: int = 10):
    # naive example: rank last N signals in DB
    cur = conn.execute("""SELECT symbol, rr, confidence, edge, success, id
                          FROM signals ORDER BY created_at DESC LIMIT ?""", (limit,))
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, x)) for x in cur.fetchall()]
    ranked = rank_signals(rows)
    # reporter expected to have send_rank()
    if hasattr(reporter, "send_rank"):
        await reporter.send_rank(ranked)
    elif hasattr(reporter, "send_info"):
        text = "\n".join([f"{i+1}. {r['symbol']} • score={r['score']} • RR={r['rr']} • conf={r['confidence']}" for i,r in enumerate(ranked)])
        await reporter.send_info("🔎 Scan & Rank", text)
