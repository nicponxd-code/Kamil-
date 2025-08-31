# app/discord_bot/bot.py
from __future__ import annotations

import traceback
import aiohttp
import discord
from discord.ext import commands

from ..config import SETTINGS
from ..engine.runner import Engine
from ..engine.reporter import (
    Reporter,
    build_full_selftest_text,
    ControlPanelView,
)

# -------- Intents --------
INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.guilds = True


# ====== View do perełek DEX ======
class DexGemView(discord.ui.View):
    def __init__(self, display: str, conn, chain: str | None = None, pair_addr: str | None = None):
        super().__init__(timeout=120)
        self.display = display
        self.conn = conn
        self.chain = chain
        self.pair_addr = pair_addr

    @discord.ui.button(label="➕ add", style=discord.ButtonStyle.success, emoji="➕")
    async def add(self, interaction: discord.Interaction, button: discord.ui.Button):
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO gems(symbol, status, chain, pair_addr) VALUES(?,?,?,?) "
            "ON CONFLICT(symbol) DO UPDATE SET status='watch', chain=excluded.chain, pair_addr=excluded.pair_addr",
            (self.display, 'watch', self.chain, self.pair_addr)
        )
        self.conn.commit()
        await interaction.response.send_message(f"✅ Dodano {self.display} do watchlisty (DEX).", ephemeral=True)

    @discord.ui.button(label="➖ skip", style=discord.ButtonStyle.danger, emoji="➖")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        cur = self.conn.cursor()
        cur.execute("DELETE FROM gems WHERE symbol=?", (self.display,))
        self.conn.commit()
        await interaction.response.send_message(f"⛔ Pominięto {self.display}.", ephemeral=True)

    @discord.ui.button(label="🧪 sandbox", style=discord.ButtonStyle.secondary, emoji="🧪")
    async def sandbox(self, interaction: discord.Interaction, button: discord.ui.Button):
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO gems(symbol, status, chain, pair_addr) VALUES(?,?,?,?) "
            "ON CONFLICT(symbol) DO UPDATE SET status='sandbox', chain=excluded.chain, pair_addr=excluded.pair_addr",
            (self.display, 'sandbox', self.chain, self.pair_addr)
        )
        self.conn.commit()
        await interaction.response.send_message(f"🧪 {self.display} dodano do sandbox (DEX).", ephemeral=True)


# ====== Główny bot ======
class AdvisorBot(commands.Bot):
    def __init__(self):
        # WAŻNE: command_prefix wymagany przez BotBase
        super().__init__(command_prefix="!", intents=INTENTS)
        self.engine = Engine(bot=self)
        self.reporter = Reporter(self, self.engine.conn, SETTINGS)

    async def setup_hook(self):
        # start pętli silnika
        await self.engine.start(self.reporter)

    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")

        # --- Bezpieczny sync komend ---
        try:
            configured_id = int(getattr(SETTINGS, "discord_guild_id", 0) or 0)

            # 1) jeśli skonfigurowany guild istnieje w self.guilds → target sync
            if configured_id and any(g.id == configured_id for g in self.guilds):
                gobj = discord.Object(id=configured_id)
                self.tree.copy_global_to(guild=gobj)
                s = await self.tree.sync(guild=gobj)
                print(f"[sync] targeted guild {configured_id}: {len(s)} cmds")
            elif configured_id:
                print(f"[sync] configured guild {configured_id} not found in bot.guilds → skipping targeted sync")

            # 2) per-guild sync (dla wszystkich, gdzie bot faktycznie jest)
            for g in self.guilds:
                try:
                    s = await self.tree.sync(guild=g)
                    print(f"[sync] {g.name} ({g.id}) -> {len(s)} cmds")
                except Exception as ge:
                    print(f"[sync] guild {g.id} error: {ge}")

            # 3) global sync (może propagować się dłużej)
            try:
                sg = await self.tree.sync()
                print(f"[sync] global -> {len(sg)} cmds")
            except Exception as ge:
                print(f"[sync] global error: {ge}")

        except Exception as e:
            print(f"[on_ready] tree sync error: {e}\n{traceback.format_exc()}")

        # --- Persistent view (panel) ---
        try:
            self.add_view(ControlPanelView(self))
        except Exception as e:
            print(f"[on_ready] add_view error: {e}")

        # spróbuj wysłać panel (jeśli channel id jest poprawny)
        try:
            await self.reporter.send_control_panel()
        except Exception as e:
            print(f"[on_ready] send_control_panel error: {e}")


bot = AdvisorBot()


# ====== Slash commands ======
@bot.tree.command(
    name="signal_force",
    description="Wygeneruj sygnał od razu (z pominięciem bramek ryzyka) – do testów."
)
async def signal_force_cmd(
    interaction: discord.Interaction,
    symbols: str | None = None,   # np. "BTC/USDT,OP/USDT"
    side: str | None = None       # LONG/SHORT (opcjonalnie – jak puste, auto)
):
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        # lista symboli: podane albo default z SETTINGS
        if symbols:
            syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
        else:
            syms = list(SETTINGS.symbols[:3])  # kilka z rotacji

        # wykonaj quick_signal dla każdego
        for sym in syms:
            await bot.engine.quick_signal(sym, side=side, bypass_gates=True)

        await interaction.followup.send(f"🚀 Wysłano {len(syms)} sygnał(y) na kanał (FORCE).", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Błąd: {e}", ephemeral=True)


@bot.tree.command(name="ping", description="Szybki test")
async def ping_cmd(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 Pong", ephemeral=True)


@bot.tree.command(name="guilds", description="Na jakich serwerach jest bot")
async def guilds_cmd(interaction: discord.Interaction):
    lines = [f"- {g.name} ({g.id})" for g in bot.guilds]
    await interaction.response.send_message("Bot jest na:\n" + "\n".join(lines), ephemeral=True)


@bot.tree.command(name="whereami", description="Pokaż ID bieżącego kanału/serwera")
async def whereami_cmd(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    channel_id = interaction.channel_id
    await interaction.response.send_message(
        f"Guild ID: `{guild_id}`\nChannel ID: `{channel_id}`",
        ephemeral=True
    )


@bot.tree.command(name="setchannel", description="Ustaw kanał do wysyłki (tylko bieżący)")
async def setchannel_cmd(interaction: discord.Interaction):
    SETTINGS.discord_channel_id = interaction.channel_id
    await interaction.response.send_message(
        f"✅ Ustawiono DISCORD_CHANNEL_ID = `{interaction.channel_id}`.\n"
        f"Użyj `/panel`, aby wysłać panel tutaj.",
        ephemeral=True
    )


@bot.tree.command(name="mode", description="Przełącz tryb SAFE/HYBRID/ON")
async def mode_cmd(interaction: discord.Interaction, mode: str):
    m = mode.upper().strip()
    if m not in ("SAFE", "HYBRID", "ON"):
        await interaction.response.send_message("Użyj: SAFE | HYBRID | ON", ephemeral=True)
        return
    SETTINGS.mode = m
    await interaction.response.send_message(f"Tryb ustawiony na **{m}**", ephemeral=True)


@bot.tree.command(name="status", description="Status systemu i bramek")
async def status_cmd(interaction: discord.Interaction):
    st = SETTINGS
    txt = (
        f"Mode: **{st.mode}**\n"
        f"RR_MIN {st.rr_min} | EDGE_TH {st.edge_threshold}\n"
        f"Daily limit: {st.max_trades_per_day} | "
        f"Pair/day: {getattr(st,'max_signals_per_pair_day', getattr(st,'max_trades_per_pair', 3))}\n"
        f"Auto-approve ≥{st.auto_approve_conf:.0%}/{st.auto_approve_after}s | "
        f"Auto-reject <{st.auto_reject_conf:.0%}/{st.auto_reject_after}s\n"
        f"Symbols: {', '.join(st.symbols)}"
    )
    await interaction.response.send_message(txt, ephemeral=True)


@bot.tree.command(name="selftest", description="Test giełd i źródeł danych")
async def selftest_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    txt = await build_full_selftest_text(bot)
    await interaction.followup.send(txt, ephemeral=True)


@bot.tree.command(name="signal", description="Wygeneruj najlepszy sygnał teraz")
async def signal_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        await bot.engine.tick_symbol(SETTINGS.symbols[0])
        await interaction.followup.send("📡 Gotowe – sprawdź kanał sygnałów.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Błąd: {e}", ephemeral=True)


@bot.tree.command(name="portfolio", description="Podsumowanie portfela (paper)")
async def portfolio_cmd(interaction: discord.Interaction):
    conn = bot.engine.conn
    cur = conn.cursor()
    cur.execute("SELECT COUNT(1) FROM positions WHERE closed=0")
    open_n = cur.fetchone()[0]
    cur.execute("SELECT IFNULL(SUM(pnl),0) FROM trades WHERE ts>=strftime('%s','now','start of day')")
    day_pnl = cur.fetchone()[0]
    await interaction.response.send_message(
        f"💼 Otwarte pozycje: {open_n}\n📈 Dzisiejszy P&L: {day_pnl:.2f}%",
        ephemeral=True
    )


@bot.tree.command(
    name="alts",
    description="Top 5 alt-ów z potencjałem (paper) – bez BTC/ETH i bluechipów."
)
async def alts_cmd(
    interaction: discord.Interaction,
    limit: int = 5,
    min_vol: float = 3_000_000.0,   # z kropką
    max_vol: float = 60_000_000.0,  # z kropką
    rr_min: float = 0.90,
    edge_th: float = 0.55
):

    """
    Skan altów (nie majory) z umiarkowanym wolumenem.
    Generuje i wysyła do `limit` sygnałów papierowych.
    """
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        from ..engine.analyzer import Analyzer
        analyzer = Analyzer(engine=bot.engine)

        results = await analyzer.scan_alt_gems(
            limit=limit,
            min_quote_vol=min_vol,
            max_quote_vol=max_vol,
            rr_min=rr_min,
            edge_th=edge_th
        )

        if not results:
            await interaction.followup.send(
                "Brak kandydatów w tym zakresie (spróbuj zwiększyć `max_vol` lub obniżyć progi).",
                ephemeral=True
            )
            return

        lines = [f"- {r.symbol} {r.side}  EDGE {r.edge:.2f}  RR~{r.rr:.2f}  conf {r.confidence:.0%}"
                 for r in results]
        await interaction.followup.send(
            f"✅ Wysłano {len(results)} alt-sygnałów na kanał:\n" + "\n".join(lines),
            ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(f"❌ Błąd skanera altów: {e}", ephemeral=True)


@bot.tree.command(
    name="gem",
    description="Perełki z DEX (parametryzowane progi + wykres)."
)
async def gem_cmd(
    interaction: discord.Interaction,
    min_liq: int = 20000,   # USD
    min_vol: int = 50000,   # USD (24h)
    min_tx: int = 30,       # liczba transakcji (24h)
    limit: int = 3          # ile wyników zwrócić
):
    await interaction.response.defer(ephemeral=True, thinking=True)

    from ..datasources.dexscreener import (
        fetch_trending_filtered, fetch_candles, DEX_HEADERS
    )
    from ..utils.charts import render_candles_png
    from io import BytesIO

    def as_embed(g, png_bytes: bytes | None):
        e = discord.Embed(
            title=f"💎 Gem: {g['display']}",
            description=(
                f"Płynność: ${g['liquidity_usd']:,.0f}\n"
                f"24h Volume: ${g['volume_h24']:,.0f}\n"
                f"Chain: `{g['chain']}`\n"
                f"[Dexscreener]({g['url']})"
            ),
            color=0x9b59b6
        )
        files = None
        if png_bytes:
            fname = f"chart_{g['chain']}_{g['pair'][:6]}.png"
            files = [discord.File(BytesIO(png_bytes), filename=fname)]
            e.set_image(url=f"attachment://{fname}")
        return e, files

    found = []
    try:
        async with aiohttp.ClientSession(headers=DEX_HEADERS) as session:
            found = await fetch_trending_filtered(session, limit=limit, min_liq=min_liq, min_vol=min_vol, min_tx=min_tx)
    except Exception as e:
        await interaction.followup.send(f"❌ Błąd Dexscreener: {e}", ephemeral=True)
        return

    if not found:
        relax_steps = [
            (min_liq // 2, min_vol // 2, max(10, min_tx // 2)),
            (min_liq // 4, min_vol // 4, max(5,  min_tx // 3)),
        ]
        async with aiohttp.ClientSession(headers=DEX_HEADERS) as session:
            for liq_r, vol_r, tx_r in relax_steps:
                try:
                    found = await fetch_trending_filtered(session, limit=limit, min_liq=liq_r, min_vol=vol_r, min_tx=tx_r)
                except Exception:
                    found = []
                if found:
                    await interaction.followup.send(
                        f"ℹ️ Brak wyników dla progów bazowych, pokazuję przy poluzowanych progach: "
                        f"liquidity≥{liq_r}, volume≥{vol_r}, tx≥{tx_r}.",
                        ephemeral=True
                    )
                    break

    if not found:
        await interaction.followup.send("Brak perełek – spróbuj obniżyć progi lub wróć za chwilę.", ephemeral=True)
        return

    async with aiohttp.ClientSession(headers=DEX_HEADERS) as s2:
        for g in found:
            png_bytes = b""
            try:
                candles = await fetch_candles(s2, g["pair"], minutes=12*60, resolution="15")
                png_bytes = render_candles_png(candles)
            except Exception:
                png_bytes = b""

            embed, files = as_embed(g, png_bytes)
            view = DexGemView(g["display"], bot.engine.conn, chain=g["chain"], pair_addr=g["pair"])
            await interaction.followup.send(embed=embed, files=files, view=view, ephemeral=True)


@bot.tree.command(name="test_send", description="Test: wyślij prosty embed na TEN kanał (bez silnika).")
async def test_send_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        # bezpośrednio na ten kanał (nie zależymy od reporter._get_channel)
        ch = interaction.channel
        emb = discord.Embed(title="TEST ✓", description="To jest testowy embed (ten kanał).", color=0x4CAF50)
        await ch.send(embed=emb)
        await interaction.followup.send("Wysłano testowy embed na **ten** kanał.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ test_send error: {e}", ephemeral=True)


@bot.tree.command(name="signal_here", description="Wygeneruj sygnał i wyślij NA TEN kanał (omija zapisany channel ID).")
async def signal_here_cmd(
    interaction: discord.Interaction,
    symbols: str,
    side: str | None = None,
    bypass_gates: bool = True
):
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        if not hasattr(bot, "engine") or not bot.engine:
            await interaction.followup.send("❌ Brak engine (bot.engine).", ephemeral=True)
            return

        syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
        for sym in syms:
            await bot.engine.quick_signal(sym, side=side, bypass_gates=bypass_gates,
                                          channel_id=interaction.channel.id)

        await interaction.followup.send(f"🚀 Wysłano {len(syms)} sygnał(y) **na ten kanał**.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ signal_here error: {e}", ephemeral=True)


@bot.tree.command(name="panel", description="Wyślij panel sterowania (do przypięcia)")
async def panel_cmd(interaction: discord.Interaction):
    try:
        await bot.reporter.send_control_panel()
        await interaction.response.send_message("🧭 Panel wysłany na kanał – przypnij tę wiadomość.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Błąd: {e}", ephemeral=True)


@bot.tree.command(
    name="scan",
    description="Skanuje pary (podane lub auto) i generuje najlepsze sygnały."
)
async def scan_cmd(
    interaction: discord.Interaction,
    symbols: str | None = None,  # np. "BTC/USDT,ETH/USDT,OP/USDT"
    limit: int = 3
):
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        from ..engine.analyzer import Analyzer

        analyzer = Analyzer(engine=bot.engine)

        sym_list = None
        if symbols:
            sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]

        results = await analyzer.scan_and_rank(
            symbols=sym_list,
            tf="15m",
            limit=limit,
            create_signals=True,
            reporter=bot.reporter
        )

        if not results:
            await interaction.followup.send("Brak kandydatów spełniających bramki ryzyka.", ephemeral=True)
            return

        text = "\n".join([f"- {r.symbol} {r.side}  EDGE {r.edge:.2f}  RR~{r.rr:.2f}  conf {r.confidence:.0%}"
                          for r in results[:limit]])
        await interaction.followup.send(f"TOP {len(results[:limit])} sygnałów wysłanych na kanał:\n{text}", ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"❌ Błąd skanera: {e}", ephemeral=True)

@bot.tree.command(name="autoscan", description="Włącz/wyłącz i skonfiguruj autoskan altów.")
async def autoscan_cmd(
    interaction: discord.Interaction,
    enabled: bool | None = None,
    interval_min: int | None = None,
    limit: int | None = None,
    min_vol: float | None = None,
    max_vol: float | None = None,
    rr_min: float | None = None,
    edge_th: float | None = None
):
    st = SETTINGS
    if enabled is not None:
        st.autoscan_enabled = bool(enabled)
    if interval_min is not None:
        st.autoscan_interval_min = int(interval_min)
    if limit is not None:
        st.autoscan_limit = int(limit)
    if min_vol is not None:
        st.autoscan_min_vol = float(min_vol)
    if max_vol is not None:
        st.autoscan_max_vol = float(max_vol)
    if rr_min is not None:
        st.autoscan_rr_min = float(rr_min)
    if edge_th is not None:
        st.autoscan_edge_th = float(edge_th)

    await interaction.response.send_message(
        f"Autoscan: **{st.autoscan_enabled}** | co **{st.autoscan_interval_min}m** | "
        f"limit **{st.autoscan_limit}** | vol **{st.autoscan_min_vol:,.0f}–{st.autoscan_max_vol:,.0f}** | "
        f"RR≥**{st.autoscan_rr_min:.2f}** | EDGE≥**{st.autoscan_edge_th:.2f}**",
        ephemeral=True
    )


@bot.tree.command(name="autoscan_status", description="Pokaż status i progi autoskanu altów.")
async def autoscan_status_cmd(interaction: discord.Interaction):
    st = SETTINGS
    await interaction.response.send_message(
        f"Autoscan: **{st.autoscan_enabled}**\n"
        f"Interwał: **{st.autoscan_interval_min} min**\n"
        f"Limit sygnałów: **{st.autoscan_limit}**\n"
        f"Volume USD: **{st.autoscan_min_vol:,.0f} – {st.autoscan_max_vol:,.0f}**\n"
        f"Progi: RR≥**{st.autoscan_rr_min:.2f}**, EDGE≥**{st.autoscan_edge_th:.2f}**\n"
        f"Wykluczenia: {', '.join(getattr(st,'autoscan_exclude', []))}",
        ephemeral=True
    )
@bot.tree.command(
    name="analyze_pair",
    description="Analiza symbolu z CEX (np. OP/USDT): plan entry/SL/TP + wykres + rekomendacja."
)
async def analyze_pair_cmd(
    interaction: discord.Interaction,
    symbol: str,
    side: str | None = None,
    bypass_gates: bool = True
):
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        await bot.engine.quick_signal(symbol.upper(), side=side, bypass_gates=bypass_gates,
                                      channel_id=interaction.channel.id)
        await interaction.followup.send(f"📡 Analiza {symbol.upper()} wysłana na **ten** kanał.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ analyze_pair error: {e}", ephemeral=True)


@bot.tree.command(name="autoscan_now", description="Natychmiastowy jednorazowy skan altów.")
async def autoscan_now_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        from ..engine.analyzer import Analyzer
        st = SETTINGS
        analyzer = Analyzer(engine=bot.engine)

        results = await analyzer.scan_alt_gems(
            limit=int(st.autoscan_limit),
            min_quote_vol=float(st.autoscan_min_vol),
            max_quote_vol=float(st.autoscan_max_vol),
            rr_min=float(st.autoscan_rr_min),
            edge_th=float(st.autoscan_edge_th),
            exclude=set(getattr(st, "autoscan_exclude", {"BTC/USDT","ETH/USDT"}))
        )

        if not results:
            await interaction.followup.send("Brak kandydatów dla obecnych progów.", ephemeral=True)
            return

        # od razu wysyłamy jako sygnały
        sent = 0
        for r in results[:int(st.autoscan_limit)]:
            from ..models import Signal
            sig = Signal(
                symbol=r.symbol, side=r.side, entry=r.entry, sl=r.sl,
                tp1=r.tp1, tp2=r.tp2, tp3=r.tp3,
                rr=r.rr, edge=r.edge, confidence=r.confidence, success=r.success,
                reason=r.reason or "autoscan_now",
                status="pending", auto_ttl=int(__import__('time').time())
            )
            cur = bot.engine.conn.cursor()
            cur.execute(
                """INSERT INTO signals(symbol, side, entry, sl, tp1, tp2, tp3, rr, edge, confidence, success, reason, status, auto_ttl, ts)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?, strftime('%s','now'))""",
                (sig.symbol, sig.side, sig.entry, sig.sl, sig.tp1, sig.tp2, sig.tp3,
                 sig.rr, sig.edge, sig.confidence, sig.success, sig.reason, sig.status, sig.auto_ttl)
            )
            bot.engine.conn.commit()
            await bot.reporter.send_signal(sig, mode=st.mode)
            sent += 1

        await interaction.followup.send(f"✅ Wysłano {sent} sygnał(y) z autoskan_now.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ autoscan_now error: {e}", ephemeral=True)


@bot.tree.command(name="diag", description="Pokaż wartości DISCORD_* widziane przez bota")
async def diag_cmd(interaction: discord.Interaction):
    st = SETTINGS
    def _mask(s: str) -> str:
        return (s[:6] + "..." + s[-4:]) if s else "(brak)"
    msg = (
        f"DISCORD_BOT_TOKEN: {_mask(st.discord_bot_token)}\n"
        f"DISCORD_CHANNEL_ID: {getattr(st, 'discord_channel_id', None)}\n"
        f"DISCORD_GUILD_ID: {getattr(st, 'discord_guild_id', None)}"
    )
    await interaction.response.send_message(msg, ephemeral=True)


def run():
    token = getattr(SETTINGS, "discord_bot_token", None) or getattr(SETTINGS, "discord_token", None)
    if not token:
        raise RuntimeError("Brak tokenu Discord: ustaw DISCORD_BOT_TOKEN w .env")
    bot.run(token)
