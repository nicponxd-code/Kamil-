# app/engine/reporter.py
"""
Reporter + ControlPanel (persistent view) dla Discorda.

Funkcje:
- Panel sterowania (SAFE/HYBRID/ON, Selftest, Status, Portfolio, Signal, Gems)
- Wysyłka sygnału z pełnymi danymi (Entry/SL/TP1..3, R:R, EDGE, Confidence/Success)
- Rekomendacja (WEJŚĆ / POCZEKAJ / NIE WCHODŹ)
- Wykres (ostatnie ~100 świec 15m) z poziomami Entry/SL/TP1/TP2/TP3
- Sekcja EXECUTION (Binance/Bitget; SPOT i Futures; TP 40/40/20)
- Bardzo czytelne logi „dokąd wysyłam”

Wymaga:
- Engine z Collector: collector.get_market(symbol, tf="15m", limit=200)
- SETTINGS.fixed_usdt (sugerowany budżet nominalny dla instrukcji)
"""

from __future__ import annotations

import math
import sqlite3
from io import BytesIO
from typing import Optional

import discord
from discord.ui import View, button, Button

from ..config import SETTINGS


# ====================== Control Panel (persistent view) ======================

class ControlPanelView(View):
    """Przyciski sterujące – rejestrowane jako persistent view w on_ready()."""

    def __init__(self, bot):
        # timeout=None => persistent view (przetrwa restart bota)
        super().__init__(timeout=None)
        self.bot = bot

    @button(label="🟢 SAFE", style=discord.ButtonStyle.success, emoji="🟢", custom_id="panel_safe")
    async def safe(self, interaction: discord.Interaction, button: Button):
        SETTINGS.mode = "SAFE"
        await interaction.response.send_message("Tryb ustawiony na **SAFE**", ephemeral=True)

    @button(label="🟡 HYBRID", style=discord.ButtonStyle.primary, emoji="🟡", custom_id="panel_hybrid")
    async def hybrid(self, interaction: discord.Interaction, button: Button):
        SETTINGS.mode = "HYBRID"
        await interaction.response.send_message("Tryb ustawiony na **HYBRID**", ephemeral=True)

    @button(label="🔴 ON", style=discord.ButtonStyle.danger, emoji="🔴", custom_id="panel_on")
    async def on(self, interaction: discord.Interaction, button: Button):
        SETTINGS.mode = "ON"
        await interaction.response.send_message("Tryb ustawiony na **ON**", ephemeral=True)

    @button(label="🩺 Selftest", style=discord.ButtonStyle.secondary, emoji="🩺", custom_id="panel_selftest")
    async def selftest(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        txt = await build_full_selftest_text(self.bot)
        await interaction.followup.send(txt, ephemeral=True)

    @button(label="📊 Status", style=discord.ButtonStyle.secondary, emoji="📊", custom_id="panel_status")
    async def status(self, interaction: discord.Interaction, button: Button):
        st = SETTINGS
        txt = (
            f"Mode: **{st.mode}**\n"
            f"RR_MIN {st.rr_min} | EDGE_TH {st.edge_threshold}\n"
            f"Daily limit: {st.max_trades_per_day} | "
            f"Pair/day: {getattr(st, 'max_signals_per_pair_day', getattr(st, 'max_trades_per_pair', 3))}\n"
            f"Auto-approve ≥{st.auto_approve_conf:.0%}/{st.auto_approve_after}s | "
            f"Auto-reject <{st.auto_reject_conf:.0%}/{st.auto_reject_after}s\n"
            f"Symbols: {', '.join(st.symbols)}"
        )
        await interaction.response.send_message(txt, ephemeral=True)

    @button(label="💼 Portfolio", style=discord.ButtonStyle.secondary, emoji="💼", custom_id="panel_portfolio")
    async def portfolio(self, interaction: discord.Interaction, button: Button):
        conn: sqlite3.Connection = self.bot.engine.conn
        cur = conn.cursor()
        cur.execute("SELECT COUNT(1) FROM positions WHERE closed=0")
        open_n = cur.fetchone()[0]
        cur.execute("SELECT IFNULL(SUM(pnl),0) FROM trades WHERE ts>=strftime('%s','now','start of day')")
        day_pnl = cur.fetchone()[0]
        await interaction.response.send_message(
            f"Otwarte pozycje: {open_n}\nDzisiejszy P&L: {day_pnl:.2f}%",
            ephemeral=True
        )

    @button(label="📡 Signal", style=discord.ButtonStyle.success, emoji="📡", custom_id="panel_signal")
    async def signal(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self.bot.engine.tick_symbol(SETTINGS.symbols[0])
            await interaction.followup.send("📡 Wygenerowano sygnał (sprawdź kanał).", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Błąd: {e}", ephemeral=True)

    @button(label="💎 Gems", style=discord.ButtonStyle.secondary, emoji="💎", custom_id="panel_gems")
    async def gems(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("Użyj komendy **/gem** aby pobrać perełki.", ephemeral=True)


# ============================== Reporter =====================================

class Reporter:
    """
    - pobranie kanału docelowego (z .env lub ustawionego komendą /setchannel),
    - wysyłkę panelu,
    - wysyłkę embedów sygnałów wraz z wykresem i instrukcją egzekucji.
    """

    def __init__(self, bot, conn: sqlite3.Connection, st):
        self.bot = bot
        self.conn = conn
        self.st = st

    async def _get_channel(self, override_channel_id: Optional[int] = None) -> Optional[discord.abc.MessageableChannel]:
        """
        Zwróć obiekt kanału Discord. Gdy override_channel_id jest podany,
        użyj go zamiast zapisanej wartości.
        """
        # 1) wybór ID
        try:
            ch_id = int(override_channel_id) if override_channel_id is not None else int(self.st.discord_channel_id)
        except Exception:
            ch_id = None

        # 2) pobierz kanał
        ch = None
        if ch_id:
            ch = self.bot.get_channel(ch_id)
            if not ch:
                try:
                    ch = await self.bot.fetch_channel(ch_id)
                except Exception:
                    ch = None

        # 3) log
        if ch:
            ch_name = f"#{getattr(ch, 'name', '?')}"
            print(f"[Reporter] używam kanału {ch.id} ({ch_name})")
        else:
            print("[Reporter] brak kanału (brak ID lub nie można pobrać).")

        return ch

    async def send_control_panel(self):
        """Wyślij / odśwież panel sterowania."""
        ch = await self._get_channel()
        if not ch:
            print("[Reporter] nie mogę wysłać panelu – brak kanału.")
            return
        embed = discord.Embed(
            title="🧭 Control Panel (pinned)",
            description=("Przełącz tryb, sprawdź status, uruchom sygnał.\n"
                         "Przypnij tę wiadomość w kanale."),
            color=0x2ecc71
        )
        print(f"[Reporter] wysyłam PANEL do kanału {ch.id} (#{getattr(ch, 'name', '?')})")
        await ch.send(embed=embed, view=ControlPanelView(self.bot))

    # ---------- Rysowanie wykresu sygnału ----------
    def _render_signal_chart_png(
        self, ohlcv: list, symbol: str,
        entry: float, sl: float, tp1: float, tp2: float, tp3: float
    ) -> bytes:
        """
        Prosty wykres z linią close oraz poziomami Entry/SL/TP1/TP2/TP3.
        Używamy domyślnych kolorów matplotlib (bez narzucania stylu).
        """
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except Exception:
            return b""

        if not ohlcv:
            return b""

        closes = [float(c[4]) for c in ohlcv[-100:] if c and len(c) >= 5]
        if len(closes) < 5:
            return b""

        fig = plt.figure(figsize=(9, 3), dpi=100)
        ax = fig.add_subplot(111)
        ax.plot(range(len(closes)), closes, linewidth=1.2)
        ax.set_title(f"{symbol} • 15m • ostatnie ~100 świec")
        ax.set_xlabel("świece")
        ax.set_ylabel("cena")
        ax.grid(True, which="both", linestyle="--", linewidth=0.5)

        for y, label in [(entry, "ENTRY"), (sl, "SL"), (tp1, "TP1"), (tp2, "TP2"), (tp3, "TP3")]:
            try:
                ax.axhline(y, linestyle="--", linewidth=1.0)
                ax.text(0.01, y, label, va="bottom")
            except Exception:
                pass

        buf = BytesIO()
        fig.tight_layout()
        fig.savefig(buf, format="png")
        try:
            import matplotlib.pyplot as plt  # noqa
            plt.close(fig)
        except Exception:
            pass
        buf.seek(0)
        return buf.read()

    # ---------- Szablon instrukcji egzekucji ----------
    def _build_execution_text(
        self, symbol: str, side: str,
        entry: float, sl: float, tp1: float, tp2: float, tp3: float
    ) -> str:
        """
        Zwrot krótkiej instrukcji dla Binance/Bitget (SPOT + Futures).
        Wielkość pozycji liczona z SETTINGS.fixed_usdt.
        """
        usdt = float(getattr(self.st, "fixed_usdt", 100))
        qty = max(usdt / max(entry, 1e-9), 0.0)
        qty_r = math.floor(qty * 10_000) / 10_000  # 4 miejsca

        side_up = side.upper()
        long_short = "LONG" if side_up == "LONG" else "SHORT"
        q1 = math.floor(qty_r * 0.40 * 10_000) / 10_000
        q2 = math.floor(qty_r * 0.40 * 10_000) / 10_000
        q3 = max(qty_r - q1 - q2, 0.0)

        spot_txt = (
            f"**SPOT (szablon):**\n"
            f"- Wejście ~`{entry:.6f}` (limit/market), SL `{sl:.6f}`\n"
            f"- TP ladder 40/40/20: `{tp1:.6f}` / `{tp2:.6f}` / `{tp3:.6f}`\n"
            f"- Sugerowane qty: `{qty_r}` {symbol.split('/')[0]} (na ~{usdt:.0f} USDT)\n"
            f"- Uwaga: SHORT na SPOT zwykle niedostępny – użyj Futures."
        )

        fut_txt = (
            f"**Futures (szablon):**\n"
            f"- Otwórz **{long_short}** ~`{entry:.6f}` (dopuszczalny slippage ±0.2%)\n"
            f"- Stop Loss: `[{sl:.6f}]`\n"
            f"- Take Profit: `TP1 {tp1:.6f} x{q1}`, `TP2 {tp2:.6f} x{q2}`, `TP3 {tp3:.6f} x{q3}`\n"
            f"- Po TP1 → BE (przesuń SL na entry), po TP2 → trailing (np. 0.6% lub 0.5×ATR)."
        )

        binance = f"**Binance**\n{spot_txt}\n{fut_txt}"
        bitget = f"**Bitget**\n{spot_txt}\n{fut_txt}"
        return f"{binance}\n\n{bitget}"

    async def send_signal(self, sig, mode: str, channel_id: Optional[int] = None):
        """
        Wyślij embed sygnału (z opcjonalnym override kanału).
        `sig` musi mieć: symbol, side, entry, sl, tp1, tp2, tp3, rr, edge, confidence, success, reason.
        """
        ch = await self._get_channel(channel_id)
        if not ch:
            print("[Reporter] brak kanału do wysyłki (sprawdź /setchannel lub podaj override).")
            return

        # ----- wykres -----
        png_bytes = b""
        try:
            ohlcv, _ticker, _ob = await self.bot.engine.collector.get_market(sig.symbol, "15m", 200)
            png_bytes = self._render_signal_chart_png(
                ohlcv, sig.symbol, sig.entry, sig.sl, sig.tp1, sig.tp2, sig.tp3
            )
        except Exception as e:
            print(f"[Reporter] chart error: {e}")

        # ----- rekomendacja -----
        rec = "WEJŚĆ" if sig.confidence >= 0.80 else ("POCZEKAJ" if sig.confidence >= 0.60 else "NIE WCHODŹ")
        side = getattr(sig, "side", "LONG").upper()
        title_side = "🟩 LONG" if side == "LONG" else "🟥 SHORT"
        side_color = 0x2ecc71 if side == "LONG" else 0xe74c3c

        # ----- embed -----
        desc = (
            f"Entry: **{sig.entry:.6f}**   •   SL: **{sig.sl:.6f}**\n"
            f"TP1/TP2/TP3: **{sig.tp1:.6f} / {sig.tp2:.6f} / {sig.tp3:.6f}** (40/40/20)\n"
            f"R:R **{sig.rr:.2f}**   •   EDGE **{sig.edge:.2f}**\n"
            f"Confidence **{sig.confidence:.0%}**   •   Success **{sig.success:.0%}**\n"
            f"Rekomendacja: **{rec}**\n"
            f"Powód: {sig.reason}"
        )
        embed = discord.Embed(title=f"{title_side}  {sig.symbol}", description=desc, color=side_color)

        files = None
        if png_bytes:
            fname = f"signal_{sig.symbol.replace('/','_')}.png"
            files = [discord.File(BytesIO(png_bytes), filename=fname)]
            embed.set_image(url=f"attachment://{fname}")

        # ----- EXECUTION -----
        exec_txt = self._build_execution_text(sig.symbol, side, sig.entry, sig.sl, sig.tp1, sig.tp2, sig.tp3)
        embed.add_field(
            name="🧭 EXECUTION (Binance & Bitget)",
            value=exec_txt[:1024],
            inline=False
        )

        print(f"[Reporter] wysyłam SYGNAŁ do kanału {ch.id} (#{getattr(ch, 'name', '?')}) → {sig.symbol} [{side}]")
        await ch.send(embed=embed, files=files)


# ======================== Selftest text builder ==============================

async def build_full_selftest_text(bot) -> str:
    """
    Zwraca tekst używany przez /selftest i przycisk w panelu.
    Wartości '⚪' oznaczają neutral (brak klucza – edge zostaje 0.50).
    """
    st = SETTINGS
    lines: list[str] = ["Selftest (live):"]

    # Giełdy (public + auth na podstawie kluczy)
    try:
        lines.append(f"• Binance public: ✅ | auth: {'✅' if st.binance_key and st.binance_secret else '❌'}")
    except Exception:
        lines.append("• Binance public: ❌")

    try:
        lines.append(f"• Bitget public: ✅ | auth: {'✅' if st.bitget_key and st.bitget_secret and st.bitget_password else '❌'}")
    except Exception:
        lines.append("• Bitget public: ❌")

    # Kanał Discord
    ch_ok = bool(getattr(st, "discord_channel_id", None))
    lines.append(f"• Discord channel: {'✅' if ch_ok else '❌'}")

    # Źródła makro
    cp = '✅' if getattr(st, 'cryptopanic_key', '') else '❌'
    wh = '✅' if getattr(st, 'whale_key', '') or getattr(st, 'whale_api_key', '') else '⚪'
    on = '✅' if getattr(st, 'etherscan_api_key', '') or getattr(st, 'etherscan_key', '') else '⚪'
    oa = '✅' if getattr(st, 'openai_api_key', '') or getattr(st, 'openai_key', '') else '❌'
    gr = '✅' if getattr(st, 'groq_api_key', '') else '⚪'
    hf = '✅' if getattr(st, 'hf_api_key', '') else '⚪'

    lines.append(f"• CryptoPanic: {cp}")
    lines.append(f"• Whale Alert: {wh} (score 0.50)")
    lines.append(f"• Etherscan: {on} (score 0.50)")
    lines.append(f"• OpenAI key: {oa}")
    lines.append(f"• Groq: {gr} (score 0.25)")
    lines.append(f"• HuggingFace: {hf} (score 0.50)")

    return "\n".join(lines)
