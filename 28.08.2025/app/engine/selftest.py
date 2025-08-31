import time
from ..db import now_ts

async def run_selftest(conn, binance, bitget, news_ok, whale_ok, onchain_ok):
    cur = conn.cursor()
    b_pub = 1
    bg_pub = 1
    try:
        _ = binance.fetch_ticker('BTC/USDT')
    except Exception:
        b_pub = 0
    try:
        _ = bitget.fetch_ticker('BTC/USDT')
    except Exception:
        bg_pub = 0
    b_auth = 1 if binance.fetch_balance_safe() else 0
    bg_auth = 1 if bitget.fetch_balance_safe() else 0
    cur.execute("REPLACE INTO health(ts, binance_public, bitget_public, binance_auth, bitget_auth, news_ok, whale_ok, onchain_ok) VALUES(?,?,?,?,?,?,?,?)",
                (now_ts(), b_pub, bg_pub, b_auth, bg_auth, 1 if news_ok else 0, 1 if whale_ok else 0, 1 if onchain_ok else 0))
    conn.commit()
