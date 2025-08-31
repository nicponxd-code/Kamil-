# app/command_daemon.py
import time
import sqlite3
from .config import SETTINGS
from .exchanges.binance import BinanceX
from .exchanges.bitget import BitgetX
from .engine.command_bus import CommandBus

def main():
    conn = sqlite3.connect(SETTINGS.db_path)
    bus = CommandBus(
        conn=conn,
        settings=SETTINGS,
        reporter=None,
        binance=BinanceX(SETTINGS.binance_key, SETTINGS.binance_secret),
        bitget=BitgetX(SETTINGS.bitget_key, SETTINGS.bitget_secret, SETTINGS.bitget_password),
    )
    print("[command_daemon] started")
    while True:
        try:
            n = bus.process_once()
            if n == 0:
                time.sleep(1.0)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print("[command_daemon] error:", e)
            time.sleep(2.0)

if __name__ == "__main__":
    main()
