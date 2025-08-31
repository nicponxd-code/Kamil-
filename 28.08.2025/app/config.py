import os
from dotenv import load_dotenv
from dataclasses import dataclass, field
from typing import List

load_dotenv()

def _get_bool(name: str, default: bool) -> bool:
    v = os.getenv(name, str(default))
    return str(v).strip().lower() in ("1","true","yes","y","on")

def _get_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except Exception:
        return float(default)

def _get_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except Exception:
        return int(default)
        

def _to_int(x, default=None):
    try:
        return int(str(x).strip())
    except Exception:
        return default

@dataclass
class Settings:
    # ...
    discord_bot_token: str = os.getenv("DISCORD_BOT_TOKEN") or os.getenv("DISCORD_TOKEN") or ""
    discord_channel_id: int = _to_int(os.getenv("DISCORD_CHANNEL_ID") or os.getenv("CHANNEL_ID"))
    discord_guild_id: int = _to_int(os.getenv("DISCORD_GUILD_ID") or os.getenv("GUILD_ID"))
    # ...


@dataclass
class Settings:
    mode: str = os.getenv("MODE", "HYBRID").upper()
    tick_seconds: int = _get_int("TICK_SECONDS", 60)
    selftest_minutes: int = _get_int("SELFTEST_MINUTES", 10)
    trading_hours: str = os.getenv("TRADING_HOURS", "07:00-23:00")
    fixed_usdt: float = _get_float("FIXED_USDT_PER_TRADE", 50)
    rr_min: float = _get_float("RR_MIN", 1.2)
    edge_threshold: float = _get_float("EDGE_THRESHOLD", 0.62)
    max_trades_per_day: int = _get_int("MAX_TRADES_PER_DAY", 4)
    max_signals_per_pair_day: int = _get_int("MAX_SIGNALS_PER_PAIR_PER_DAY", 3)
    circuit_breaker_pct: float = _get_float("DAILY_CIRCUIT_BREAKER_PCT", -3.5)
    vol_throttle: bool = _get_bool("VOL_THROTTLE_HIGH_VOLA", True)
    w_fvg: float = _get_float("W_FVG", 0.35)
    w_rr: float = _get_float("W_RR", 0.25)
    w_obi: float = _get_float("W_OBI", 0.15)
    w_news: float = _get_float("W_NEWS", 0.10)
    w_whale: float = _get_float("W_WHALE", 0.10)
    w_onc: float = _get_float("W_ONCHAIN", 0.05)
    auto_approve_conf: float = _get_float("AUTO_APPROVE_CONF", 0.8)
    auto_approve_after: int = _get_int("AUTO_APPROVE_AFTER_SEC", 120)
    auto_reject_conf: float = _get_float("AUTO_REJECT_CONF", 0.6)
    auto_reject_after: int = _get_int("AUTO_REJECT_AFTER_SEC", 600)
    binance_key: str = os.getenv("BINANCE_API_KEY","")
    binance_secret: str = os.getenv("BINANCE_API_SECRET","")
    bitget_key: str = os.getenv("BITGET_API_KEY","")
    bitget_secret: str = os.getenv("BITGET_API_SECRET","")
    bitget_password: str = os.getenv("BITGET_PASSWORD","")
    cryptopanic_key: str = os.getenv("CRYPTOPANIC_KEY","")
    whale_key: str = os.getenv("WHALE_API_KEY","")
    etherscan_key: str = os.getenv("ETHERSCAN_API_KEY","")
    discord_token: str = os.getenv("DISCORD_BOT_TOKEN","")
    discord_guild_id: int = int(os.getenv("DISCORD_GUILD_ID","0") or 0)
    discord_channel_id: int = int(os.getenv("DISCORD_CHANNEL_ID","0") or 0)
    streamlit_port: int = _get_int("STREAMLIT_PORT", 8501)
    openai_key: str = os.getenv("OPENAI_API_KEY","")
    openai_model: str = os.getenv("OPENAI_MODEL","gpt-4o-mini")
    symbols: List[str] = field(default_factory=lambda: [s.strip() for s in os.getenv("SYMBOLS","BTC/USDT,ETH/USDT").split(",")])
    db_path: str = os.getenv("DB_PATH","./data/bot.db")
    groq_key: str = os.getenv("GROQ_API_KEY","")
    hf_key: str = os.getenv("HF_API_KEY","")
    gems_max: int = int(os.getenv("GEMS_MAX", "5"))
    


SETTINGS = Settings()
 
 
 
    # --- AUTOSKAN ALTÓW (DEX/CEX alts) ---
self.autoscan_enabled = True          # czy skan ma działać w tle
self.autoscan_interval_min = 60       # co ile minut
self.autoscan_limit = 5               # ile sygnałów wysłać na skan
self.autoscan_min_vol = 3_000_000.0   # min 24h quote volume (USD)
self.autoscan_max_vol = 60_000_000.0  # max 24h quote volume (USD)
self.autoscan_rr_min = 0.90           # minimalne RR
self.autoscan_edge_th = 0.55          # minimalny EDGE
self.autoscan_exclude = {"BTC/USDT", "ETH/USDT"}  # możesz dopisać swoje wykluczenia
    # --- AUTOSKAN: auto-relax progów, jeśli pusto ---
self.autoscan_enabled = True
self.autoscan_interval_min = 360      # 6h domyślnie
self.autoscan_limit = 5
self.autoscan_min_vol = 3_000_000.0
self.autoscan_max_vol = 60_000_000.0
self.autoscan_rr_min = 0.90
self.autoscan_edge_th = 0.55
self.autoscan_exclude = {"BTC/USDT", "ETH/USDT"}
self.autoscan_relax_steps = 10        # maks. liczba kroków luzowania
self.autoscan_relax_factor = 0.95     # co krok ×0.95 na progach wolumenu
self.autoscan_relax_edge = 0.01       # -0.01 EDGE na krok (do min 0.5)
self.autoscan_relax_rr = 0.02         # -0.02 RR na krok (do min 0.80)

# --- Skąd brać uniwersum par: 'global' | 'portfolio' | 'both' ---
self.universe_mode = "both"           # domyślnie: najpierw portfolio, potem reszta