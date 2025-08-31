from __future__ import annotations
import json, asyncio
from pathlib import Path
from typing import Optional
import discord
from discord.ext import commands

# (Opcjonalnie) korzystamy z Twojego routingu kanału, jeśli masz ten cog:
try:
    from channel_config_cog import get_channel_id as _get_channel_id
except Exception:
    _get_channel_id = None  # fallback: pierwszy kanał tekstowy

MODE_DB = Path("mode_state.json")          # zgodne z Twoim projektem
AUTO_DB = Path("auto_panel_state.json")    # tylko do włącz/wyłącz auto-panel

DEFAULT_MODE = {"mode": "off"}

def _load_mode():
    if not MODE_DB.exists():
        MODE_DB.write_text(json.dumps(DEFAULT_MODE, indent=2), encoding="utf-8")
    try:
        return json.loads(MODE_DB.read_text(encoding="utf-8"))
    except Exception:
        return DEFAULT_MODE.copy()

def _save_mode(mode: str):
    MODE_DB.write_text(json.dumps({"mode": mode}, indent=2), encoding="utf-8")

def _load_auto():
    if AUTO_DB.exists():
        try:
            return json.loads(AUTO_DB.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"enabled": True}

def _save_auto(enabled: bool):
    AUTO_DB.write_text(json.dumps({"enabled": bool(enabled)}, indent=2), encoding="utf-8")

def _pick_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    # 1) preferowany kanał z channel_config_cog (jeśli masz)
    if _get_channel_id:
        try:
            cid = _get_channel_id(guild.id)
            if cid:
                ch = guild.get_channel(int(cid))
                if ch:
                    return ch
        except Exception:
            pass
    # 2) fallback: pierwszy kanał tekstowy
    return guild.text_channels[0] if guild.text_channels else None

class _PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🟢 Auto (ON)", style=discord.ButtonStyle.success, custom_id="ext_mode_on")
    async def mode_on(self, itx: discord.Interaction, btn: discord.ui.Button):
        _save_mode("on")
        await itx.response.send_message("✅ Ustawiono tryb **ON**", ephemeral=True)

    @discord.ui.button(label="🟠 Hybrid", style=discord.ButtonStyle.primary, custom_id="ext_mode_hybrid")
    async def mode_hybrid(self, itx: discord.Interaction, btn: discord.ui.Button):
        _save_mode("hybrid")
        await itx.response.send_message("✅ Ustawiono tryb **HYBRID**", ephemeral=True)

    @discord.ui.button(label="🔴 OFF", style=discord.ButtonStyle.secondary, custom_id="ext_mode_off")
    async def mode_off(self, itx: discord.Interaction, btn: discord.ui.Button):
        _save_mode("off")
        await itx.response.send_message("⏸️ Ustawiono tryb **OFF**", ephemeral=True)

    @discord.ui.button(label="🛑 PANIC", style=discord.ButtonStyle.danger, custom_id="ext_mode_panic")
    async def mode_panic(self, itx: discord.Interaction, btn: discord.ui.Button):
        _save_mode("off")
        await itx.response.send_message("🛑 **PANIC MODE** – zatrzymano działania.", ephemeral=True)

class AdvisorPanelExtCog(commands.Cog):
    """NIE zastępuje Twojego starego advisora — tylko dodaje auto-panel + diag."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._auto_enabled = _load_auto().get("enabled", True)

    @commands.Cog.listener()
    async def on_ready(self):
        if not self._auto_enabled:
            return
        await asyncio.sleep(2)  # daj załadować się innym cogom
        for g in self.bot.guilds:
            ch = _pick_channel(g)
            if not ch:
                continue
            mode = _load_mode().get("mode", "off").upper()
            em = discord.Embed(
                title="🎛️ Panel sterowania (EXT)",
                description=(
                    f"Tryb: **{mode}** • Venue: Binance + Bitget\n"
                    f"• Sterowanie główne: `!mode on|hybrid|off|quiet`\n"
                    f"• Futures: `!fmode`, `!fpreset`, `!flimits`, `!fstatus`\n"
                    f"• Ten moduł: `!panel_auto on|off`, `!diag all`"
                ),
                color=0x5865F2
            )
            await ch.send(embed=em, view=_PanelView())
            break

    @commands.command(name="panel_auto")
    async def panel_auto(self, ctx: commands.Context, flag: str = None):
        """
        Włącz/wyłącz auto-panel po starcie.
        Użycie: !panel_auto on|off
        """
        if flag is None:
            return await ctx.send(f"Auto-panel: **{_load_auto().get('enabled', True)}**. Użycie: `!panel_auto on|off`")
        flag = flag.lower().strip()
        if flag not in ("on", "off"):
            return await ctx.send("Użycie: `!panel_auto on|off`")
        _save_auto(flag == "on")
        await ctx.send(f"✅ Auto-panel ustawiony na **{flag.upper()}**")

    @commands.command(name="diag")
    async def diag(self, ctx: commands.Context, what: str = "all"):
        """
        !diag all — szybki przegląd kluczowych komend (dry-run), bez handlu.
        """
        if what != "all":
            return await ctx.send("Użycie: `!diag all`")
        await ctx.send("🔧 Uruchamiam: `!selftest`, `!analiza_portfela`, `!futures_setups`, `!news`, `!whales` …")
        for cmd_name in ["selftest", "analiza_portfela", "futures_setups", "news", "whales"]:
            try:
                cmd = ctx.bot.get_command(cmd_name)
                if cmd:
                    await ctx.invoke(cmd)
            except Exception:
                pass

async def setup(bot: commands.Bot):
    await bot.add_cog(AdvisorPanelExtCog(bot))
