"""SQLite access (one connection per thread, WAL) with versioned migrations."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

MIGRATIONS: list[str] = [
    # ---- 1: initial schema -------------------------------------------------------------
    """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        display_name TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        is_admin INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL
    );
    CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        user_agent TEXT
    );
    CREATE TABLE invites (
        token_hash TEXT PRIMARY KEY,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        used_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        note TEXT
    );
    CREATE TABLE instance_settings (key TEXT PRIMARY KEY, value TEXT);

    -- One connected service per user and service type.
    CREATE TABLE accounts (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        service TEXT NOT NULL,
        display_name TEXT,
        credentials TEXT,             -- encrypted JSON
        status TEXT NOT NULL DEFAULT 'ok',   -- ok | error
        status_message TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        UNIQUE (user_id, service)
    );

    -- A "Fade" (table name `lines` predates the rename): a group of collections kept in sync.
    CREATE TABLE lines (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        color TEXT NOT NULL,
        sync_deletions INTEGER NOT NULL DEFAULT 1,
        interval_minutes INTEGER NOT NULL DEFAULT 60,
        delete_guard_min INTEGER NOT NULL DEFAULT 5,
        delete_guard_ratio REAL NOT NULL DEFAULT 0.2,
        paused INTEGER NOT NULL DEFAULT 0,
        position INTEGER NOT NULL DEFAULT 0,
        last_run_at REAL,
        last_status TEXT,
        last_summary TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE line_members (
        id INTEGER PRIMARY KEY,
        line_id INTEGER NOT NULL REFERENCES lines(id) ON DELETE CASCADE,
        account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,           -- liked | library | playlist
        collection_id TEXT NOT NULL DEFAULT '',
        collection_name TEXT NOT NULL,
        role TEXT NOT NULL,           -- both | send | receive
        position INTEGER NOT NULL DEFAULT 0,
        item_count INTEGER,
        initialized INTEGER NOT NULL DEFAULT 0
    );

    -- Global song identity across services.
    CREATE TABLE songs (
        id INTEGER PRIMARY KEY,
        title TEXT NOT NULL,
        artist TEXT NOT NULL DEFAULT '',
        album TEXT NOT NULL DEFAULT '',
        duration INTEGER,
        isrc TEXT,
        created_at REAL NOT NULL
    );
    CREATE INDEX songs_isrc ON songs(isrc);
    CREATE TABLE song_refs (
        service TEXT NOT NULL,
        ref_id TEXT NOT NULL,
        song_id INTEGER NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
        title TEXT, artist TEXT, album TEXT, duration INTEGER, isrc TEXT, url TEXT,
        PRIMARY KEY (service, ref_id)
    );
    CREATE INDEX song_refs_song ON song_refs(song_id, service);
    CREATE TABLE search_cache (
        service TEXT NOT NULL,
        song_id INTEGER NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
        best_ref TEXT,
        score REAL NOT NULL DEFAULT 0,
        candidates TEXT,
        created_at REAL NOT NULL,
        PRIMARY KEY (service, song_id)
    );

    -- What the line considers present (the shared set) ...
    CREATE TABLE line_items (
        line_id INTEGER NOT NULL REFERENCES lines(id) ON DELETE CASCADE,
        song_id INTEGER NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
        added_at REAL NOT NULL,
        PRIMARY KEY (line_id, song_id)
    );
    -- ... and what each member was last seen with / was sent.
    -- confirmed: seen in the collection at least once. excluded: removed by the user on a
    -- receive-only member, so it is not added again.
    CREATE TABLE member_items (
        member_id INTEGER NOT NULL REFERENCES line_members(id) ON DELETE CASCADE,
        song_id INTEGER NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
        ref_id TEXT,
        confirmed INTEGER NOT NULL DEFAULT 0,
        excluded INTEGER NOT NULL DEFAULT 0,
        misses INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (member_id, song_id)
    );

    -- Things that need the user: songs not found, deletions above the safety limit.
    CREATE TABLE issues (
        id INTEGER PRIMARY KEY,
        line_id INTEGER NOT NULL REFERENCES lines(id) ON DELETE CASCADE,
        member_id INTEGER REFERENCES line_members(id) ON DELETE CASCADE,
        song_id INTEGER REFERENCES songs(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,           -- unmatched | ignored
        data TEXT,
        created_at REAL NOT NULL,
        UNIQUE (line_id, member_id, song_id, kind)
    );

    CREATE TABLE runs (
        id INTEGER PRIMARY KEY,
        line_id INTEGER NOT NULL REFERENCES lines(id) ON DELETE CASCADE,
        trigger TEXT NOT NULL,        -- manual | schedule | preview
        started_at REAL NOT NULL,
        finished_at REAL,
        status TEXT NOT NULL,
        summary TEXT,
        plan TEXT
    );
    CREATE INDEX runs_line ON runs(line_id, id DESC);
    CREATE TABLE changes (
        id INTEGER PRIMARY KEY,
        run_id INTEGER REFERENCES runs(id) ON DELETE CASCADE,
        line_id INTEGER NOT NULL REFERENCES lines(id) ON DELETE CASCADE,
        member_id INTEGER REFERENCES line_members(id) ON DELETE SET NULL,
        song_id INTEGER REFERENCES songs(id) ON DELETE SET NULL,
        action TEXT NOT NULL,         -- add | remove
        label TEXT,
        ok INTEGER NOT NULL,
        message TEXT,
        ts REAL NOT NULL
    );
    CREATE INDEX changes_line ON changes(line_id, id DESC);
    """,
    # ---- 2: version-aware matching --------------------------------------------------------
    """
    ALTER TABLE song_refs ADD COLUMN explicit INTEGER;
    ALTER TABLE song_refs ADD COLUMN album_type TEXT;
    ALTER TABLE song_refs ADD COLUMN video_type TEXT;
    ALTER TABLE search_cache ADD COLUMN exact INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE search_cache ADD COLUMN notes TEXT;
    ALTER TABLE lines ADD COLUMN version_policy TEXT NOT NULL DEFAULT 'closest';
    ALTER TABLE changes ADD COLUMN note TEXT;
    DELETE FROM search_cache;
    """,
    # ---- 3: cover art ------------------------------------------------------------------------
    """
    ALTER TABLE song_refs ADD COLUMN artwork TEXT;
    """,
    # ---- 4: song details behind YouTube videos (Google-code login) -----------------------------
    """
    CREATE TABLE ytm_meta (video_id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at REAL NOT NULL);
    DELETE FROM search_cache;
    """,
    # ---- 5: music catalog (everything a user has, across services) -----------------------------
    """
    CREATE TABLE catalog_sources (
        id INTEGER PRIMARY KEY,
        account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,           -- liked | library | playlist
        collection_id TEXT NOT NULL DEFAULT '',
        name TEXT NOT NULL,
        editable INTEGER NOT NULL DEFAULT 1,
        included INTEGER NOT NULL DEFAULT 1,
        item_count INTEGER,
        scanned_at REAL,
        error TEXT,
        UNIQUE (account_id, kind, collection_id)
    );
    CREATE TABLE catalog_items (
        source_id INTEGER NOT NULL REFERENCES catalog_sources(id) ON DELETE CASCADE,
        ref_id TEXT NOT NULL,
        song_id INTEGER NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
        position INTEGER NOT NULL DEFAULT 0,
        first_seen REAL NOT NULL,
        PRIMARY KEY (source_id, ref_id)
    );
    CREATE INDEX catalog_items_song ON catalog_items(song_id);
    """,
    # ---- 6: Crossfade redesign: fade icons, per-user appearance and language, admin-created users
    """
    ALTER TABLE lines ADD COLUMN icon TEXT NOT NULL DEFAULT 'note';
    ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE users ADD COLUMN theme TEXT NOT NULL DEFAULT 'system';
    ALTER TABLE users ADD COLUMN lang TEXT NOT NULL DEFAULT 'system';
    DROP TABLE IF EXISTS invites;
    """,
    # ---- 7: a song only counts as removed after it was missing on two reads some time apart
    """
    ALTER TABLE member_items ADD COLUMN missing_since REAL;
    """,
    # ---- 8: a collection that only sends can still follow deletions made elsewhere
    """
    ALTER TABLE line_members ADD COLUMN follow_deletions INTEGER NOT NULL DEFAULT 0;
    """,
    # ---- 9: tidying the library: copies in a playlist, video or song on YouTube, dismissed hints
    """
    ALTER TABLE catalog_items ADD COLUMN copies INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE accounts ADD COLUMN yt_format TEXT NOT NULL DEFAULT 'song';
    -- the music video of a YouTube Music song and the song of a video ('' = there is none)
    CREATE TABLE yt_counterparts (video_id TEXT PRIMARY KEY, counterpart TEXT NOT NULL, checked_at REAL NOT NULL);
    CREATE TABLE cleanup_dismissed (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        key TEXT NOT NULL,
        created_at REAL NOT NULL,
        PRIMARY KEY (user_id, key)
    );
    """,
    # ---- 10: friends and shared fades. Nothing of a friend's music is visible before they confirm:
    # a friendship is only a pair of names, an invite only names the fade, and a friend's collection
    # joins a fade only when they pick it themselves.
    """
    CREATE TABLE friendships (
        id INTEGER PRIMARY KEY,
        requester_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        addressee_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        status TEXT NOT NULL DEFAULT 'pending',     -- pending | accepted
        created_at REAL NOT NULL,
        accepted_at REAL,
        UNIQUE (requester_id, addressee_id)
    );
    CREATE TABLE fade_invites (
        id INTEGER PRIMARY KEY,
        line_id INTEGER NOT NULL REFERENCES lines(id) ON DELETE CASCADE,
        from_user INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        to_user INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at REAL NOT NULL,
        UNIQUE (line_id, to_user)
    );
    CREATE INDEX fade_invites_to ON fade_invites(to_user);
    """,
    # ---- 11: search with what other services know: a miss is searched again once the song turns up
    # elsewhere (helpers = the refs the search used, "service:id,…")
    """
    ALTER TABLE search_cache ADD COLUMN helpers TEXT;
    """,
    # ---- 12: which release a duplicate keeps when the same recording is on an album and a single
    """
    ALTER TABLE users ADD COLUMN release_pref TEXT NOT NULL DEFAULT 'album';
    """,
    # ---- 13: show up in the friends' ranking (on unless the user opts out; off also hides it from them)
    """
    ALTER TABLE users ADD COLUMN share_stats INTEGER NOT NULL DEFAULT 1;
    """,
    # ---- 14: joining a friend's fade is a request: the friend picks a collection and direction
    # (request = JSON of that choice), the owner approves it, possibly with another direction
    """
    ALTER TABLE fade_invites ADD COLUMN request TEXT;
    ALTER TABLE fade_invites ADD COLUMN requested_at REAL;
    """,
    # ---- 15: notifications (ntfy, webhook, e-mail) and a daily check of every login
    """
    CREATE TABLE notify_channels (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,              -- ntfy | webhook | email
        config TEXT NOT NULL,            -- encrypted JSON (topic, URL, token, address)
        events TEXT NOT NULL,            -- JSON list of event names
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at REAL NOT NULL,
        last_sent_at REAL,
        last_error TEXT
    );
    CREATE INDEX notify_channels_user ON notify_channels(user_id);
    -- what was already sent, so a lasting problem is reported once (value: e.g. the count it was about)
    CREATE TABLE notify_state (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        key TEXT NOT NULL,
        value TEXT,
        sent_at REAL NOT NULL,
        PRIMARY KEY (user_id, key)
    );
    ALTER TABLE accounts ADD COLUMN checked_at REAL;      -- last time the login was tried
    ALTER TABLE accounts ADD COLUMN expires_at REAL;      -- when the login ends, if the service says
    ALTER TABLE accounts ADD COLUMN connected_at REAL;    -- when this login was entered
    UPDATE accounts SET connected_at=updated_at;
    """,
    # ---- 16: how the services are doing, per hour (admin health page)
    """
    CREATE TABLE service_stats (
        hour INTEGER NOT NULL,           -- unix time // 3600
        service TEXT NOT NULL,
        calls INTEGER NOT NULL DEFAULT 0,
        errors INTEGER NOT NULL DEFAULT 0,
        auth_errors INTEGER NOT NULL DEFAULT 0,
        rate_limited INTEGER NOT NULL DEFAULT 0,
        failed_items INTEGER NOT NULL DEFAULT 0,
        ms INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (hour, service)
    );
    """,
    # ---- 17: time travel: how each collection looked over time (see crossfade/history.py)
    """
    CREATE TABLE snapshots (
        id INTEGER PRIMARY KEY,
        account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        collection_id TEXT NOT NULL DEFAULT '',
        taken_at REAL NOT NULL,          -- when this content was first seen
        last_seen REAL NOT NULL,         -- last read that still found exactly this
        count INTEGER NOT NULL,
        digest TEXT NOT NULL,
        items BLOB NOT NULL,             -- zlib JSON list of the service's ids, in listed order
        origin TEXT NOT NULL             -- sync | scan | restore
    );
    CREATE INDEX snapshots_collection ON snapshots(account_id, kind, collection_id, taken_at);
    """,
]


class Database:
    def __init__(self, path: Path, backup: bool = False):
        self.path = path
        self._local = threading.local()
        self.write_lock = threading.RLock()
        if backup:
            self.backup()
        self.migrate()

    def backup(self, keep: int = 10) -> Path | None:
        """Consistent copy of the database (SQLite online backup) into data/backups, before
        migrations run – an update can always be rolled back with the previous image + this file."""
        if not self.path.exists() or self.path.stat().st_size == 0:
            return None
        folder = self.path.parent / "backups"
        folder.mkdir(exist_ok=True)
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        target = folder / f"crossfade-{time.strftime('%Y%m%d-%H%M%S')}-schema{version}.sqlite3"
        dst = sqlite3.connect(target)
        try:
            self.conn.backup(dst)
        finally:
            dst.close()
        target.chmod(0o600)
        backups = sorted(folder.glob("crossfade-*.sqlite3"), key=lambda f: f.stat().st_mtime)
        for old in backups[:-keep]:
            old.unlink(missing_ok=True)
        return target

    @property
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, isolation_level=None, timeout=30, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA busy_timeout=30000")
            self._local.conn = c
        return c

    def migrate(self) -> None:
        c = self.conn
        version = c.execute("PRAGMA user_version").fetchone()[0]
        for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
            with self.write_lock:
                c.executescript("BEGIN;" + sql + f"; PRAGMA user_version={i}; COMMIT;")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    # ---------------------------------------------------------------- helpers
    def all(self, sql: str, args: tuple | list = ()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql: str, args: tuple | list = ()) -> dict | None:
        r = self.conn.execute(sql, args).fetchone()
        return dict(r) if r else None

    def val(self, sql: str, args: tuple | list = ()) -> Any:
        r = self.conn.execute(sql, args).fetchone()
        return r[0] if r else None

    def run(self, sql: str, args: tuple | list = ()) -> int:
        with self.write_lock:
            cur = self.conn.execute(sql, args)
            return cur.lastrowid if sql.lstrip().upper().startswith("INSERT") else cur.rowcount

    def many(self, sql: str, rows: list) -> None:
        if rows:
            with self.write_lock:
                self.conn.executemany(sql, rows)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self.write_lock:
            c = self.conn
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise

    # ---------------------------------------------------------------- instance settings
    def setting(self, key: str, default: Any = None) -> Any:
        v = self.val("SELECT value FROM instance_settings WHERE key=?", (key,))
        return json.loads(v) if v is not None else default

    def set_setting(self, key: str, value: Any) -> None:
        if value is None:
            self.run("DELETE FROM instance_settings WHERE key=?", (key,))
        else:
            self.run("INSERT INTO instance_settings(key,value) VALUES(?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))


def now() -> float:
    return time.time()


_db: Database | None = None


def get_db() -> Database:
    global _db
    if _db is None:
        from .config import config
        _db = Database(config.db_path, backup=config.backup_on_start)
    return _db
