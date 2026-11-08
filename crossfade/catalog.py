"""Music catalog: every song a user has in their collections, across services.

A scan reads the included collections of every connected service (likes, library, playlists),
maps the tracks to global songs and merges identical songs across services. Bulk actions remove
songs from chosen collections or add them to one collection (searching the target service).

Lines are not touched directly: they read the services themselves on their next run, so a removal
here is passed on (or respected) exactly like a removal the user made in the service's own app.
"""
from __future__ import annotations

import time
from collections.abc import Callable

from . import matcher
from .db import Database
from .identity import Identity
from .models import LIBRARY, LIKED, PLAYLIST, SERVICE_NAMES, ApiError, AuthError, Track
from .providers.base import Provider

Progress = Callable[[str, int, int], None]
KIND_ORDER = {LIKED: 0, LIBRARY: 1, PLAYLIST: 2}


class Stopped(Exception):
    pass


def _source_label(src: dict) -> str:
    return f"{SERVICE_NAMES[src['service']]} · {src['name']}"


def user_sources(db: Database, user_id: int, ids: list[int] | None = None) -> list[dict]:
    sql = """SELECT s.*, a.service, a.yt_format FROM catalog_sources s JOIN accounts a ON a.id=s.account_id
             WHERE a.user_id=?"""
    args: list = [user_id]
    if ids is not None:
        if not ids:
            return []
        sql += f" AND s.id IN ({','.join('?' * len(ids))})"
        args += ids
    return db.all(sql + " ORDER BY a.service, s.kind='playlist', s.name COLLATE NOCASE", args)


class Catalog:
    def __init__(self, db: Database, user_id: int, providers: dict[int, Provider],
                 progress: Progress | None = None, should_stop: Callable[[], bool] | None = None):
        self.db = db
        self.user_id = user_id
        self.providers = providers            # account_id -> provider (connected accounts only)
        self.ident = Identity(db)
        self.progress = progress or (lambda *a: None)
        self.should_stop = should_stop or (lambda: False)
        self.auth_errors: dict[int, str] = {}   # account_id -> message

    def _check_stop(self) -> None:
        if self.should_stop():
            raise Stopped()

    # ================================================================ scan
    def refresh_sources(self) -> None:
        """Bring the list of collections up to date. New ones are included; gone ones are dropped."""
        db = self.db
        for acc_id, prov in self.providers.items():
            self._check_stop()
            try:
                cols = prov.collections()
            except AuthError as e:
                self.auth_errors[acc_id] = str(e)
                continue
            except ApiError:
                continue          # keep the known sources; reading them reports the error
            seen = set()
            for c in cols:
                seen.add((c.kind, c.id))
                db.run("""INSERT INTO catalog_sources(account_id,kind,collection_id,name,editable,item_count)
                          VALUES(?,?,?,?,?,?) ON CONFLICT(account_id,kind,collection_id) DO UPDATE SET
                          name=excluded.name, editable=excluded.editable,
                          item_count=COALESCE(excluded.item_count, item_count)""",
                       (acc_id, c.kind, c.id, c.name, int(c.editable), c.count))
            for s in db.all("SELECT id, kind, collection_id FROM catalog_sources WHERE account_id=?", (acc_id,)):
                if (s["kind"], s["collection_id"]) not in seen:
                    db.run("DELETE FROM catalog_sources WHERE id=?", (s["id"],))

    def scan(self) -> dict:
        db, now = self.db, time.time()
        self.progress("Loading collections …", 0, 0)
        self.refresh_sources()
        sources = [s for s in user_sources(db, self.user_id)
                   if s["included"] and s["account_id"] in self.providers and s["account_id"] not in self.auth_errors]
        sources.sort(key=lambda s: (s["service"], KIND_ORDER.get(s["kind"], 3), s["name"].lower()))
        by_service: dict[str, dict[int, Track]] = {}
        errors: list[str] = []
        read = 0
        for i, src in enumerate(sources):
            self._check_stop()
            self.progress(f"Reading {_source_label(src)} …", i, len(sources))
            prov = self.providers[src["account_id"]]
            try:
                tracks = prov.read(src["kind"], src["collection_id"])
            except AuthError as e:
                self.auth_errors[src["account_id"]] = str(e)
                errors.append(f"{SERVICE_NAMES[src['service']]}: {e}")
                continue
            except ApiError as e:
                db.run("UPDATE catalog_sources SET error=? WHERE id=?", (str(e), src["id"]))
                errors.append(f"{_source_label(src)}: {e}")
                continue
            rows = []
            pool = by_service.setdefault(src["service"], {})
            for pos, t in enumerate(tracks):
                sid = self.ident.song_for(t)
                pool.setdefault(sid, t)
                rows.append((src["id"], t.id, sid, pos, now, t.handle.get("copies", 1)))
            with db.tx() as c:
                c.execute("CREATE TEMP TABLE IF NOT EXISTS _cat_keep(ref_id TEXT PRIMARY KEY)")
                c.execute("DELETE FROM _cat_keep")
                c.executemany("INSERT OR IGNORE INTO _cat_keep(ref_id) VALUES(?)", [(r[1],) for r in rows])
                c.execute("DELETE FROM catalog_items WHERE source_id=? AND ref_id NOT IN (SELECT ref_id FROM _cat_keep)",
                          (src["id"],))
                c.executemany("""INSERT INTO catalog_items(source_id,ref_id,song_id,position,first_seen,copies)
                                 VALUES(?,?,?,?,?,?) ON CONFLICT(source_id,ref_id) DO UPDATE SET
                                 song_id=excluded.song_id, position=excluded.position, copies=excluded.copies""", rows)
                c.execute("UPDATE catalog_sources SET item_count=?, scanned_at=?, error=NULL WHERE id=?",
                          (len(rows), now, src["id"]))
            read += 1
        self._check_stop()
        merged = self._merge(by_service)
        songs = db.val("""SELECT COUNT(DISTINCT ci.song_id) FROM catalog_items ci
                          JOIN catalog_sources s ON s.id=ci.source_id JOIN accounts a ON a.id=s.account_id
                          WHERE a.user_id=? AND s.included=1""", (self.user_id,))
        self.progress("Done", len(sources), len(sources))
        return {"collections": read, "songs": songs, "merged": merged, "errors": errors}

    def _merge(self, by_service: dict[str, dict[int, Track]]) -> int:
        """Identify the same song across services. Only identical versions are merged – the
        identity is shared with the lines, and a line set to "exact" must be able to rely on it.
        Besides those: a YouTube upload credited to its channel instead of the artist, when nothing
        else of that title and length exists on either side (matcher.pair_renamed)."""
        services = sorted(by_service)
        merged = 0
        for ai, a_svc in enumerate(services):
            for b_svc in services[ai + 1:]:
                self.progress(f"Matching songs between {SERVICE_NAMES[a_svc]} and {SERVICE_NAMES[b_svc]} …", 0, 0)
                has_b = {r["song_id"] for r in self.db.all("SELECT DISTINCT song_id FROM song_refs WHERE service=?",
                                                           (b_svc,))}
                has_a = {r["song_id"] for r in self.db.all("SELECT DISTINCT song_id FROM song_refs WHERE service=?",
                                                           (a_svc,))}
                left = {sid: t for sid, t in by_service[a_svc].items() if sid not in has_b}
                right = {sid: t for sid, t in by_service[b_svc].items() if sid not in has_a}
                lids = {id(v): k for k, v in left.items()}
                rids = {id(v): k for k, v in right.items()}
                pairs = [(lt, rt) for lt, rt, _ in
                         matcher.pair_lists(list(left.values()), list(right.values()), require_exact=True)]
                paired = {id(t) for p in pairs for t in p}
                pairs += matcher.pair_renamed([t for t in left.values() if id(t) not in paired],
                                              [t for t in right.values() if id(t) not in paired],
                                              list(by_service[a_svc].values()), list(by_service[b_svc].values()))
                for lt, rt in pairs:
                    keep, drop = lids[id(lt)], rids[id(rt)]
                    if keep != drop:
                        self.ident.merge(keep, drop)
                        by_service[b_svc][keep] = by_service[b_svc].pop(drop, rt)
                        merged += 1
        return merged

    # ================================================================ remove
    def remove(self, song_ids: set[int], source_ids: list[int]) -> dict:
        db = self.db
        sources = user_sources(db, self.user_id, source_ids)
        removed = failed = 0
        problems: list[str] = []
        for i, src in enumerate(sources):
            self._check_stop()
            refs = {r["ref_id"]: r["song_id"] for r in db.all(
                f"SELECT ref_id, song_id FROM catalog_items WHERE source_id=? AND song_id IN ({','.join('?' * len(song_ids))})",
                (src["id"], *song_ids))}
            if not refs:
                continue
            prov = self.providers.get(src["account_id"])
            if prov is None or src["account_id"] in self.auth_errors:
                failed += len(refs)
                problems.append(f"{SERVICE_NAMES[src['service']]} is not connected.")
                continue
            if not src["editable"]:
                failed += len(refs)
                problems.append(f"“{src['name']}” can't be edited.")
                continue
            self.progress(f"Removing {len(refs)} songs from {_source_label(src)} …", i, len(sources))
            try:
                # removing needs the collection's own handles (library ids, playlist entry ids …)
                current = [t for t in prov.read(src["kind"], src["collection_id"]) if t.id in refs]
                bad = set(prov.remove(src["kind"], src["collection_id"], current)) if current else set()
            except AuthError as e:
                self.auth_errors[src["account_id"]] = str(e)
                failed += len(refs)
                problems.append(f"{SERVICE_NAMES[src['service']]}: {e}")
                continue
            except ApiError as e:
                failed += len(refs)
                problems.append(f"{_source_label(src)}: {e}")
                continue
            ok = [r for r in refs if r not in bad]
            db.many("DELETE FROM catalog_items WHERE source_id=? AND ref_id=?", [(src["id"], r) for r in ok])
            db.run("UPDATE catalog_sources SET item_count=(SELECT COUNT(*) FROM catalog_items WHERE source_id=?) "
                   "WHERE id=?", (src["id"], src["id"]))
            removed += len(ok)          # already gone there counts as removed, too
            failed += len(bad)
            if bad:
                problems.append(f"{_source_label(src)}: {len(bad)} couldn't be removed.")
        return {"removed": removed, "failed": failed, "errors": problems}

    # ================================================================ add
    def add(self, song_ids: list[int], source_id: int | None = None, account_id: int | None = None,
            new_playlist: str | None = None) -> dict:
        db = self.db
        if new_playlist:
            prov = self.providers.get(account_id or -1)
            if prov is None:
                raise ApiError("This service is not connected.")
            self.progress(f"Creating playlist “{new_playlist}” …", 0, 0)
            col = prov.create_playlist(new_playlist)
            db.run("""INSERT INTO catalog_sources(account_id,kind,collection_id,name,editable,item_count,scanned_at)
                      VALUES(?,?,?,?,1,0,?) ON CONFLICT(account_id,kind,collection_id) DO UPDATE SET name=excluded.name""",
                   (account_id, PLAYLIST, col.id, col.name, time.time()))
            source_id = db.val("SELECT id FROM catalog_sources WHERE account_id=? AND kind=? AND collection_id=?",
                               (account_id, PLAYLIST, col.id))
        src = (user_sources(db, self.user_id, [source_id]) if source_id else [None])[0]
        if not src:
            raise ApiError("Collection not found.")
        prov = self.providers.get(src["account_id"])
        if prov is None:
            raise ApiError(f"{SERVICE_NAMES[src['service']]} is not connected.")
        service = src["service"]
        there = {r["song_id"] for r in db.all("SELECT song_id FROM catalog_items WHERE source_id=?", (src["id"],))}
        todo = [sid for sid in dict.fromkeys(song_ids) if sid not in there]
        found: dict[int, Track] = {}
        missing: list[dict] = []
        for i, sid in enumerate(todo):
            self._check_stop()
            desc = self.ident.describe(sid)
            self.progress(f"Searching {SERVICE_NAMES[service]} for “{desc.label()}” …", i, len(todo))
            match = self.ident.find(sid, service, prov)
            if match.accepted("closest"):
                found[sid], _ = self.ident.in_format(sid, match.track, prov, src.get("yt_format"))
            else:
                missing.append({"song_id": sid, "title": desc.title, "artist": desc.artist})
        failed: set[str] = set()
        if found:
            self._check_stop()
            self.progress(f"Adding {len(found)} songs to {_source_label(src)} …", len(todo), len(todo))
            failed = set(prov.add(src["kind"], src["collection_id"], list(found.values())))
        added = 0
        now = time.time()
        for sid, t in found.items():
            if t.id in failed:
                continue
            self.ident.link(sid, t)    # if the track belonged to another song, that one is merged into sid
            db.run("""INSERT INTO catalog_items(source_id,ref_id,song_id,position,first_seen) VALUES(?,?,?,?,?)
                      ON CONFLICT(source_id,ref_id) DO NOTHING""", (src["id"], t.id, sid, 1_000_000 + added, now))
            added += 1
        db.run("UPDATE catalog_sources SET item_count=(SELECT COUNT(*) FROM catalog_items WHERE source_id=?) "
               "WHERE id=?", (src["id"], src["id"]))
        return {"added": added, "already": len(song_ids) - len(todo), "failed": len(failed),
                "missing": missing, "target": _source_label(src), "source_id": src["id"]}
