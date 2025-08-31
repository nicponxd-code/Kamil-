#!/usr/bin/env python3
import os, sys, shutil, time, sqlite3, subprocess, json, datetime
from pathlib import Path

PATCH_FILES = {
    "app/engine/gems_autoscan.py": "app/engine/gems_autoscan.py",
    "app/engine/scan_rank.py": "app/engine/scan_rank.py",
    "app/db_migrations/003_gems.sql": "app/db_migrations/003_gems.sql",
}

def log(msg):
    print(f"[paczka1] {msg}")

def ensure_parent(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)

def backup_file(dst_root: Path, rel_path: str, backup_dir: Path):
    dst = dst_root / rel_path
    if dst.exists():
        ensure_parent(backup_dir / rel_path)
        shutil.copy2(dst, backup_dir / rel_path)
        log(f"Backup: {rel_path} -> {backup_dir / rel_path}")

def write_file(dst_root: Path, rel_path: str, data: bytes):
    dst = dst_root / rel_path
    ensure_parent(dst)
    with open(dst, "wb") as f:
        f.write(data)
    log(f"Wrote: {rel_path} ({len(data)} bytes)")

def find_project_root(start: Path) -> Path:
    cur = start.resolve()
    for _ in range(6):
        if (cur / "app").exists():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return start.resolve()

def apply_sql(db_path: Path, sql_text: str):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(sql_text)
        conn.commit()
    finally:
        conn.close()

def run_migrations(proj: Path):
    migrate_py = proj / "app" / "migrate.py"
    db_path = proj / "data" / "bot.db"
    sql_path = proj / "app" / "db_migrations" / "003_gems.sql"

    if migrate_py.exists():
        log("Running app.migrate ...")
        try:
            subprocess.check_call([sys.executable, str(migrate_py)])
            return True
        except Exception as e:
            log(f"app.migrate failed: {e}")
    # fallback: apply our SQL only
    if sql_path.exists():
        log(f"Applying SQL directly to {db_path} ...")
        sql = sql_path.read_text(encoding="utf-8")
        apply_sql(db_path, sql)
        return True
    return False

def load_patch_bin(rel_path: str) -> bytes:
    # Load the embedded binary blob from within this script directory
    here = Path(__file__).parent
    blob_path = here / "paczka1_payload" / rel_path
    return blob_path.read_bytes()

def set_autoscan_toggle(proj: Path, enable: bool = True):
    # set key AUTOSCAN_ENABLED in kv_settings table (created by migration); safe even if table exists
    db_path = proj / "data" / "bot.db"
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS kv_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        );""")
        conn.commit()
        cur.execute("INSERT INTO kv_settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at;",
                    ("AUTOSCAN_ENABLED", "1" if enable else "0", int(time.time())))
        conn.commit()
        conn.close()
        log(f"AUTOSCAN_ENABLED set to {'1' if enable else '0'}")
    except Exception as e:
        log(f"Failed to set AUTOSCAN_ENABLED: {e}")

def main():
    log("Self-installing patch starting...")
    proj = find_project_root(Path.cwd())
    log(f"Detected project root: {proj}")

    # Validate structure
    if not (proj / "app").exists():
        log("ERROR: Cannot find 'app/' directory. Run this script from within your project.")
        sys.exit(2)

    # Prepare backup dir
    bdir = proj / "backups" / ("p1_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    bdir.mkdir(parents=True, exist_ok=True)

    # Copy files
    for rel in PATCH_FILES:
        backup_file(proj, rel, bdir)
        data = load_patch_bin(PATCH_FILES[rel])
        write_file(proj, rel, data)

    # Run migrations
    ok = run_migrations(proj)
    if not ok:
        log("WARNING: Could not run migrations automatically. You may need to run `python -m app.migrate`.")

    # Enable autoscan by default (can be toggled later via command)
    set_autoscan_toggle(proj, True)

    log("Patch installed successfully ✅")
    log("Next steps: restart your bot process. Autoscan loop will start if AUTOSCAN_ENABLED=1 in kv_settings.")

if __name__ == "__main__":
    main()
