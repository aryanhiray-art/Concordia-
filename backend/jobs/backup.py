#!/usr/bin/env python3
"""Off-box-capable, WAL-safe online backup of the Concord SQLite DB.

`concord.db` is the ONLY store of the Fernet-encrypted per-member Robinhood OAuth
vault, the executions ledger, and the club's owned-shares basis. A disk loss
without a backup is unrecoverable — hence this job.

It takes a hot, consistent copy with sqlite3's online backup API
(`src.backup(dst)`), which is WAL-safe (do NOT `cp` a live WAL DB — you can copy
a torn page). The copy lands in /opt/concord/backups/ mode 0600, the newest ~14
are kept, and — if CONCORD_BACKUP_DEST is set — the fresh copy is shipped off-box
via rsync/scp (failures warn, never crash: the local backup already succeeded).

IMPORTANT: this job never writes the Fernet key into the backup dir. The key
(CONCORD_FERNET_KEY) and CONCORD_ADMIN_TOKEN live only in /opt/concord/.env and
MUST be backed up SEPARATELY, out of band (see RUNBOOK §7). Without the key, the
encrypted vault inside any DB backup is useless.

systemd: concord-backup.timer → this. Stdlib only.
"""
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


KEEP = _int_env("CONCORD_BACKUP_KEEP", 14)          # how many local copies to retain
SHIP_TIMEOUT = _int_env("CONCORD_BACKUP_SHIP_TIMEOUT", 300)  # seconds for the off-box push


def _paths() -> tuple[Path, Path]:
    """(source DB, backup directory). Honours CONCORD_DB_PATH / CONCORD_BACKUP_DIR."""
    src = Path(settings.db_path).expanduser().resolve()
    # /opt/concord/backend/concord.db → /opt/concord/backups
    default_dir = src.parent.parent / "backups"
    backup_dir = Path(os.environ.get("CONCORD_BACKUP_DIR", str(default_dir))).expanduser()
    return src, backup_dir


def online_backup(src: Path, dst: Path) -> None:
    """Hot, WAL-safe copy of `src` → `dst` via sqlite3's online backup API.

    The destination inherits the source's WAL journal mode, which would spawn
    `-wal`/`-shm` sidecars whenever the archive is later opened (restore/verify).
    We flip the copy to rollback-journal mode so each backup is ONE self-contained
    file — portable to ship off-box and trivial to restore.
    """
    src_conn = sqlite3.connect(str(src), timeout=30)
    try:
        dst_conn = sqlite3.connect(str(dst))
        try:
            src_conn.backup(dst_conn)
            dst_conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")  # flush WAL → main file
            dst_conn.execute("PRAGMA journal_mode=DELETE;")       # single-file archive
        finally:
            dst_conn.close()
    finally:
        src_conn.close()
    # the WAL→DELETE conversion can leave vestigial sidecars; all data is now in
    # the main file, so drop them for a clean, portable single-file archive
    for side in (Path(str(dst) + "-wal"), Path(str(dst) + "-shm")):
        try:
            side.unlink()
        except OSError:
            pass


def prune(backup_dir: Path, keep: int) -> int:
    """Delete all but the newest `keep` backups. Names sort chronologically."""
    if keep <= 0:
        return 0
    files = sorted(backup_dir.glob("concord-*.db"))
    stale = files[:-keep] if len(files) > keep else []
    removed = 0
    for f in stale:
        try:
            f.unlink()
            removed += 1
        except OSError as e:
            print(f"[backup] WARNING: could not prune {f.name}: {e}")
        # sweep any stray WAL/SHM sidecars a restore/verify may have left behind
        for side in (f.with_name(f.name + "-wal"), f.with_name(f.name + "-shm")):
            try:
                side.unlink()
            except OSError:
                pass
    return removed


def ship_offbox(path: Path, dest: str) -> None:
    """Copy `path` to `dest` (an rsync/scp target or a mounted path). Never raises."""
    rsync = shutil.which("rsync")
    if rsync:
        cmd = [rsync, "-a",
               "-e", "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new",
               str(path), dest]
    else:
        scp = shutil.which("scp")
        if not scp:
            print("[backup] WARNING: neither rsync nor scp on PATH — skipping off-box ship")
            return
        cmd = [scp, "-B", "-o", "StrictHostKeyChecking=accept-new", str(path), dest]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=SHIP_TIMEOUT)
        if r.returncode != 0:
            msg = (r.stderr.strip() or r.stdout.strip() or "no output")
            print(f"[backup] WARNING: off-box ship failed (exit {r.returncode}): {msg}")
        else:
            print(f"[backup] shipped off-box → {dest}")
    except subprocess.TimeoutExpired:
        print(f"[backup] WARNING: off-box ship timed out after {SHIP_TIMEOUT}s → {dest}")
    except Exception as e:  # never let a bad dest crash the (successful) local backup
        print(f"[backup] WARNING: off-box ship error: {e}")


def main() -> int:
    src, backup_dir = _paths()
    if not src.exists():
        print(f"[backup] ERROR: source DB not found: {src}")
        return 1

    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = backup_dir / f"concord-{ts}.db"

    try:
        online_backup(src, dst)
    except Exception as e:
        print(f"[backup] ERROR: online backup failed: {e}")
        try:
            dst.unlink()  # drop any partial file
        except FileNotFoundError:
            pass
        return 1

    os.chmod(dst, 0o600)
    print(f"[backup] wrote {dst} ({dst.stat().st_size} bytes)")

    removed = prune(backup_dir, KEEP)
    if removed:
        print(f"[backup] pruned {removed} old backup(s); keeping newest {KEEP}")

    dest = os.environ.get("CONCORD_BACKUP_DEST", "").strip()
    if dest:
        ship_offbox(dst, dest)
    else:
        print("[backup] CONCORD_BACKUP_DEST unset — local backup only (see RUNBOOK §7)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
