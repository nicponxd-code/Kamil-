# app/engine/runner.py
from __future__ import annotations

import asyncio
import time
from typing import Optional

from ..config import SETTINGS
from ..db import connect, init_schema
from ..exchanges.binance import BinanceX
from ..exchanges.bitget import BitgetX
from ..engine.collector import Collector
from ..features.fvg import fvg_scores, atr
from ..features.rr import rr_coeff
from ..features.obi import obi_coeff
from ..engine.fusion import fuse_edge
from ..engine.risk import RiskManager
from ..engine.planner_ai import plan_openai
from ..models import Signal


class Engine:
    """
    Główny silnik:
    - pętla selftest (co N min),
    - pętla tick (rotacja symboli co tick_seconds),
    - pętla pending (auto-approve/auto-reject),
    - pętla autoscan (TOP alty co X min; domyślnie co 6h, z auto-relax),
    - router sygnałów do reportera,
    - quick_signal() – sygnał testowy z opcją bypass_gates i channel_id,
    - analyze_dex_pair() – analiza pary z Dexscreener po pairAddress.
    """

    def __init__(self, bot=None):
        self.st = SETTINGS
        self.conn = connect(self.st.db_path)
        init_schema(self.conn)

        # Giełdy
        self.binance = BinanceX(self.st.binance_key, self.st.binance_secret)
        self.bitget = BitgetX(self.st.bitget_key, self.st.bitget_secret, self.st.bitget_password)

        # Collector / Risk
        self.collector = Collector(self.binance, self.bitget)
        self.risk = RiskManager(self.conn, self.st)

        # Reporter (wstrzykiwany z bot.py)
        self.bot = bot
        self.reporter = None

        # Zadania w tle
        self._tasks: list[asyncio.Task] = []

        # Makro-sygnały (domyślnie neutral)
        self.news_score: float = 0.5
        self.whale_score: float = 0.5
        self.onchain_score: float = 0.5

    # ------------------------------------------------------------------ #
    #                           Lifecycle                                 #
    # ------------------------------------------------------------------ #
    async def start(self, reporter):
        """Uruchom pętle w tle i zapamiętaj reportera."""
        self.reporter = reporter
        self._tasks.append(asyncio.create_task(self.loop_selftest()))
        self._tasks.append(asyncio.create_task(self.loop_tick()))
        self._tasks.append(asyncio.create_task(self.loop_pending()))
        self._tasks.append(asyncio.create_task(self.loop_autoscan()))  # autoskan altów

    async def loop_selftest(self):
        """Self-test źródeł i zapis neutralizacji/health co X minut."""
        interval = max(1, int(getattr(self.st, "selftest_minutes", 5))) * 60
        while True:
            try:
                # NEWS
                try:
                    from ..datasources.cryptopanic import score_news
                    n_score, n_ok = await score_news(getattr(self.st, "cryptopanic_key", ""))
                    self.news_score = n_score if n_ok else 0.5
                except Exception:
                    self.news_score = 0.5

                # WHALE
                try:
                    from ..datasources.whale import score_whales
                    w_score, w_ok = await score_whales(
                        getattr(self.st, "whale_key", "") or getattr(self.st, "whale_api_key", "")
                    )
                    self.whale_score = w_score if w_ok else 0.5
                except Exception:
                    self.whale_score = 0.5

                # ONCHAIN
                try:
                    from ..datasources.etherscan import score_onchain
                    o_score, o_ok = await score_onchain(
                        getattr(self.st, "etherscan_key", "") or getattr(self.st, "etherscan_api_key", "")
                    )
                    self.onchain_score = o_score if o_ok else 0.5
                except Exception:
                    self.onchain_score = 0.5

            except Exception:
                pass

            await asyncio.sleep(interval)

    async def loop_pending(self):
        """Auto-approve / auto-reject sygnałów w statusie 'pending'."""
        auto_approve_conf = float(getattr(self.st, "auto_approve_conf", 0.80))
        auto_reject_conf = float(getattr(self.st, "auto_reject_conf", 0.60))
        approve_after = int(getattr(self.st, "auto_approve_after", 120))
        reject_after = int(getattr(self.st, "auto_reject_after", 600))

        while True:
            try:
                cur = self.conn.cursor()
                cur.execute("SELECT id, confidence, auto_ttl, status FROM signals WHERE status='pending'")
                rows = cur.fetchall()
                now = int(time.time())
                for _id, conf, ttl, _status in rows:
                    ttl = int(ttl or 0)
                    if conf >= auto_approve_conf and now - ttl >= approve_after:
                        await self.signal_approved(_id)
                        cur2 = self.conn.cursor()
                        cur2.execute("UPDATE signals SET status='approved' WHERE id=?", (_id,))
                        self.conn.commit()
                    elif conf < auto_reject_conf and now - ttl >= reject_after:
                        cur2 = self.conn.cursor()
                        cur2.execute("UPDATE signals SET status='rejected' WHERE id=?", (_id,))
                        self.conn.commit()
            except Exception:
                pass

            await asyncio.sleep(10)

    async def loop_autoscan(self):
        """
        Co autoscan_interval_min skanuje alty (bez majorów) i wysyła do autoscan_limit sygnałów.
        Domyślnie 6h, z mechanizmem auto-relax progów (−5% min_vol, +5% max_vol, −0.01 EDGE, −0.02 RR)
        aż do skutku (limit kroków).
        """
        from ..engine.analyzer import Analyzer

        while True:
            try:
                if not bool(getattr(self.st, "autoscan_enabled", True)):
                    await asyncio.sleep(30)
                    continue

                # Bazowe progi
                interval = max(5, int(getattr(self.st, "autoscan_interval_min", 360))) * 60
                limit    = int(getattr(self.st, "autoscan_limit", 5))
                min_vol  = float(getattr(self.st, "autoscan_min_vol", 3_000_000.0))
                max_vol  = float(getattr(self.st, "autoscan_max_vol", 60_000_000.0))
                rr_min   = float(getattr(self.st, "autoscan_rr_min", 0.90))
                edge_th  = float(getattr(self.st, "autoscan_edge_th", 0.55))
                exclude  = set(getattr(self.st, "autoscan_exclude", {"BTC/USDT", "ETH/USDT"}))

                relax_steps  = int(getattr(self.st, "autoscan_relax_steps", 10))
                relax_factor = float(getattr(self.st, "autoscan_relax_factor", 0.95))  # -5% min_vol / +5% max_vol
                relax_edge   = float(getattr(self.st, "autoscan_relax_edge", 0.01))    # -0.01 / krok (>=0.50)
                relax_rr     = float(getattr(self.st, "autoscan_relax_rr", 0.02))      # -0.02 / krok (>=0.80)

                analyzer = Analyzer(engine=self)

                # 1) Próba na twardych progach
                results = await analyzer.scan_alt_gems(
                    limit=limit,
                    min_quote_vol=min_vol,
                    max_quote_vol=max_vol,
                    rr_min=rr_min,
                    edge_th=edge_th,
                    exclude=exclude
                )

                # 2) Auto-relax jeśli pusto
                steps_done = 0
                cur_min_vol, cur_max_vol = min_vol, max_vol
                cur_rr, cur_edge = rr_min, edge_th

                while not results and steps_done < relax_steps:
                    steps_done += 1
                    cur_min_vol *= relax_factor
                    cur_max_vol *= (1.0 / relax_factor)  # lekko rozszerz sufit
                    cur_rr   = max(0.80, cur_rr - relax_rr)
                    cur_edge = max(0.50, cur_edge - relax_edge)

                    results = await analyzer.scan_alt_gems(
                        limit=limit,
                        min_quote_vol=cur_min_vol,
                        max_quote_vol=cur_max_vol,
                        rr_min=cur_rr,
                        edge_th=cur_edge,
                        exclude=exclude
                    )

                # 3) Jeśli są – zrób sygnały
                if results and self.reporter:
                    for r in results[:limit]:
                        sig = Signal(
                            symbol=r.symbol, side=r.side, entry=r.entry, sl=r.sl,
                            tp1=r.tp1, tp2=r.tp2, tp3=r.tp3,
                            rr=r.rr, edge=r.edge, confidence=r.confidence, success=r.success,
                            reason=r.reason or f"autoscan (relaxed {steps_done}x)",
                            status="pending", auto_ttl=int(time.time())
                        )
                        cur = self.conn.cursor()
                        cur.execute(
                            """INSERT INTO signals(symbol, side, entry, sl, tp1, tp2, tp3, rr, edge, confidence, success, reason, status, auto_ttl, ts)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?, strftime('%s','now'))""",
                            (sig.symbol, sig.side, sig.entry, sig.sl, sig.tp1, sig.tp2, sig.tp3,
                             sig.rr, sig.edge, sig.confidence, sig.success, sig.reason, sig.status, sig.auto_ttl)
                        )
                        self.conn.commit()
                        await self.reporter.send_signal(sig, mode=self.st.mode)

            except Exception as e:
                print(f"[autoscan] error: {e}")

            # Odczekaj do następnego skanu
            try:
                interval = max(5, int(getattr(self.st, "autoscan_interval_min", 360))) * 60
                await asyncio.sleep(interval)
            except Exception:
                await asyncio.sleep(60)

    async def loop_tick(self):
        """Rotacja symboli – co tick_seconds analizuj jeden symbol."""
        idx = 0
        tick_seconds = int(getattr(self.st, "tick_seconds", 60))
        syms = list(self.st.symbols) if getattr(self.st, "symbols", None) else ["BTC/USDT", "ETH/USDT"]

        while True:
            symbol = syms[idx % len(syms)]
            idx += 1
            try:
                await self.tick_symbol(symbol)
            except Exception as e:
                print(f"[tick] error {symbol}: {e}")
            await asyncio.sleep(tick_seconds)

    # ------------------------------------------------------------------ #
    #                         Główna analiza                              #
    # ------------------------------------------------------------------ #
    async def _get_last_price(self, ohlcv, ticker) -> Optional[float]:
        """Bezpieczne pobranie ostatniej ceny."""
        try:
            return float(
                (ticker or {}).get("last")
                or (ticker or {}).get("close")
                or (ohlcv[-1][4] if ohlcv and len(ohlcv[-1]) >= 5 else None)
            )
        except Exception:
            return None

    def _extract_plan(self, plan: dict, last: float, atr_val: float) -> dict:
        """
        Ujednolicone pobranie metryk z planu + domyślne fallbacki.
        Zwraca dict z kluczami: entry, sl, tp1, tp2, tp3, rr, conf, succ.
        """
        conf = float(plan.get("conf", plan.get("confidence", 0.75)))
        succ = float(plan.get("success", plan.get("success_chance", 0.60)))
        rr   = float(plan.get("rr", 1.0))

        entry = float(plan.get("entry", last))
        sl    = float(plan.get("sl", last - atr_val))
        tp1   = float(plan.get("tp1", last + atr_val * 0.8))
        tp2   = float(plan.get("tp2", last + atr_val * 1.6))
        tp3   = float(plan.get("tp3", last + atr_val * 2.4))

        return dict(entry=entry, sl=sl, tp1=tp1, tp2=tp2, tp3=tp3, rr=rr, conf=conf, succ=succ)

    async def tick_symbol(self, symbol: str):
        """
        Standardowy 'tick' dla jednego symbolu:
        - zbiera rynek (OHLCV/ticker/OB),
        - liczy feature’y (FVG/ATR/OBI/RR),
        - fusion EDGE (z makro: NEWS/WHALE/ONCHAIN),
        - plan AI,
        - bramki ryzyka,
        - router do reportera (signal embed).
        """
        # Market
        ohlcv, ticker, obook = await self.collector.get_market(symbol, '15m', 200)
        last = await self._get_last_price(ohlcv, ticker)
        if last is None:
            return  # brak ceny

        # Feature’y
        f_long, f_short = fvg_scores(ohlcv)
        atr_val = atr(ohlcv, 14)
        obi = obi_coeff(obook)
        _rr_val, rr_c = rr_coeff(last, last - atr_val * 0.5, last + atr_val * 0.8)

        # Makro (z selftest/neutral)
        news = float(getattr(self, "news_score", 0.5))
        whale = float(getattr(self, "whale_score", 0.5))
        onc = float(getattr(self, "onchain_score", 0.5))

        # Fusion EDGE
        long_edge, short_edge = fuse_edge(
            f_long, f_short, rr_c, obi, news, whale, onc,
            self.st.w_fvg, self.st.w_rr, self.st.w_obi, self.st.w_news, self.st.w_whale, self.st.w_onc
        )

        # Kierunek
        side = 'LONG' if long_edge >= short_edge else 'SHORT'
        edge = max(long_edge, short_edge)

        # Plan AI
        plan = plan_openai(
            dict(f_long=f_long, f_short=f_short, rr_c=rr_c, obi=obi, news=news, whale=whale, onc=onc),
            side, last, atr_val
        )
        p = self._extract_plan(plan, last, atr_val)

        ok, why = self.risk.can_open(symbol, p["rr"], edge, atr_pct=(atr_val / max(last, 1e-9)))
        if not ok:
            return

        # Zbuduj Signal i wyślij
        sig = Signal(
            symbol=symbol, side=side, entry=p["entry"], sl=p["sl"],
            tp1=p["tp1"], tp2=p["tp2"], tp3=p["tp3"],
            rr=p["rr"], edge=edge, confidence=p["conf"], success=p["succ"],
            reason=f"{why}; FVG:{f_long:.2f}/{f_short:.2f} OBI:{obi:.2f} ATR:{atr_val:.5f}",
            status='pending', auto_ttl=int(time.time())
        )
        await self.router(sig)

    # ------------------------------------------------------------------ #
    #                        Router / Akceptacja                          #
    # ------------------------------------------------------------------ #
    async def router(self, sig: Signal):
        """Wyślij sygnał do reportera (Discord) oraz zarejestruj w DB."""
        cur = self.conn.cursor()
        cur.execute(
            """INSERT INTO signals(symbol, side, entry, sl, tp1, tp2, tp3, rr, edge, confidence, success, reason, status, auto_ttl, ts)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?, strftime('%s','now'))""",
            (sig.symbol, sig.side, sig.entry, sig.sl, sig.tp1, sig.tp2, sig.tp3,
             sig.rr, sig.edge, sig.confidence, sig.success, sig.reason, sig.status, sig.auto_ttl)
        )
        self.conn.commit()

        if self.reporter:
            await self.reporter.send_signal(sig, mode=self.st.mode)

    async def signal_approved(self, signal_id: int):
        """
        Auto-approve wykonuje prostą symulację otwarcia pozycji (paper).
        W realnym ON – tu wchodziłaby egzekucja (SPOT/Futures).
        """
        cur = self.conn.cursor()
        cur.execute("SELECT symbol, side, entry, sl, tp1, tp2, tp3 FROM signals WHERE id=?", (signal_id,))
        row = self.conn.cursor().fetchone() if False else cur.fetchone()
        if not row:
            return
        symbol, side, entry, sl, tp1, tp2, tp3 = row
        qty = float(getattr(self.st, "fixed_usdt", 100)) / max(float(entry), 1e-9)
        cur.execute(
            """INSERT INTO positions(signal_id, symbol, side, qty, entry, sl, tp1, tp2, tp3, closed)
               VALUES(?,?,?,?,?,?,?,?,?,0)""",
            (signal_id, symbol, side, qty, entry, sl, tp1, tp2, tp3)
        )
        self.conn.commit()

    # ------------------------------------------------------------------ #
    #                         Quick Signal (TEST)                         #
    # ------------------------------------------------------------------ #
    async def quick_signal(
        self,
        symbol: str,
        side: str | None = None,
        bypass_gates: bool = True,
        channel_id: int | None = None
    ):
        """
        Szybkie wygenerowanie sygnału (TEST).
        - Zbiera rynek, liczy feature'y (FVG/ATR/OBI), fusion EDGE, plan (AI).
        - Jeśli bypass_gates=True -> omija bramki i wysyła sygnał.
        - Jeśli False -> przechodzi przez RiskManager; przy blokadzie wysyła [BLOCKED] z powodem.
        - channel_id pozwala wymusić wysyłkę na bieżący kanał (np. /signal_here).
        """

        # 1) Market
        ohlcv, ticker, obook = await self.collector.get_market(symbol, "15m", 200)
        last = await self._get_last_price(ohlcv, ticker)
        if last is None:
            # Wyślij INFO o braku ceny
            sig_info = Signal(
                symbol=symbol, side=(side or "LONG").upper(),
                entry=0.0, sl=0.0, tp1=0.0, tp2=0.0, tp3=0.0,
                rr=0.0, edge=0.0, confidence=0.0, success=0.0,
                reason="[BLOCKED: Brak ceny (ticker/ohlcv null)]",
                status="pending", auto_ttl=int(time.time())
            )
            if self.reporter:
                await self.reporter.send_signal(sig_info, mode=self.st.mode, channel_id=channel_id)
            return

        # 2) Feature’y
        f_long, f_short = fvg_scores(ohlcv)
        atr_val = atr(ohlcv, 14)
        obi = obi_coeff(obook)
        _rr_val, rr_c = rr_coeff(last, last - atr_val * 0.5, last + atr_val * 0.8)

        # 3) Makro
        news  = float(getattr(self, "news_score",  0.5))
        whale = float(getattr(self, "whale_score", 0.5))
        onc   = float(getattr(self, "onchain_score", 0.5))

        # 4) Fusion
        long_edge, short_edge = fuse_edge(
            f_long, f_short, rr_c, obi, news, whale, onc,
            self.st.w_fvg, self.st.w_rr, self.st.w_obi, self.st.w_news, self.st.w_whale, self.st.w_onc
        )

        # 5) Kierunek
        auto_side = "LONG" if long_edge >= short_edge else "SHORT"
        side_up = (side or auto_side).upper()
        edge = max(long_edge, short_edge)

        # 6) Plan AI
        plan = plan_openai(
            dict(f_long=f_long, f_short=f_short, rr_c=rr_c, obi=obi, news=news, whale=whale, onc=onc),
            side_up, last, atr_val
        )
        p = self._extract_plan(plan, last, atr_val)

        # 7) Risk gates (opcjonalnie)
        why = "OK"
        if not bypass_gates:
            ok, why = self.risk.can_open(symbol, p["rr"], edge, atr_pct=(atr_val / max(last, 1e-9)))
            if not ok:
                sig_block = Signal(
                    symbol=symbol, side=side_up, entry=p["entry"], sl=p["sl"],
                    tp1=p["tp1"], tp2=p["tp2"], tp3=p["tp3"],
                    rr=p["rr"], edge=edge, confidence=p["conf"], success=p["succ"],
                    reason=f"[BLOCKED: {why}] FVG:{f_long:.2f}/{f_short:.2f} OBI:{obi:.2f} ATR:{atr_val:.5f}",
                    status="pending", auto_ttl=int(time.time())
                )
                if self.reporter:
                    await self.reporter.send_signal(sig_block, mode=self.st.mode, channel_id=channel_id)
                return

        # 8) Zapis i wysyłka
        sig = Signal(
            symbol=symbol, side=side_up, entry=p["entry"], sl=p["sl"],
            tp1=p["tp1"], tp2=p["tp2"], tp3=p["tp3"],
            rr=p["rr"], edge=edge, confidence=p["conf"], success=p["succ"],
            reason=f"{why}; FVG:{f_long:.2f}/{f_short:.2f} OBI:{obi:.2f} ATR:{atr_val:.5f}",
            status="pending", auto_ttl=int(time.time())
        )

        # DB (żeby liczyły się limity /pair/day)
        cur = self.conn.cursor()
        cur.execute(
            """INSERT INTO signals(symbol, side, entry, sl, tp1, tp2, tp3, rr, edge, confidence, success, reason, status, auto_ttl, ts)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?, strftime('%s','now'))""",
            (sig.symbol, sig.side, sig.entry, sig.sl, sig.tp1, sig.tp2, sig.tp3,
             sig.rr, sig.edge, sig.confidence, sig.success, sig.reason, sig.status, sig.auto_ttl)
        )
        self.conn.commit()

        # Discord
        if self.reporter:
            await self.reporter.send_signal(sig, mode=self.st.mode, channel_id=channel_id)

    # ------------------------------------------------------------------ #
    #                    DEX: analiza po pairAddress                      #
    # ------------------------------------------------------------------ #
    async def analyze_dex_pair(self, pair_addr: str, chain: str | None = None, channel_id: int | None = None):
        """
        Analiza pary z Dexscreener (pairAddress). Zwraca Signal lub None.
        Pobiera świece 15m, liczy FVG/ATR, fusion EDGE (OBI=0.5), plan i wysyła embed.
        """
        try:
            import aiohttp
            from ..datasources.dexscreener import fetch_candles, DEX_HEADERS

            async with aiohttp.ClientSession(headers=DEX_HEADERS) as s:
                # 12h świec 15m
                candles = await fetch_candles(s, pair_addr, minutes=12*60, resolution="15")
                if not candles or len(candles) < 30:
                    return None

            # ułóż ohlcv: [ts, o,h,l,c, v]
            ohlcv = []
            for c in candles[-200:]:
                ts = int(c.get("t", 0))
                o  = float(c.get("o", 0))
                h  = float(c.get("h", 0))
                l  = float(c.get("l", 0))
                cl = float(c.get("c", 0))
                v  = float(c.get("v", 0.0))
                ohlcv.append([ts, o, h, l, cl, v])

            last = float(ohlcv[-1][4])

            # Feature’y
            f_long, f_short = fvg_scores(ohlcv)
            atr_val = atr(ohlcv, 14)
            obi = 0.5  # neutral – brak order booku na DEX

            # Makro
            news  = float(getattr(self, "news_score",  0.5))
            whale = float(getattr(self, "whale_score", 0.5))
            onc   = float(getattr(self, "onchain_score", 0.5))

            _rr_val, rr_c = rr_coeff(last, last - atr_val * 0.5, last + atr_val * 0.8)
            long_edge, short_edge = fuse_edge(
                f_long, f_short, rr_c, obi, news, whale, onc,
                self.st.w_fvg, self.st.w_rr, self.st.w_obi, self.st.w_news, self.st.w_whale, self.st.w_onc
            )
            side = "LONG" if long_edge >= short_edge else "SHORT"
            edge = max(long_edge, short_edge)

            # Plan AI
            plan = plan_openai(
                dict(f_long=f_long, f_short=f_short, rr_c=rr_c, obi=obi, news=news, whale=whale, onc=onc),
                side, last, atr_val
            )
            p = self._extract_plan(plan, last, atr_val)

            sig = Signal(
                symbol=f"DEX:{pair_addr}", side=side, entry=p["entry"], sl=p["sl"],
                tp1=p["tp1"], tp2=p["tp2"], tp3=p["tp3"],
                rr=p["rr"], edge=edge, confidence=p["conf"], success=p["succ"],
                reason=f"DEX analyze; FVG:{f_long:.2f}/{f_short:.2f} ATR:{atr_val:.5f}",
                status="pending", auto_ttl=int(time.time())
            )

            # wpis do DB
            cur = self.conn.cursor()
            cur.execute(
                """INSERT INTO signals(symbol, side, entry, sl, tp1, tp2, tp3, rr, edge, confidence, success, reason, status, auto_ttl, ts)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?, strftime('%s','now'))""",
                (sig.symbol, sig.side, sig.entry, sig.sl, sig.tp1, sig.tp2, sig.tp3,
                 sig.rr, sig.edge, sig.confidence, sig.success, sig.reason, sig.status, sig.auto_ttl)
            )
            self.conn.commit()

            if self.reporter:
                await self.reporter.send_signal(sig, mode=self.st.mode, channel_id=channel_id)
            return sig

        except Exception as e:
            print(f"[analyze_dex_pair] error: {e}")
            return None
