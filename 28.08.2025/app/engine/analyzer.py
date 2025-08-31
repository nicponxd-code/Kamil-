# app/engine/analyzer.py
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

from ..config import SETTINGS
from ..features.fvg import fvg_scores, atr
from ..features.rr import rr_coeff
from ..features.obi import obi_coeff
from ..engine.fusion import fuse_edge
from ..engine.planner_ai import plan_openai
from ..models import Signal


@dataclass
class AnalysisRow:
    symbol: str
    side: str  # "LONG" / "SHORT"
    edge_long: float
    edge_short: float
    edge: float
    rr_seed: float
    obi: float
    atr: float
    entry: float  # last price used for planning reference
    reason: str


# Zbiór majorów, które wycinamy przy wyszukiwaniu altów
MAJORS = {
    "BTC", "ETH", "BNB", "SOL", "USDT", "USDC", "XRP", "ADA", "DOGE", "TRX", "TON", "DOT",
    "MATIC", "LTC", "BCH", "LINK", "AVAX", "ATOM", "FIL", "APT", "OP", "ARB", "NEAR", "ETC"
}


class Analyzer:
    """
    Skanuje pary, liczy cechy i zwraca posortowane rekomendacje.
    Potrafi też tworzyć pełne `Signal` i przekazać je dalej (Reporter/DB).
    """

    def __init__(self, engine):
        self.engine = engine
        self.st = SETTINGS
        self.conn = engine.conn
        self.risk = engine.risk

    # --------------------------------------------------------------------- #
    #                         ODKRYWANIE SYMBOLI                             #
    # --------------------------------------------------------------------- #
    async def autodiscover_symbols(self, max_symbols: int = 20) -> List[str]:
        """
        Pobiera top pary USDT wg wolumenu z Binance + Bitget.
        Zwraca unikatowe symbole (np. "BTC/USDT").
        """
        got: List[Tuple[str, float]] = []

        async def from_ex(ex):
            try:
                tickers = await ex.fetch_tickers()  # dict symbol -> ticker
                for sym, t in tickers.items():
                    if "/USDT" not in sym:
                        continue
                    vol = float(t.get("quoteVolume", 0) or 0.0)
                    got.append((sym, vol))
            except Exception:
                pass

        await asyncio.gather(from_ex(self.engine.binance), from_ex(self.engine.bitget))

        got.sort(key=lambda x: x[1], reverse=True)
        uniq: List[str] = []
        seen = set()
        for sym, _ in got:
            if sym in seen:
                continue
            seen.add(sym)
            uniq.append(sym)
            if len(uniq) >= max_symbols:
                break

        if not uniq:
            # fallback – weź z SETTINGS.symbols
            uniq = list(self.st.symbols)[:max_symbols]
        return uniq

    async def get_portfolio_symbols(self) -> List[str]:
        """
        Zwraca listę symboli w formacie 'COIN/USDT' na podstawie balansów SPOT z Binance/Bitget.
        Tylko tickery z sensownym saldem > 0 i mapowalne do pary /USDT.
        """
        syms = set()

        # Binance
        try:
            bals = await self.engine.binance.fetch_balances()
            for coin, amt in bals.items():
                try:
                    if float(amt) > 0 and coin not in ("USDT", "BUSD", "USD"):
                        syms.add(f"{coin}/USDT")
                except Exception:
                    continue
        except Exception:
            pass

        # Bitget
        try:
            bals = await self.engine.bitget.fetch_balances()
            for coin, amt in bals.items():
                try:
                    if float(amt) > 0 and coin not in ("USDT", "USD"):
                        syms.add(f"{coin}/USDT")
                except Exception:
                    continue
        except Exception:
            pass

        return sorted(syms)

    async def autodiscover_alt_symbols(
        self,
        max_symbols: int = 40,
        min_quote_vol: float = 3_000_000,   # 3M USDT/24h - nie trup
        max_quote_vol: float = 60_000_000,  # 60M USDT/24h - nie mega bluechip
    ) -> List[str]:
        """
        Zbiera tickery USDT z Binance+Bitget, wyrzuca majory, zostawia alt-y z umiarkowanym wolumenem.
        Zwraca do max_symbols symboli w formacie 'XXX/USDT'.
        """
        pool: List[Tuple[str, float]] = []

        async def from_ex(ex):
            try:
                t = await ex.fetch_tickers()
                for sym, tk in t.items():
                    if "/USDT" not in sym:
                        continue
                    base = sym.split("/")[0].upper()
                    if base in MAJORS:
                        continue
                    qv = float(tk.get("quoteVolume", 0) or 0.0)
                    if qv < min_quote_vol or qv > max_quote_vol:
                        continue
                    pool.append((sym, qv))
            except Exception:
                pass

        await asyncio.gather(from_ex(self.engine.binance), from_ex(self.engine.bitget))

        pool.sort(key=lambda x: x[1], reverse=True)
        uniq: List[str] = []
        seen = set()
        for sym, _ in pool:
            if sym in seen:
                continue
            seen.add(sym)
            uniq.append(sym)
            if len(uniq) >= max_symbols:
                break

        if not uniq:
            src = [s for s in getattr(self.st, "symbols", []) if "/USDT" in s]
            uniq = [s for s in src if s.split("/")[0].upper() not in MAJORS][:max_symbols]
        return uniq

    # --------------------------------------------------------------------- #
    #                           SKAN „GEMS / ALTS”                          #
    # --------------------------------------------------------------------- #
    async def scan_alt_gems(
        self,
        limit: int = 5,
        min_quote_vol: float = 3_000_000,
        max_quote_vol: float = 60_000_000,
        rr_min: float = 0.90,      # lekkie rozluźnienie
        edge_th: float = 0.55,
    ) -> List[Signal]:
        """
        Dobiera alt-y, skanuje, filtruje przez bramki i generuje do `limit` sygnałów (paper),
        wysyłając je przez reportera (jeśli podpięty).
        """
        syms = await self.autodiscover_alt_symbols(
            max_symbols=limit * 6,
            min_quote_vol=min_quote_vol,
            max_quote_vol=max_quote_vol,
        )

        results = await self.scan_and_rank(
            symbols=syms,
            tf="15m",
            limit=limit,
            create_signals=True,
            reporter=self.engine.reporter,
            rr_min_override=rr_min,
            edge_th_override=edge_th,
        )
        return results

    # --------------------------------------------------------------------- #
    #                         ANALIZA JEDNEJ PARY                           #
    # --------------------------------------------------------------------- #
    async def analyze_symbol(self, symbol: str, tf: str = "15m") -> Optional[AnalysisRow]:
        """
        Wczytuje rynek (OHLCV/ticker/OB), liczy feature'y i oddaje wiersz analizy.
        """
        try:
            ohlcv, ticker, obook = await self.engine.collector.get_market(symbol, tf, 200)
            last = float((ticker or {}).get("last") or (ticker or {}).get("close") or (ohlcv[-1][4]))
            atr_val = atr(ohlcv, 14)
            f_long, f_short = fvg_scores(ohlcv)
            obi = obi_coeff(obook)

            # Multi-TF bonus/penalty: zgodność 15m vs 1h
            try:
                ohlcv_h, _, _ = await self.engine.collector.get_market(symbol, "1h", 200)
                fL_h, fS_h = fvg_scores(ohlcv_h)
                mtf_bonus = 0.05 if ((f_long > f_short and fL_h > fS_h) or (f_short > f_long and fS_h > fL_h)) else -0.05
            except Exception:
                mtf_bonus = 0.0

            # makro (selftest może nie mieć kluczy – neutral 0.5)
            news = getattr(self.engine, "news_score", 0.5) if hasattr(self.engine, "news_score") else 0.5
            whale = getattr(self.engine, "whale_score", 0.5) if hasattr(self.engine, "whale_score") else 0.5
            onc = getattr(self.engine, "onchain_score", 0.5) if hasattr(self.engine, "onchain_score") else 0.5

            # RR – seed do skali
            _rr_val, rr_c = rr_coeff(last, last - atr_val * 0.5, last + atr_val * 0.8)

            # EDGE
            long_edge, short_edge = fuse_edge(
                f_long, f_short, rr_c, obi, news, whale, onc,
                self.st.w_fvg, self.st.w_rr, self.st.w_obi, self.st.w_news, self.st.w_whale, self.st.w_onc
            )
            long_edge += mtf_bonus
            short_edge += mtf_bonus

            side = "LONG" if long_edge >= short_edge else "SHORT"
            edge = max(long_edge, short_edge)

            return AnalysisRow(
                symbol=symbol,
                side=side,
                edge_long=long_edge,
                edge_short=short_edge,
                edge=edge,
                rr_seed=rr_c,
                obi=obi,
                atr=atr_val,
                entry=last,
                reason=f"FVG L/S={f_long:.2f}/{f_short:.2f}; OBI={obi:.2f}; MTF={mtf_bonus:+.2f}"
            )
        except Exception:
            return None

    # --------------------------------------------------------------------- #
    #                        SKAN ZBIORCZY + RANKING                        #
    # --------------------------------------------------------------------- #
    async def scan_and_rank(
        self,
        symbols: Optional[Iterable[str]] = None,
        tf: str = "15m",
        limit: int = 10,
        create_signals: bool = False,
        reporter=None,
        rr_min_override: Optional[float] = None,
        edge_th_override: Optional[float] = None,
        relax_steps: Optional[List[Tuple[float, float]]] = None,  # [(RR_MIN, EDGE_TH), ...]
    ) -> List[Signal]:
        """
        Skanuje listę par (lub auto-odkrywa), sortuje po EDGE i filtruje przez Risk/Gating.
        Jeśli create_signals=True – tworzy sygnały (pending) i opcjonalnie wysyła przez reporter.
        Zwraca listę `Signal` (gdy create_signals=True) lub kandydatów (gdy tylko ranking).

        Parametr `relax_steps` pozwala przekazać listę par (rr_min, edge_th),
        po których będziemy schodzić, jeśli bazowe progi nie dadzą żadnego wyniku.
        """
        # 1) przygotuj listę symboli
        if not symbols:
            symbols = await self.autodiscover_symbols(max_symbols=max(limit * 3, 20))
        symbols = list(symbols)

        # 2) policz analizy
        tasks = [self.analyze_symbol(sym, tf=tf) for sym in symbols]
        rows = [r for r in await asyncio.gather(*tasks) if r is not None]

        # 3) ranking
        rows.sort(key=lambda r: r.edge, reverse=True)
        base_rr = rr_min_override if rr_min_override is not None else float(self.st.rr_min)
        base_edge = edge_th_override if edge_th_override is not None else float(self.st.edge_threshold)

        def _try_pick(rows_in: List[AnalysisRow], rr_min: float, edge_th: float) -> List[Signal]:
            out: List[Signal] = []
            for row in rows_in:
                ok, why = self.risk.can_open(
                    row.symbol,
                    row.rr_seed,
                    row.edge,
                    rr_min=rr_min,
                    edge_th=edge_th
                )
                if not ok:
                    continue

                # AI plan – konkretny plan transakcji
                ctx = dict(
                    f_long=row.edge_long,
                    f_short=row.edge_short,
                    rr_c=row.rr_seed,
                    obi=row.obi,
                    news=0.5, whale=0.5, onc=0.5
                )
                plan = plan_openai(ctx, row.side, row.entry, row.atr)

                sig = Signal(
                    symbol=row.symbol,
                    side=row.side,
                    entry=plan["entry"],
                    sl=plan["sl"],
                    tp1=plan["tp1"], tp2=plan["tp2"], tp3=plan["tp3"],
                    rr=plan["rr"], edge=row.edge,
                    confidence=plan["conf"], success=plan["success"],
                    reason=f"{why}; {row.reason}",
                    status="pending",
                    auto_ttl=__import__("time").time().__int__()
                )
                out.append(sig)
                if len(out) >= limit:
                    break
            return out

        # 4) próba z bazowymi progami
        results: List[Signal] = _try_pick(rows, base_rr, base_edge)

        # 5) jeśli pusto, a podano relax_steps – schodź po progach
        if not results and relax_steps:
            for rr_min, edge_th in relax_steps:
                results = _try_pick(rows, rr_min, edge_th)
                if results:
                    break

        # 6) jeżeli tworzymy sygnały – zapisz/wyślij
        if create_signals and results:
            cur = self.conn.cursor()
            for sig in results[:limit]:
                cur.execute(
                    """INSERT INTO signals(symbol, side, entry, sl, tp1, tp2, tp3, rr, edge, confidence, success, reason, status, auto_ttl)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (sig.symbol, sig.side, sig.entry, sig.sl, sig.tp1, sig.tp2, sig.tp3,
                     sig.rr, sig.edge, sig.confidence, sig.success, sig.reason, sig.status, sig.auto_ttl)
                )
                self.conn.commit()
                if reporter:
                    await reporter.send_signal(sig, mode=self.st.mode)

        return results[:limit]
