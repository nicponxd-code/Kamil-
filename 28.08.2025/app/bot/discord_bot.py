# app/bot/discord_bot.py
import os
import asyncio
import sqlite3
from datetime import datetime
from typing import Optional, List

import discord
from discord import app_commands

from ..config import SETTINGS
from ..models import Signal
from ..engine.command_bus import CommandBus
from ..engine.collector import Collector
from ..features.fvg import fvg_scores, atr
from ..features.rr import rr_coeff
from ..features.obi import obi_coeff
from ..engine.fusion import fuse_edge
from ..exchanges.binance import BinanceX
from ..exchanges.bitget import BitgetX

DB_PATH = SETTINGS.db_path
CHANNEL_ID = int(getattr(SETTINGS, "discord_channel_id", 0) or 0)

INTENTS = discord.Intents.default()
INTENTS.message_content = False
INTENTS.guilds = True

class AdvisorBot(discord.Client):
    def __init__(self):
        super().__init__(intents=INTENTS)
        self.tree = app_commands.CommandTree(self)
        self.conn: Optional[sqlite3.Connection] = None
        self.binance = BinanceX(SETTINGS.binance_key, SETTINGS.binance_secret)
        self.bitget  = BitgetX(SETTINGS.bitget_key, SETTINGS.bitget_secret, SETTINGS.bitget_password)
        self.bus: Optional[CommandBus] = None
        self.collector = Collector()
        self.bg_task = None
        self.last_autoscan = 0

    async def setup_hook(self):
        # Sync tree on start
        try:
            await self.tree.sync()
        except Exception as e:
            print("[discord] sync error:", e)

        # Open DB and init command bus
        self.conn = sqlite3.connect(DB_PATH)
        self.bus = CommandBus(self.conn, SETTINGS, binance=self.binance, bitget=self.bitget)

        # Kick off background tasks
        self.bg_task = asyncio.create_task(self._background_worker())

    async def on_ready(self):
        print(f"[discord] Logged in as {self.user} (id={self.user.id})")

    async def _background_worker(self):
        await self.wait_until_ready()
        channel = None
        if CHANNEL_ID:
            channel = self.get_channel(CHANNEL_ID)
        while not self.is_closed():
            try:
                # 1) process command queue (UI tiles)
                if self.bus:
                    self.bus.process_once()

                # 2) autoscan scheduler
                now = int(datetime.utcnow().timestamp())
                interval = int(getattr(SETTINGS, "autoscan_interval_min", 360))*60
                if getattr(SETTINGS, "autoscan_enabled", True) and now - self.last_autoscan >= interval:
                    await self._run_autoscan(channel)
                    self.last_autoscan = now

                # 3) broadcast fresh signals (without msg_id)
                if channel:
                    await self._post_new_signals(channel)

            except Exception as e:
                print("[discord] background error:", e)
            await asyncio.sleep(1.0)

    async def _post_new_signals(self, channel: discord.abc.Messageable):
        cur = self.conn.cursor()
        try:
            cur.execute("ALTER TABLE signals ADD COLUMN msg_id TEXT")
        except Exception:
            pass
        cur.execute("SELECT id, symbol, side, entry, sl, tp1, tp2, tp3, rr, edge, confidence, success, reason, status "
                    "FROM signals WHERE msg_id IS NULL ORDER BY ts DESC LIMIT 5")
        rows = cur.fetchall()
        for row in rows:
            (sid, symbol, side, entry, sl, tp1, tp2, tp3, rr, edge, conf, suc, reason, status) = row
            embed = discord.Embed(title=f"Signal: {symbol} {side}", description=reason or "", color=0x2b90ff)
            embed.add_field(name="Entry", value=str(entry))
            embed.add_field(name="SL", value=str(sl))
            embed.add_field(name="TP1/TP2/TP3", value=f"{tp1} / {tp2} / {tp3}", inline=False)
            embed.add_field(name="R:R", value=f"{rr:.2f}")
            embed.add_field(name="EDGE", value=f"{edge:.2f}")
            embed.add_field(name="Confidence", value=f"{conf:.2f}")
            embed.add_field(name="Mode", value=getattr(SETTINGS, "mode", "SAFE"))
            msg = await channel.send(embed=embed)
            cur2 = self.conn.cursor()
            cur2.execute("UPDATE signals SET msg_id=? WHERE id=?", (str(msg.id), sid))
            self.conn.commit()

    async def _run_autoscan(self, channel: Optional[discord.abc.Messageable] = None):
        # very light autoscan over SETTINGS.symbols
        symbols: List[str] = list(getattr(SETTINGS, "symbols", ["BTC/USDT","ETH/USDT"]))
        tf = "15m"
        print(f"[autoscan] scanning {len(symbols)} symbols")

        # gate check for HYBRID
        def hybrid_ok() -> bool:
            b = self.binance.fetch_balance_safe()
            g = self.bitget.fetch_balance_safe()
            return bool(b and g)

        if SETTINGS.mode == "HYBRID" and not hybrid_ok():
            self._log_health("autoscan", "blocked", "HYBRID requires both exchanges auth/balance")
            return

        for sym in symbols:
            try:
                ohlcv, ob, _ = await self.collector.get_market(sym, tf=tf, limit=200)
                if not ohlcv:
                    continue
                a = atr(ohlcv, period=14)
                long_edge, short_edge = fvg_scores(ohlcv)
                rr_long, rr_long_coeff = rr_coeff(ohlcv, side="LONG")
                rr_short, rr_short_coeff = rr_coeff(ohlcv, side="SHORT")
                obi = obi_coeff(ob)
                edge_long, edge_short = fuse_edge(long_edge, short_edge, rr_long_coeff, rr_short_coeff, obi, news=0.5, whale=0.5, onchain=0.5)

                # pick best side
                side = "LONG" if edge_long >= edge_short else "SHORT"
                rr = rr_long if side == "LONG" else rr_short
                edge = max(edge_long, edge_short)
                last = ohlcv[-1][4]
                entry = float(last)
                sl = float(last * (0.99 if side=="LONG" else 1.01))
                tp1 = float(last * (1.01 if side=="LONG" else 0.99))
                tp2 = float(last * (1.02 if side=="LONG" else 0.98))
                tp3 = float(last * (1.03 if side=="LONG" else 0.97))
                conf = float(min(1.0, max(0.0, edge)))

                # gate behavior
                status = "pending"
                auto_ttl = int(datetime.utcnow().timestamp())
                if SETTINGS.mode == "ON":
                    status = "approved"
                elif SETTINGS.mode == "HYBRID":
                    status = "approved" if hybrid_ok() else "pending"

                cur = self.conn.cursor()
                cur.execute("""INSERT INTO signals(symbol, side, entry, sl, tp1, tp2, tp3, rr, edge, confidence, success, reason, status, auto_ttl, ts)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?, strftime('%s','now'))""",
                            (sym, side, entry, sl, tp1, tp2, tp3, rr, edge, conf, 0.0, f"AUTO {tf}", status, auto_ttl))
                self.conn.commit()

                if channel:
                    await channel.send(f"🔎 Autoscan: {sym} {side} (EDGE {edge:.2f}, R:R {rr:.2f}) [{SETTINGS.mode}]")

            except Exception as e:
                print("[autoscan] error on", sym, "->", e)

    def _log_health(self, scope: str, status: str, note: str = ""):
        cur = self.conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS health(ts INTEGER, scope TEXT, status TEXT, note TEXT)")
        cur.execute("INSERT INTO health(ts, scope, status, note) VALUES(strftime('%s','now'), ?, ?, ?)", (scope, status, note))
        self.conn.commit()


bot = AdvisorBot()

# ----------- Slash commands -> insert into commands queue -----------

def queue_cmd(name: str, payload: str = ""):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS commands(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, name TEXT, payload TEXT)")
    conn.execute("INSERT INTO commands(ts, name, payload) VALUES(strftime('%s','now'), ?, ?)", (name, payload))
    conn.commit()
    conn.close()

@bot.tree.command(name="selftest", description="Uruchom self-test")
async def selftest(interaction: discord.Interaction):
    queue_cmd("selftest","")
    await interaction.response.send_message("🩺 Self-test: queued", ephemeral=True)

@bot.tree.command(name="status", description="Status silnika")
async def status_cmd(interaction: discord.Interaction):
    queue_cmd("status","")
    await interaction.response.send_message("📊 Status: queued", ephemeral=True)

@bot.tree.command(name="panel", description="Otwórz/odśwież panel")
async def panel_cmd(interaction: discord.Interaction):
    queue_cmd("panel","")
    await interaction.response.send_message("🖥️ Panel: queued", ephemeral=True)

@bot.tree.command(name="portfolio", description="Raport portfela")
async def portfolio_cmd(interaction: discord.Interaction):
    queue_cmd("portfolio","")
    await interaction.response.send_message("💼 Portfolio: queued", ephemeral=True)

@bot.tree.command(name="gems", description="Skan Gems")
async def gems_cmd(interaction: discord.Interaction):
    queue_cmd("gems","")
    await interaction.response.send_message("💎 Gems: queued", ephemeral=True)

@bot.tree.command(name="pause", description="Wstrzymaj wejścia")
async def pause_cmd(interaction: discord.Interaction):
    queue_cmd("pause","")
    await interaction.response.send_message("⛔ Pause: queued", ephemeral=True)

@bot.tree.command(name="resume", description="Wznów pracę")
async def resume_cmd(interaction: discord.Interaction):
    queue_cmd("resume","")
    await interaction.response.send_message("▶️ Resume: queued", ephemeral=True)

@bot.tree.command(name="snooze", description="Snooze (minuty)")
@app_commands.describe(minutes="Ile minut uśpienia")
async def snooze_cmd(interaction: discord.Interaction, minutes: int):
    minutes = max(1, min(120, int(minutes)))
    queue_cmd(f"snooze_{minutes}m","")
    await interaction.response.send_message(f"🕑 Snooze: {minutes}m queued", ephemeral=True)

@bot.tree.command(name="alerttest", description="Test alertu")
async def alerttest_cmd(interaction: discord.Interaction):
    queue_cmd("alert_test","")
    await interaction.response.send_message("🔔 Alert test: queued", ephemeral=True)

@bot.tree.command(name="rerun", description="Natychmiastowy skan analizy")
async def rerun_cmd(interaction: discord.Interaction):
    queue_cmd("rerun_scan","")
    await interaction.response.send_message("🔁 Re-run: queued", ephemeral=True)

@bot.tree.command(name="togglehybrid", description="Przełącz HYBRID")
async def togglehybrid_cmd(interaction: discord.Interaction):
    queue_cmd("toggle_hybrid","")
    await interaction.response.send_message("🔀 Toggle HYBRID: queued", ephemeral=True)

@bot.tree.command(name="scan", description="Skan rynku (autoscan)")
async def scan_cmd(interaction: discord.Interaction):
    queue_cmd("scan_market","")
    await interaction.response.send_message("🧭 Scan Market: queued", ephemeral=True)

@bot.tree.command(name="approve", description="Approve ostatniego pending")
async def approve_cmd(interaction: discord.Interaction):
    queue_cmd("approve_last","")
    await interaction.response.send_message("✅ Approve last: queued", ephemeral=True)

@bot.tree.command(name="reject", description="Reject ostatniego pending")
async def reject_cmd(interaction: discord.Interaction):
    queue_cmd("reject_last","")
    await interaction.response.send_message("❌ Reject last: queued", ephemeral=True)

@bot.tree.command(name="mode", description="Ustaw tryb")
@app_commands.choices(value=[
    app_commands.Choice(name="SAFE", value="SAFE"),
    app_commands.Choice(name="HYBRID", value="HYBRID"),
    app_commands.Choice(name="ON", value="ON"),
])
async def mode_cmd(interaction: discord.Interaction, value: app_commands.Choice[str]):
    queue_cmd("set_mode", value.value)
    await interaction.response.send_message(f"💾 Mode set -> {value.value} (queued)", ephemeral=True)

def run():
    token = getattr(SETTINGS, "discord_token", os.getenv("DISCORD_TOKEN",""))
    if not token:
        raise RuntimeError("Brak DISCORD_TOKEN w .env/SETTINGS")
    bot.run(token)
