# app/engine/command_bus.py
import time
import sqlite3
from typing import Optional, Dict, Any

class CommandBus:
    """
    Prosty dispatcher komend z tabeli `commands`.
    Działa w pętli: pobiera najstarsze komendy, wykonuje akcję, usuwa z kolejki.
    """
    def __init__(self, conn: sqlite3.Connection, settings, reporter=None,
                 binance=None, bitget=None):
        self.conn = conn
        self.st = settings
        self.reporter = reporter
        self.binance = binance
        self.bitget = bitget
        # stan wykonawczy
        self.snooze_until = 0
        self.paused = False

    def _now(self) -> int:
        return int(time.time())

    def _log_health(self, scope: str, status: str, note: str = ""):
        cur = self.conn.cursor()
        cur.execute("INSERT INTO health(ts, scope, status, note) VALUES(?, ?, ?, ?)",
                    (self._now(), scope, status, note))
        self.conn.commit()

    def _approve_reject_last(self, approve: bool):
        cur = self.conn.cursor()
        cur.execute("SELECT id FROM signals WHERE status='pending' ORDER BY ts DESC LIMIT 1")
        row = cur.fetchone()
        if not row:
            return False
        sid = row[0]
        new_status = 'approved' if approve else 'rejected'
        cur.execute("UPDATE signals SET status=? WHERE id=?", (new_status, sid))
        self.conn.commit()
        return True

    def process_once(self) -> int:
        """
        Przetwarza pojedynczą transzę komend.
        Zwraca liczbę przetworzonych wierszy.
        """
        cur = self.conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS commands(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, name TEXT, payload TEXT)")
        cur.execute("SELECT id, name, payload FROM commands ORDER BY ts ASC, id ASC LIMIT 50")
        rows = cur.fetchall()
        processed = 0
        for cid, name, payload in rows:
            name = (name or '').strip().lower()
            try:
                if name == "set_mode":
                    mode = (payload or '').strip().upper()
                    if mode in ("SAFE","HYBRID","ON"):
                        self.st.mode = mode
                        self._log_health("mode", "ok", f"set to {mode}")
                    else:
                        self._log_health("mode", "warn", f"invalid payload: {payload}")

                elif name == "selftest":
                    # szybki public/auth check przez klasy exchange
                    b_pub = g_pub = 1
                    try:
                        _ = self.binance.fetch_ticker('BTC/USDT')
                    except Exception:
                        b_pub = 0
                    try:
                        _ = self.bitget.fetch_ticker('BTC/USDT')
                    except Exception:
                        g_pub = 0
                    b_auth = 1 if self.binance.fetch_balance_safe() else 0
                    g_auth = 1 if self.bitget.fetch_balance_safe() else 0
                    self._log_health("binance", "ok" if b_pub else "fail", f"auth={b_auth}")
                    self._log_health("bitget", "ok" if g_pub else "fail", f"auth={g_auth}")

                elif name == "pause":
                    self.paused = True
                    self._log_health("engine", "paused", "manual")

                elif name == "resume":
                    self.paused = False
                    self._log_health("engine", "running", "manual")

                elif name.startswith("snooze_"):
                    mins = 10
                    try:
                        mins = int(name.split("_",1)[1].replace("m",""))
                    except Exception:
                        pass
                    self.snooze_until = self._now() + mins*60
                    self._log_health("engine", "snooze", f"{mins}m")

                elif name == "toggle_hybrid":
                    self.st.mode = "HYBRID" if self.st.mode != "HYBRID" else "SAFE"
                    self._log_health("mode", "ok", f"toggle -> {self.st.mode}")

                elif name == "approve_last":
                    ok = self._approve_reject_last(True)
                    self._log_health("signals", "approved" if ok else "empty")

                elif name == "reject_last":
                    ok = self._approve_reject_last(False)
                    self._log_health("signals", "rejected" if ok else "empty")

                elif name in ("status","panel","portfolio","gems","alert_test","rerun_scan","scan_market"):
                    # Miejsce na integrację z reporterem/schedulerem
                    self._log_health("cmd", "ok", name)

                else:
                    self._log_health("cmd", "unknown", name)

            finally:
                # skasuj przetworzoną komendę
                cur2 = self.conn.cursor()
                cur2.execute("DELETE FROM commands WHERE id=?", (cid,))
                self.conn.commit()
                processed += 1

        return processed
