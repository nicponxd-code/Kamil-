# Advisor Bot v4.1 (HYBRID default)

**Bot-doradca** dla SPOT/Futures + perełki z DEX. Generuje sygnały z pełnym planem (Entry, SL, 3×TP 40/40/20, trailing), wylicza EDGE na bazie FVG/RR/OBI + NEWS/WHALE/ONCHAIN (braki=0.5), przechodzi przez bramki ryzyka, a następnie wysyła **interaktywne kafelki** na Discorda (✅/❌/🕑/🖼️). UI w Streamlit.

## Szybki start (Windows)
1. Pobierz ZIP i rozpakuj np. do `C:\Users\Kamil\Desktop\Finalv2\advisor-bot-v4.1`.
2. Skopiuj `.env.example` na `.env` i uzupełnij **TOKEN** i (opcjonalnie) klucze API.
3. Zainstaluj zależności:
   ```powershell
   cd C:\Users\Kamil\Desktop\Finalv2\advisor-bot-v4.1
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```
4. Uruchom bota (Discord + silnik):
   ```powershell
   python -m app.main
   ```
   Pierwsze uruchomienie utworzy DB i machinerię zadań.
5. UI (osobno, w drugim oknie):
   ```powershell
   streamlit run app/ui/app_streamlit.py --server.port %STREAMLIT_PORT%
   ```

## Najważniejsze komendy na Discordzie
- `/mode` – SAFE/HYBRID/ON
- `/status` – stan systemu i bramek
- `/selftest` – test giełd i źródeł
- `/signal` – generuj najlepszy sygnał teraz
- `/portfolio` – saldo i koncentracje
- `/gem` – perełki z DEX (trending + watchlista)

## Struktura projektu
- `app/` – kod źródłowy
- `data/bot.db` – SQLite (tworzy się automatycznie)
- `.env` – konfiguracja “jednego źródła prawdy”
- `requirements.txt` – zależności

## Uwaga
- Domyślnie **HYBRID**: Ty zatwierdzasz; auto-approve ≥80% po 2 min; auto-reject <60% po 10 min.
- SPOT **ON** już teraz; Futures (pełne OCO) – krokowo w następnych wydaniach.
- Jeśli NEWS/WHALE/ONCHAIN są niedostępne → przyjmujemy neutralne **0.5**, więc nie wywracają EDGE.


## Command tiles & daemon
Uruchom panel kafelków: `streamlit run app/ui/app_streamlit.py`
W drugim terminalu uruchom kolejkę komend: `python -m app.command_daemon`


## Discord bot
Uruchom: `python -m app.bot.discord_bot`
W .env ustaw: DISCORD_TOKEN, DISCORD_CHANNEL_ID, (opcjonalnie) DISCORD_GUILD_ID.
