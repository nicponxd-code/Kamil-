from ..models import Signal
from typing import Optional
import time

class Router:
    def __init__(self, settings, conn, reporter, risk_manager):
        self.st = settings
        self.conn = conn
        self.reporter = reporter
        self.risk = risk_manager

    async def route(self, sig: Signal):
        # Persist
        cur = self.conn.cursor()
        cur.execute("""                INSERT INTO signals(ts, symbol, side, entry, sl, tp1, tp2, tp3, rr, edge, confidence, success, reason, status, auto_ttl)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (int(time.time()), sig.symbol, sig.side, sig.entry, sig.sl, sig.tp1, sig.tp2, sig.tp3, sig.rr, sig.edge, sig.confidence, sig.success, sig.reason, sig.status, sig.auto_ttl))
        self.conn.commit()
        sig.id = cur.lastrowid
        # Mode
        if self.st.mode == 'SAFE':
            await self.reporter.send_signal(sig, mode='SAFE')
        elif self.st.mode == 'HYBRID':
            await self.reporter.send_signal(sig, mode='HYBRID')
        else:  # ON
            await self.reporter.send_signal(sig, mode='ON')
            await self.reporter.on_approved(sig)  # immediate execute/paper
