"""Backups of the database: listed, made, downloaded and restored by admins.

The database holds everything except the secret key: fades, users, and the service logins encrypted
with that key. A backup is therefore only fully useful together with the key, which lives outside
(CROSSFADE_SECRET_KEY or data/secret.key) and is never part of a backup or a download.

Restoring copies the backup into the live database with SQLite's online backup (no file swapping, so
every open connection keeps working), then runs the migrations of this version. Before that, a copy
of the current state is kept ("before-restore"), so a restore can itself be undone.
"""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

from cryptography.fernet import InvalidToken

from .db import MIGRATIONS, Database

# how many of each kind are kept (oldest go first)
KEEP = {"start": 10, "daily": 14, "manual": 20, "restore": 5}
KINDS = tuple(KEEP)
NAME = re.compile(r"crossfade-(\d{8}-\d{6})-schema(\d+)(?:-(daily|manual|restore))?(?:-\d+)?\.sqlite3")
DAY = 86400


class BackupError(ValueError):
    """A backup can't be used (shown to the admin as is)."""


def folder_of(db_path: Path) -> Path:
    return db_path.parent / "backups"


def parse(name: str) -> dict | None:
    m = NAME.fullmatch(name)
    if not m:
        return None
    at = time.mktime(time.strptime(m.group(1), "%Y%m%d-%H%M%S"))
    return {"name": name, "at": at, "schema": int(m.group(2)), "kind": m.group(3) or "start"}


def listing(db: Database) -> list[dict]:
    """Every backup, newest first."""
    out = []
    folder = folder_of(db.path)
    for f in folder.glob("crossfade-*.sqlite3") if folder.exists() else []:
        info = parse(f.name)
        if info and f.is_file():
            out.append({**info, "bytes": f.stat().st_size})
    return sorted(out, key=lambda b: b["at"], reverse=True)


def prune(folder: Path, kind: str, keep: int) -> None:
    mine = [f for f in folder.glob("crossfade-*.sqlite3") if (parse(f.name) or {}).get("kind") == kind]
    for old in sorted(mine, key=lambda f: f.stat().st_mtime)[:-keep]:
        old.unlink(missing_ok=True)


def path_of(db: Database, name: str) -> Path:
    """The file of a listed backup; never anything else (no paths from outside)."""
    if not parse(name):
        raise BackupError("Unknown backup.")
    f = folder_of(db.path) / name
    if not f.is_file():
        raise BackupError("Unknown backup.")
    return f


def daily_due(db: Database) -> bool:
    """No automatic backup (start or daily) in the last day."""
    return not any(b["kind"] in ("start", "daily") and b["at"] > time.time() - DAY for b in listing(db))


# ---------------------------------------------------------------------- checking a file
def inspect(path: Path) -> dict:
    """Is this a Crossfade database this version can take? Returns what it holds."""
    with open(path, "rb") as f:
        if f.read(16) != b"SQLite format 3\x00":
            raise BackupError("This isn't a Crossfade backup (not an SQLite database).")
    try:
        src = sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    except sqlite3.Error as e:
        raise BackupError(f"The file can't be opened: {e}") from e
    try:
        src.row_factory = sqlite3.Row
        if src.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise BackupError("The backup is damaged.")
        schema = src.execute("PRAGMA user_version").fetchone()[0]
        tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if schema < 6 or not {"users", "lines", "accounts", "instance_settings"} <= tables:
            raise BackupError("This isn't a Crossfade backup.")
        if schema > len(MIGRATIONS):
            raise BackupError(f"The backup comes from a newer Crossfade (schema {schema}). Update Crossfade first.")
        admins = src.execute("SELECT COUNT(*) FROM users WHERE is_admin=1").fetchone()[0]
        if not admins:
            raise BackupError("The backup has no admin, so nobody could sign in to manage it.")
        blob = src.execute("SELECT credentials FROM accounts WHERE credentials IS NOT NULL LIMIT 1").fetchone()
        info = {"schema": schema,
                "users": src.execute("SELECT COUNT(*) FROM users").fetchone()[0],
                "fades": src.execute("SELECT COUNT(*) FROM lines").fetchone()[0],
                "logins": src.execute("SELECT COUNT(*) FROM accounts WHERE credentials IS NOT NULL").fetchone()[0]}
    except sqlite3.DatabaseError as e:
        raise BackupError(f"The backup can't be read: {e}") from e
    finally:
        src.close()
    if blob:
        from .security import _fernet
        try:
            _fernet.decrypt(blob[0].encode())
        except InvalidToken as e:
            raise BackupError("The logins in this backup were saved with another secret key. Start Crossfade "
                              "with that key (CROSSFADE_SECRET_KEY) to restore it.") from e
    return info


# ---------------------------------------------------------------------- restoring
def restore(db: Database, path: Path, keep_session: str | None = None) -> dict:
    """Replace the live database with the backup at `path`. The caller makes sure nothing is syncing.
    `keep_session` (a token hash) stays signed in when its user is in the backup too."""
    info = inspect(path)
    session = db.one("""SELECT s.*, u.username FROM sessions s JOIN users u ON u.id=s.user_id
                        WHERE s.token_hash=?""", (keep_session,)) if keep_session else None
    before = db.backup("restore")
    src = sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        with db.write_lock:
            src.backup(db.conn)
    finally:
        src.close()
    db.migrate()
    if session and db.val("SELECT 1 FROM users WHERE id=? AND username=?", (session["user_id"], session["username"])):
        db.run("""INSERT OR IGNORE INTO sessions(token_hash,user_id,created_at,expires_at,user_agent)
                  VALUES(?,?,?,?,?)""", (session["token_hash"], session["user_id"], session["created_at"],
                                         session["expires_at"], session["user_agent"]))
    return {**info, "before": before.name if before else None}
