"""Time travel: how each collection looked over time, and bringing an earlier state back.

Whenever Crossfade reads a whole collection (a sync, a library scan), it keeps a snapshot – only
when something changed since the last one, otherwise it just notes that the content was still the
same. A snapshot is the list of the service's own ids in the listed order, so restoring needs no
search: the exact entries go back.

Kept: everything of the last 7 days, then the last snapshot of each day up to 90 days.
"""
from __future__ import annotations

import hashlib
import json
import time
import zlib
from collections.abc import Callable

from .db import Database
from .identity import Identity
from .models import SERVICE_NAMES, ApiError, AuthError, Track

KEEP_ALL_DAYS = 7
KEEP_DAYS = 90
NAMES = {"liked": "Liked songs", "library": "Library"}


def _pack(refs: list[str]) -> bytes:
    return zlib.compress(json.dumps(refs, separators=(",", ":")).encode(), 6)


def unpack(blob: bytes) -> list[str]:
    return json.loads(zlib.decompress(blob))


def _digest(refs: list[str]) -> str:
    return hashlib.sha1("\n".join(sorted(set(refs))).encode()).hexdigest()


def capture(db: Database, account_id: int, kind: str, collection_id: str, refs: list[str], origin: str,
            now: float | None = None) -> int | None:
    """Keep the collection's current content; returns the new snapshot id (None: nothing changed)."""
    now = now or time.time()
    seen: dict[str, None] = {}
    for r in refs:
        seen.setdefault(r, None)
    refs = list(seen)
    digest = _digest(refs)
    last = db.one("""SELECT id, digest FROM snapshots WHERE account_id=? AND kind=? AND collection_id=?
                     ORDER BY taken_at DESC, id DESC LIMIT 1""", (account_id, kind, collection_id or ""))
    if last and last["digest"] == digest:
        db.run("UPDATE snapshots SET last_seen=? WHERE id=?", (now, last["id"]))
        return None
    sid = db.run("""INSERT INTO snapshots(account_id,kind,collection_id,taken_at,last_seen,count,digest,items,origin)
                    VALUES(?,?,?,?,?,?,?,?,?)""",
                 (account_id, kind, collection_id or "", now, now, len(refs), digest, _pack(refs), origin))
    prune(db, account_id, kind, collection_id or "", now)
    return sid


def prune(db: Database, account_id: int, kind: str, collection_id: str, now: float | None = None) -> None:
    now = now or time.time()
    rows = db.all("""SELECT id, taken_at FROM snapshots WHERE account_id=? AND kind=? AND collection_id=?
                     ORDER BY taken_at DESC, id DESC""", (account_id, kind, collection_id))
    drop, days = [], set()
    for i, r in enumerate(rows):
        age = now - r["taken_at"]
        if i == 0 or age < KEEP_ALL_DAYS * 86400:
            continue
        day = int(r["taken_at"] // 86400)
        if age > KEEP_DAYS * 86400 or day in days:
            drop.append(r["id"])
        else:
            days.add(day)        # newest first: the first one seen of a day is that day's last state
    if drop:
        db.run(f"DELETE FROM snapshots WHERE id IN ({','.join('?' * len(drop))})", drop)


# ---------------------------------------------------------------------- reading
def collection_name(db: Database, account_id: int, kind: str, collection_id: str) -> str:
    name = db.val("SELECT name FROM catalog_sources WHERE account_id=? AND kind=? AND collection_id=?",
                  (account_id, kind, collection_id)) or db.val(
        "SELECT collection_name FROM line_members WHERE account_id=? AND kind=? AND collection_id=? LIMIT 1",
        (account_id, kind, collection_id))
    return name or NAMES.get(kind, "Playlist")


def collections(db: Database, user_id: int) -> list[dict]:
    rows = db.all("""SELECT s.account_id, a.service, s.kind, s.collection_id, COUNT(*) AS snapshots,
                     MIN(s.taken_at) AS first, MAX(s.last_seen) AS last_seen,
                     (SELECT count FROM snapshots x WHERE x.account_id=s.account_id AND x.kind=s.kind
                      AND x.collection_id=s.collection_id ORDER BY x.taken_at DESC, x.id DESC LIMIT 1) AS count
                     FROM snapshots s JOIN accounts a ON a.id=s.account_id WHERE a.user_id=?
                     GROUP BY s.account_id, s.kind, s.collection_id""", (user_id,))
    for r in rows:
        r["name"] = collection_name(db, r["account_id"], r["kind"], r["collection_id"])
        r["fades"] = db.val("SELECT COUNT(DISTINCT line_id) FROM line_members WHERE account_id=? AND kind=? "
                            "AND collection_id=?", (r["account_id"], r["kind"], r["collection_id"]))
    rows.sort(key=lambda r: (r["service"], {"liked": 0, "library": 1}.get(r["kind"], 2), r["name"].lower()))
    return rows


def own_snapshot(db: Database, snapshot_id: int, user_id: int) -> dict | None:
    return db.one("""SELECT s.*, a.service, a.user_id FROM snapshots s JOIN accounts a ON a.id=s.account_id
                     WHERE s.id=? AND a.user_id=?""", (snapshot_id, user_id))


def timeline(db: Database, account_id: int, kind: str, collection_id: str) -> list[dict]:
    rows = db.all("""SELECT id, taken_at, last_seen, count, origin, items FROM snapshots
                     WHERE account_id=? AND kind=? AND collection_id=? ORDER BY taken_at, id""",
                  (account_id, kind, collection_id))
    out, prev = [], None
    for r in rows:
        refs = set(unpack(r.pop("items")))
        r["added"] = len(refs - prev) if prev is not None else None
        r["removed"] = len(prev - refs) if prev is not None else None
        prev = refs
        out.append(r)
    return list(reversed(out))


def songs(db: Database, service: str, refs: list[str]) -> list[dict]:
    """What the app shows for these entries (title, artist, cover), in the given order."""
    info: dict[str, dict] = {}
    for i in range(0, len(refs), 500):
        part = refs[i:i + 500]
        for r in db.all(f"SELECT * FROM song_refs WHERE service=? AND ref_id IN ({','.join('?' * len(part))})",
                        (service, *part)):
            info[r["ref_id"]] = r
    out = []
    for ref in refs:
        r = info.get(ref)
        out.append({"ref": ref, "song_id": r["song_id"] if r else None, "title": (r or {}).get("title") or ref,
                    "artist": (r or {}).get("artist") or "", "album": (r or {}).get("album") or "",
                    "artwork": (r or {}).get("artwork"), "url": (r or {}).get("url"), "service": service})
    return out


def compare(db: Database, snap: dict) -> dict:
    """The snapshot against the newest one: what is gone since then and what came in."""
    latest = db.one("""SELECT id, taken_at, last_seen, items FROM snapshots WHERE account_id=? AND kind=?
                       AND collection_id=? ORDER BY taken_at DESC, id DESC LIMIT 1""",
                    (snap["account_id"], snap["kind"], snap["collection_id"]))
    then, now = unpack(snap["items"]), unpack(latest["items"])
    now_set, then_set = set(now), set(then)
    return {"latest": {"id": latest["id"], "taken_at": latest["taken_at"], "last_seen": latest["last_seen"]},
            "missing": songs(db, snap["service"], [r for r in then if r not in now_set]),
            "extra": songs(db, snap["service"], [r for r in now if r not in then_set])}


# ---------------------------------------------------------------------- restoring
class Stopped(Exception):
    pass


def restore(db: Database, user_id: int, providers: dict, snapshot_id: int, mode: str, only: list[str] | None,
            progress: Callable[[str, int, int], None], should_stop: Callable[[], bool]) -> dict:
    """Bring a collection back to a snapshot. mode 'missing': add what is gone since (or just `only`);
    'exact': also remove what came in since. Reads the collection live first, so nothing that is
    already there is added twice, and keeps the state before as its own snapshot."""
    snap = own_snapshot(db, snapshot_id, user_id)
    if not snap:
        raise ApiError("Snapshot not found.")
    prov = providers.get(snap["account_id"])
    if prov is None:
        raise AuthError(f"{SERVICE_NAMES.get(snap['service'], snap['service'])} is not connected.")
    kind, cid, svc = snap["kind"], snap["collection_id"], snap["service"]
    label = f"{SERVICE_NAMES.get(svc, svc)} · {collection_name(db, snap['account_id'], kind, cid)}"
    progress(f"Reading {label} …", 0, 0)
    current = prov.read(kind, cid)
    capture(db, snap["account_id"], kind, cid, [t.id for t in current], "sync")   # the state before
    want = unpack(snap["items"])
    have = {t.id for t in current}
    missing = [r for r in want if r not in have]
    if only is not None:
        keep = set(only)
        missing = [r for r in missing if r in keep]
    extra = [t for t in current if t.id not in set(want)] if mode == "exact" else []
    ident = Identity(db)
    tracks = []
    for r in missing:
        row = db.one("SELECT * FROM song_refs WHERE service=? AND ref_id=?", (svc, r))
        tracks.append(ident._track(row) if row else Track(service=svc, id=r, title=r))
    total = len(tracks) + len(extra)
    done, failed = 0, 0
    added_ok: list[str] = []
    for i in range(0, len(tracks), 50):
        if should_stop():
            raise Stopped()
        part = tracks[i:i + 50]
        progress(f"Bringing songs back to {label} …", done, total)
        bad = set(prov.add(kind, cid, part))
        failed += len(bad)
        added_ok += [t.id for t in part if t.id not in bad]
        done += len(part)
    removed_ok: list[str] = []
    for i in range(0, len(extra), 50):
        if should_stop():
            raise Stopped()
        part = extra[i:i + 50]
        progress(f"Removing songs from {label} …", done, total)
        bad = set(prov.remove(kind, cid, part))
        failed += len(bad)
        removed_ok += [t.id for t in part if t.id not in bad]
        done += len(part)
    progress("Done", total, total)
    gone = set(removed_ok)
    after = [t.id for t in current if t.id not in gone] + added_ok
    capture(db, snap["account_id"], kind, cid, after, "restore")
    _update_library(db, snap["account_id"], kind, cid, added_ok, removed_ok)
    fades = db.val("SELECT COUNT(DISTINCT line_id) FROM line_members WHERE account_id=? AND kind=? AND collection_id=?",
                   (snap["account_id"], kind, cid))
    return {"added": len(added_ok), "removed": len(removed_ok), "failed": failed, "fades": fades or 0,
            "name": label}


def _update_library(db: Database, account_id: int, kind: str, cid: str, added: list[str], removed: list[str]) -> None:
    """The library shows the restored state right away, not only after its next scan."""
    src = db.val("SELECT id FROM catalog_sources WHERE account_id=? AND kind=? AND collection_id=?",
                 (account_id, kind, cid))
    if not src:
        return
    svc = db.val("SELECT service FROM accounts WHERE id=?", (account_id,))
    now = time.time()
    with db.tx() as c:
        for r in removed:
            c.execute("DELETE FROM catalog_items WHERE source_id=? AND ref_id=?", (src, r))
        for i, r in enumerate(added):
            song = c.execute("SELECT song_id FROM song_refs WHERE service=? AND ref_id=?", (svc, r)).fetchone()
            if song:
                c.execute("""INSERT OR IGNORE INTO catalog_items(source_id,ref_id,song_id,position,first_seen)
                             VALUES(?,?,?,?,?)""", (src, r, song[0], 2_000_000 + i, now))
        c.execute("UPDATE catalog_sources SET item_count=(SELECT COUNT(*) FROM catalog_items WHERE source_id=?) "
                  "WHERE id=?", (src, src))
