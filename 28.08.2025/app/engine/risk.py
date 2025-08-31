# app/engine/risk.py
from __future__ import annotations

import math
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Tuple


@dataclass
class GateResult:
    ok: bool
    reason: str
    hints: str = ""  # np. 'vol_throttle' itp.


class RiskManager:
    """
    Centralny moduł bramek ryzyka.
    Odczytuje progi z SETTINGS, statystyki z DB (SQLite) i odpowiada na pytanie
    'czy mogę otworzyć nową pozycję / wyemitować sygnał?'.

    Tabele wykorzystywane (minimalny zakres):
      - signals(id, symbol, side, ts, status, ... )
      - trades(id, signal_id, ts, pnl, closed, ... )
      - positions(id, signal_id, symbol, side, qty, entry, sl, tp1, tp2, tp3, closed)
      - state(key TEXT PRIMARY KEY, value TEXT)  -- (opcjonalna, np. 'last_news_ts')

    Uwaga: funkcja `can_open` przyjmuje opcjonalne override'y progów:
      rr_min, edge_th  — pozwala chwilowo poluzować parametry w komendzie /scan.
    """

    def __init__(self, conn: sqlite3.Connection, settings):
        self.conn = conn
        self.st = settings

        # Domyślne nazwy/progi, jeśli nie ma w .env/config
        self.RR_MIN_DEFAULT = 1.0
        self.EDGE_TH_DEFAULT = 0.60
        self.MAX_TRADES_PER_DAY_DEFAULT = 4
        self.MAX_PER_PAIR_DEFAULT = 3
        self.CIRCUIT_BREAKER_PCT_DEFAULT = -5.0  # -5% dziennie
        self.TRADING_HOURS_START_DEFAULT = "07:00"
        self.TRADING_HOURS_END_DEFAULT = "23:00"
        self.NEWS_MUTE_MIN_DEFAULT = 0

        # Volatility throttle – heurystyka, opisowo (flagą w hints)
        self.VOL_ATR_PCT_TRIG = 0.02  # 2% ATR/last => pół budżetu

    # --------------------------------------------------------------------- #
    #                                Helpers                                #
    # --------------------------------------------------------------------- #

    def _get_setting(self, name: str, default):
        return getattr(self.st, name, default)

    def _now_ts(self) -> int:
        return int(time.time())

    def _today_bounds(self) -> Tuple[int, int]:
        """Zwraca (ts_start, ts_end) dzisiejszego dnia w sekundach UTC."""
        now = datetime.utcnow()
        start = datetime(now.year, now.month, now.day)
        end = start + timedelta(days=1)
        return int(start.timestamp()), int(end.timestamp())

    def _count_trades_today(self) -> int:
        """Liczba zamkniętych/otwartych trade'ów (wg wpisów w 'trades') od początku dnia."""
        start, end = self._today_bounds()
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(1) FROM trades WHERE ts >= ? AND ts < ?", (start, end))
        row = cur.fetchone()
        return int(row[0] or 0)

    def _count_signals_for_pair_today(self, symbol: str) -> int:
        start, end = self._today_bounds()
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(1) FROM signals WHERE symbol=? AND ts>=? AND ts<?", (symbol, start, end))
        row = cur.fetchone()
        return int(row[0] or 0)

    def _day_pnl_pct(self) -> float:
        """
        Szacunkowy P&L % dnia.
        Jeżeli masz dokładną księgę (equity), tutaj policz różnicę equity/day_start vs now.
        Minimalna wersja: suma 'pnl' z tabeli trades dla dzisiejszego dnia.
        """
        start, end = self._today_bounds()
        cur = self.conn.cursor()
        cur.execute("SELECT IFNULL(SUM(pnl), 0) FROM trades WHERE ts >= ? AND ts < ?", (start, end))
        row = cur.fetchone()
        try:
            return float(row[0] or 0.0)
        except Exception:
            return 0.0

    def _trading_hours_ok(self) -> bool:
        """
        Okno godzinowe. Przyjmuje HH:MM w strefie serwera (UTC jeśli serwer w UTC).
        W prostszej wersji — porównanie stringów HH:MM lokalnego czasu serwera.
        """
        start = self._get_setting("trading_hours_start", self.TRADING_HOURS_START_DEFAULT)
        end = self._get_setting("trading_hours_end", self.TRADING_HOURS_END_DEFAULT)

        try:
            now_local = datetime.now().strftime("%H:%M")
        except Exception:
            return True  # nie blokuj, jeśli nie umiemy ocenić

        if start <= end:
            return start <= now_local <= end
        # okno przez północ
        return now_local >= start or now_local <= end

    def _news_mute_active(self) -> bool:
        """
        Pauza po 'twardych newsach'.
        Oczekuje klucza 'last_news_ts' w tabeli 'state' (sekundy).
        """
        mute_min = int(self._get_setting("news_spike_mute_minutes", self.NEWS_MUTE_MIN_DEFAULT) or 0)
        if mute_min <= 0:
            return False

        cur = self.conn.cursor()
        try:
            cur.execute("SELECT value FROM state WHERE key='last_news_ts'")
            row = cur.fetchone()
            if not row:
                return False
            last_ts = int(row[0])
            return (self._now_ts() - last_ts) < (mute_min * 60)
        except Exception:
            return False

    def _circuit_breaker_tripped(self) -> bool:
        """
        Dzienny circuit breaker w % P&L (np. -5%).
        Jeśli P&L dnia <= próg → blokada do końca dnia.
        """
        th = float(self._get_setting("circuit_breaker_daily_pct", self.CIRCUIT_BREAKER_PCT_DEFAULT))
        day_pnl = self._day_pnl_pct()
        return day_pnl <= th

    # --------------------------------------------------------------------- #
    #                             Public API                                 #
    # --------------------------------------------------------------------- #

    def can_open(
        self,
        symbol: str,
        rr: float,
        edge: float,
        *,
        rr_min: Optional[float] = None,
        edge_th: Optional[float] = None,
        atr_pct: Optional[float] = None,  # opcjonalnie do throttle
        require_auth_for_on: bool = True
    ) -> Tuple[bool, str]:
        """
        Główna bramka. Zwraca (ok, reason).
        - rr_min / edge_th: override progów dla sytuacji ad-hoc (np. /scan rr_min:0.9).
        - atr_pct: jeśli przekażesz ATR/last (np. 0.018 = 1.8%) – włączamy heurystykę throttle w hints.
        """

        # 0) Tryb pracy / autoryzacje
        mode = getattr(self.st, "mode", "HYBRID").upper()
        if mode == "SAFE":
            return False, "SAFE mode – tylko analiza"
        if mode == "ON" and require_auth_for_on:
            # jeżeli nie ma kluczy do giełd – zablokuj ON
            if not (getattr(self.st, "binance_key", None) and getattr(self.st, "binance_secret", None)) \
               and not (getattr(self.st, "bitget_key", None) and getattr(self.st, "bitget_secret", None) and getattr(self.st, "bitget_password", None)):
                return False, "ON mode zablokowany – brak kluczy auth do giełd"

        # 1) Trading hours guard
        if not self._trading_hours_ok():
            return False, "Poza dozwolonym oknem godzinowym"

        # 2) News spike mute
        if self._news_mute_active():
            return False, "News-mute aktywny (pauza po twardych newsach)"

        # 3) Circuit breaker (dzienny P&L)
        if self._circuit_breaker_tripped():
            return False, "Circuit breaker dnia aktywny (limit P&L)"

        # 4) Limity wolumetryczne (global/pair)
        max_trades = int(self._get_setting("max_trades_per_day", self.MAX_TRADES_PER_DAY_DEFAULT))
        if self._count_trades_today() >= max_trades:
            return False, f"Limit dzienny wyczerpany ({max_trades})"

        per_pair = int(
            self._get_setting("max_signals_per_pair_day",
                              self._get_setting("max_trades_per_pair", self.MAX_PER_PAIR_DEFAULT))
        )
        if self._count_signals_for_pair_today(symbol) >= per_pair:
            return False, f"Limit sygnałów na parę/dzień wyczerpany ({per_pair})"

        # 5) Twarde progi jakości sygnału (RR i EDGE)
        rr_req = float(self._get_setting("rr_min", self.RR_MIN_DEFAULT)) if rr_min is None else float(rr_min)
        edge_req = float(self._get_setting("edge_threshold", self.EDGE_TH_DEFAULT)) if edge_th is None else float(edge_th)

        if rr < rr_req:
            return False, f"RR {rr:.2f} < RR_MIN {rr_req:.2f}"
        if edge < edge_req:
            return False, f"EDGE {edge:.2f} < EDGE_THRESHOLD {edge_req:.2f}"

        # 6) Volatility throttle – sygnał OK, ale ostrzeżenie (połowa budżetu)
        #    W tej implementacji zwracamy tylko sugestię w reason (silnik może to uwzględnić).
        hints = []
        try:
            if atr_pct is not None and atr_pct >= self.VOL_ATR_PCT_TRIG:
                hints.append("VOL_THROTTLE")
        except Exception:
            pass

        return True, ("OK" if not hints else "OK; " + ",".join(hints))

    # --------------------------------------------------------------------- #
    #                      Hooki pomocnicze (opcjonalne)                     #
    # --------------------------------------------------------------------- #

    def mark_news_spike(self):
        """
        Ustaw ostatni news spike (np. wywołaj z modułu CryptoPanic kiedy wykryjesz poważny alert).
        """
        cur = self.conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)")
        cur.execute("INSERT INTO state(key,value) VALUES('last_news_ts', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (self._now_ts(),))
        self.conn.commit()

    def record_signal(self, symbol: str, status: str = "pending"):
        """
        Zapisz ślad emisji sygnału (jeśli tworzysz 'ręcznie' bez engine). Użyteczne do limitów /pair/day.
        """
        cur = self.conn.cursor()
        cur.execute("INSERT INTO signals(symbol, ts, status) VALUES(?, ?, ?)", (symbol, self._now_ts(), status))
        self.conn.commit()

    def record_trade(self, pnl_pct: float):
        """
        Zapisz wynik trade'u do tabeli 'trades' – do liczenia P&L dnia i limitów.
        """
        cur = self.conn.cursor()
        cur.execute("INSERT INTO trades(ts, pnl, closed) VALUES(?, ?, 1)", (self._now_ts(), float(pnl_pct)))
        self.conn.commit()
