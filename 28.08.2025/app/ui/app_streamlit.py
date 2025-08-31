import streamlit as st
import sqlite3, pandas as pd, time, os
from datetime import datetime
from ..config import SETTINGS
from ..exchanges.binance import BinanceX
from ..exchanges.bitget import BitgetX

st.set_page_config(page_title="Advisor Bot", page_icon="🤖", layout="wide")

def get_conn():
    os.makedirs(os.path.dirname(SETTINGS.db_path), exist_ok=True)
    return sqlite3.connect(SETTINGS.db_path)

def ensure_tables():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS commands(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL,
        name TEXT NOT NULL,
        payload TEXT
    );""")
    cur.execute("""CREATE TABLE IF NOT EXISTS health(
        ts INTEGER NOT NULL,
        scope TEXT,
        status TEXT,
        note TEXT
    );""")
    cur.execute("""CREATE TABLE IF NOT EXISTS trades(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL,
        symbol TEXT,
        side TEXT,
        qty REAL,
        price REAL,
        pnl REAL
    );""")
    cur.execute("""CREATE TABLE IF NOT EXISTS equity(
        ts INTEGER NOT NULL,
        value REAL
    );""")
    conn.commit()
    conn.close()

def log_command(name: str, payload: str = ""):
    conn = get_conn()
    conn.execute("INSERT INTO commands(ts,name,payload) VALUES (?,?,?)", (int(time.time()), name, payload))
    conn.commit()
    conn.close()

def test_hybrid_connectivity():
    """Check both exchanges for auth + balance access."""
    b = BinanceX(SETTINGS.binance_key, SETTINGS.binance_secret)
    g = BitgetX(SETTINGS.bitget_key, SETTINGS.bitget_secret, SETTINGS.bitget_password)
    return {
        "binance": {
            "auth": b.has_auth(),
            "balance_ok": b.fetch_balance_safe()
        },
        "bitget": {
            "auth": g.has_auth(),
            "balance_ok": g.fetch_balance_safe()
        }
    }

ensure_tables()

st.title("🤖 Advisor Bot – Control & Monitor")

tab1, tabX, tab2, tab3, tab4 = st.tabs(["Control", "Commands", "Signals", "Trades + Equity", "Health"])

with tab1:
    st.subheader("Control")
    c1, c2, c3 = st.columns([2,2,2])
    with c1:
        mode = st.selectbox("Mode", ["SAFE","HYBRID","ON"], index=["SAFE","HYBRID","ON"].index(SETTINGS.mode))
        if st.button("💾 Zapisz tryb"):
            # In a full app, this would persist and inform the runner. Here we log the intent.
            log_command("set_mode", mode)
            st.success(f"Tryb zapisany: {mode}")
    with c2:
        st.caption("Hybrid – status giełd")
        status = test_hybrid_connectivity()
        b = status["binance"]; g = status["bitget"]
        st.markdown(f"""**Binance**: {'✅' if b['auth'] else '⚠️'} auth | {'✅' if b['balance_ok'] else '❌'} balance""")
        st.markdown(f"""**Bitget**: {'✅' if g['auth'] else '⚠️'} auth | {'✅' if g['balance_ok'] else '❌'} balance""")
        if mode == "HYBRID":
            if b["balance_ok"] and g["balance_ok"]:
                st.success("HYBRID gotowy: obie giełdy OK ✔️")
            else:
                st.warning("HYBRID niekompletny – sprawdź API/hasła na obu giełdach.")
    with c3:
        st.caption("Skróty")
        if st.button("🩺 Self‑test"):
            log_command("selftest")
            st.info("Self-test uruchomiony.")
        if st.button("🔁 Re‑run scan"):
            log_command("rerun_scan")
            st.info("Ponowny skan zaplanowany.")

with tabX:
    st.subheader("Interactive Commands")
    st.caption("Kliknij kafelek, aby wywołać akcję. Każdy kafelek ma opis w rozwijanym panelu.")
    # Define commands
    commands = [
        {"label":"🩺 Self‑test","name":"selftest","desc":"Uruchamia pełny test zdrowia: API, DB, limity, klucze, połączenia."},
        {"label":"📊 Status","name":"status","desc":"Zwraca aktualny stan silnika, ostatnie sygnały, tryb i blokady."},
        {"label":"🖥️ Panel","name":"panel","desc":"Otwiera/odświeża panel UI oraz sekcję monitoringu."},
        {"label":"💼 Portfolio","name":"portfolio","desc":"Pobiera i raportuje stan portfela (środki, alokacja, P/L)."},
        {"label":"💎 Gems","name":"gems","desc":"Wyszukuje altcoiny z podwyższonym EDGE i filtrami wolumenu."},
        {"label":"⛔ Pause","name":"pause","desc":"Wstrzymuje nowe wejścia; wychodzi tylko zgodnie z planem ryzyka."},
        {"label":"▶️ Resume","name":"resume","desc":"Wznawia pełną pracę zgodnie z aktualnym trybem."},
        {"label":"🕑 Snooze 10m","name":"snooze_10m","desc":"Usypia alerty i wejścia na 10 minut."},
        {"label":"🕑 Snooze 30m","name":"snooze_30m","desc":"Usypia alerty i wejścia na 30 minut."},
        {"label":"🕑 Snooze 60m","name":"snooze_60m","desc":"Usypia alerty i wejścia na 60 minut."},
        {"label":"🔔 Alert test","name":"alert_test","desc":"Wysyła przykładowy alert do wszystkich kanałów powiadomień."},
        {"label":"🔁 Re‑run analysis","name":"rerun_scan","desc":"Natychmiastowy skan symboli i rekalkulacja EDGE/R:R/OBI."},
        {"label":"🔀 Toggle HYBRID","name":"toggle_hybrid","desc":"Przełącza tryb HYBRID ON/OFF z walidacją kluczy."},
        {"label":"🧭 Scan Market","name":"scan_market","desc":"Skanuje rynek wg parametrów autoscan_* z configu."},
        {"label":"✅ Approve last","name":"approve_last","desc":"Zatwierdza ostatni sygnał oczekujący (manual gate)."},
        {"label":"❌ Reject last","name":"reject_last","desc":"Odrzuca ostatni sygnał oczekujący (manual gate)."},
    ]
    # Render as tiles 4 per row
    cols_per_row = 4
    for i in range(0, len(commands), cols_per_row):
        row = commands[i:i+cols_per_row]
        cols = st.columns(cols_per_row)
        for col, cmd in zip(cols, row):
            with col:
                if st.button(cmd["label"], use_container_width=True):
                    log_command(cmd["name"])
                    st.success(f"Wysłano: {cmd['label']}")
                with st.expander("Opis"):
                    st.write(cmd["desc"])

with tab2:
    st.subheader("Signals")
    conn = get_conn()
    # expect a signals table if the engine runs; otherwise show empty frame
    try:
        df = pd.read_sql_query("SELECT * FROM signals ORDER BY ts DESC LIMIT 50", conn)
    except Exception:
        df = pd.DataFrame()
    st.dataframe(df, use_container_width=True)
    conn.close()

with tab3:
    st.subheader("Trades + Equity")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Trades (today)**")
        conn = get_conn()
        df = pd.read_sql_query("SELECT * FROM trades WHERE ts>=strftime('%s','now','start of day') ORDER BY ts DESC", conn)
        st.dataframe(df, use_container_width=True)
    with c2:
        st.markdown("**Equity (all)**")
        df = pd.read_sql_query("SELECT * FROM equity ORDER BY ts ASC", conn)
        if not df.empty:
            df_plot = df.copy()
            # If ts is epoch seconds, convert to datetime index for nicer chart
            try:
                df_plot["dt"] = pd.to_datetime(df_plot["ts"], unit="s")
                df_plot = df_plot.set_index("dt")
                st.line_chart(df_plot["value"])
            except Exception:
                st.line_chart(df.set_index('ts')['value'])
        else:
            st.info("Brak danych equity.")

with tab4:
    st.subheader("Health")
    conn = get_conn()
    df = pd.read_sql_query("SELECT * FROM health ORDER BY ts DESC LIMIT 20", conn)
    st.dataframe(df, use_container_width=True)
