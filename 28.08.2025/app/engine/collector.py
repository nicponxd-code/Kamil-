# app/engine/collector.py
import asyncio
from typing import Tuple, List, Any, Optional

# Uwaga: Twoje klasy BinanceX/BitgetX są synchronizowane (ccxt)
# – więc zawołamy je w wątku (asyncio.to_thread).
# DEX OHLCV pobierzemy przez dexscreener (async).
from ..datasources.dexscreener import fetch_candles


class Collector:
    """
    Zbiera dane rynkowe z CEX (Binance/Bitget) i DEX (Dexscreener).

    get_market(symbol, tf, limit) zwraca:
      - ohlcv: List[List[ts, o, h, l, c, v]] (ts w sekundach)
      - ticker: dict(last/close)
      - orderbook: dict(bids, asks) lub {} dla DEX
    """

    def __init__(self, binance, bitget):
        self.binance = binance
        self.bitget = bitget

    # ----------------------- helpers -----------------------

    def _is_dex_symbol(self, symbol: str) -> bool:
        # Format: DEX:<chain>:<pairAddress>
        return symbol.upper().startswith("DEX:")

    def has_symbol(self, symbol: str) -> bool:
        """
        True jeśli symbol jest obsługiwalny:
        - DEX:<chain>:<pairAddr> -> True (OHLCV z Dexscreener)
        - CEX: sprawdź ticker na Binance/Bitget
        """
        if self._is_dex_symbol(symbol):
            return True
        # CEX ping
        try:
            self.binance.fetch_ticker(symbol)
            return True
        except Exception:
            try:
                self.bitget.fetch_ticker(symbol)
                return True
            except Exception:
                return False

    # ----------------------- DEX path -----------------------

    async def _get_market_dex(self, symbol: str, tf: str, limit: int):
        """
        DEX:<chain>:<pairAddress> -> pobiera świece z Dexscreener.
        """
        
        
        try:
            _, chain, pair_addr = symbol.split(":", 2)
        except ValueError:
            return [], None, None

        bars = await fetch_candles(chain, pair_addr, lookback_hours=48, resolution_sec=900)  # 15m
        if not bars:
            return [], None, None

        # bars: [{"t":ts,"o":..,"h":..,"l":..,"c":..,"v":..},...]
        ohlcv = [[int(b["t"]), float(b["o"]), float(b["h"]), float(b["l"]), float(b["c"]), float(b.get("v", 0.0))] for b in bars]
        ohlcv = ohlcv[-limit:]
        last = ohlcv[-1][4]
        ticker = {"last": last, "close": last}
        # DEX: bez orderbooka na tym etapie (OBI ustawiamy neutralnie 0.5 w runnerze)
        orderbook = {"bids": [], "asks": []}
        return ohlcv, ticker, orderbook

    # ----------------------- CEX path -----------------------

    async def _cex_fetch_ohlcv(self, ex, symbol: str, tf: str, limit: int):
        return await asyncio.to_thread(ex.fetch_ohlcv, symbol, timeframe=tf, limit=limit)

    async def _cex_fetch_ticker(self, ex, symbol: str):
        return await asyncio.to_thread(ex.fetch_ticker, symbol)

    async def _cex_fetch_order_book(self, ex, symbol: str, limit: int = 100):
        return await asyncio.to_thread(ex.fetch_order_book, symbol, limit)

    async def _get_market_cex(self, symbol: str, tf: str, limit: int):
        """
        Najpierw próbujemy Binance, potem Bitget (fallback).
        """
        # --- Binance ---
        try:
            ohlcv = await self._cex_fetch_ohlcv(self.binance, symbol, tf, limit)
            ticker = await self._cex_fetch_ticker(self.binance, symbol)
            obook = await self._cex_fetch_order_book(self.binance, symbol, 100)
            if ohlcv and ticker:
                return ohlcv, ticker, obook
        except Exception:
            pass

        # --- Bitget fallback ---
        try:
            ohlcv = await self._cex_fetch_ohlcv(self.bitget, symbol, tf, limit)
            ticker = await self._cex_fetch_ticker(self.bitget, symbol)
            obook = await self._cex_fetch_order_book(self.bitget, symbol, 100)
            if ohlcv and ticker:
                return ohlcv, ticker, obook
        except Exception:
            pass

        return [], None, None

    # ----------------------- public API -----------------------

    async def get_market(self, symbol: str, tf: str = "15m", limit: int = 200):
        """
        Wspólny interfejs dla Runnera.
        """
        if self._is_dex_symbol(symbol):
            return await self._get_market_dex(symbol, tf, limit)
        return await self._get_market_cex(symbol, tf, limit)
