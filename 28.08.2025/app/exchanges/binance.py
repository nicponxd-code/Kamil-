import ccxt
from typing import Dict, Any, List

class BinanceX:
    def __init__(self, api_key: str = '', api_secret: str = ''):
        self.x = ccxt.binance({
            'apiKey': api_key or None,
            'secret': api_secret or None,
            'enableRateLimit': True,
        })

    def fetch_ohlcv(self, symbol: str, timeframe: str='15m', limit: int=200):
        return self.x.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

    def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        return self.x.fetch_ticker(symbol)

    def fetch_order_book(self, symbol: str, limit: int=50):
        return self.x.fetch_order_book(symbol, limit=limit)

    def has_auth(self) -> bool:
        return bool(self.x.apiKey and self.x.secret)

    def fetch_balance_safe(self) -> bool:
        if not self.has_auth():
            return False
        try:
            _ = self.x.fetch_balance()
            return True
        except Exception:
            return False
