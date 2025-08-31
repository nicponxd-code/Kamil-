PACZKA #1 — Autoscan Gems + Scan & Rank

1) Uruchom instalator (z katalogu projektu lub dowolnie wewnątrz):
   python install_paczka1.py

   Instalator:
   - wykona backup zmienianych plików do backups/p1_YYYYMMDD_HHMMSS/
   - dogra pliki:
       app/engine/gems_autoscan.py
       app/engine/scan_rank.py
       app/db_migrations/003_gems.sql
   - odpali migracje (app/migrate.py) lub na fallbacku wstrzyknie SQL
   - włączy AUTOSCAN_ENABLED=1 w kv_settings

2) Restart bota. Pętla autoscan uruchomi się automatycznie, jeśli Twój main tworzy
   obiekt GemsAutoscan i woła .start(). Jeśli nie — odezwij się, dodam auto-boot,
   który wykryje bota i sam podłączy pętlę bez zmian w Twoim kodzie.

3) Komendy / kafelki:
   - "Autoscan" (toggle) może używać klucza AUTOSCAN_ENABLED w kv_settings (0/1).
   - "Scan & Rank" może wywołać app/engine/scan_rank.run_scan_and_rank().

Kontakt: jeśli coś nie wstanie, wrzuć log z konsoli i zrobię hotfix.
