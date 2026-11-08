"""Tidying the library: duplicates, unusual versions, playlists, and video or song on YouTube.

Findings come from what the last scan (and the fades' runs) saw – nothing is asked from the services
to show them. Nothing changes unless the user confirms a suggestion.

Duplicates are resolved fade-safe: the songs are first merged into one identity, then only entries a
collection holds *twice* are removed, so every collection keeps the song and no fade sees a removal.
"""
from __future__ import annotations

import re
import time

from . import matcher
from .catalog import Catalog, _source_label, user_sources
from .db import Database
from .models import LIKED, PLAYLIST, SERVICE_NAMES, YTM, ApiError, AuthError, Track
from .providers.base import CopiesLost

VIDEO_TYPES = ("MUSIC_VIDEO_TYPE_OMV", "MUSIC_VIDEO_TYPE_UGC")
UNUSUAL = ("Karaoke", "Cover", "Sped up / Slowed")       # version tags that are rarely liked on purpose
DURATION_TOLERANCE = 4          # seconds; more apart is "check", not "likely"
SIMILAR = 0.8                   # playlists sharing this much are "almost the same"
MIN_PLAYLIST = 4                # smaller playlists are not compared
UNDO_KEY = "catalog_undo_{}"
SERVICE_ORDER = {"ytm": 0, "apple": 1, "spotify": 2}
RELEASES = ("album", "single")  # which release a duplicate keeps by default (users.release_pref)


def _is_video(v: dict) -> bool:
    return v.get("video_type") in VIDEO_TYPES


def _track(v: dict) -> Track:
    return Track(service=v["service"], id=v["ref_id"], title=v["title"] or "", artist=v["artist"] or "",
                 album=v["album"] or "", duration=v["duration"], isrc=v["isrc"],
                 explicit=None if v["explicit"] is None else bool(v["explicit"]), album_type=v["album_type"],
                 video_type=v["video_type"], artwork=v["artwork"])


def _vid(service: str, ref_id: str) -> str:
    return f"{service}:{ref_id}"


_SINGLE_SUFFIX = re.compile(r"\s[-–]\s(single|ep)\s*$", re.I)


def _release(v: dict) -> str | None:
    """Album or single, as far as the service tells – Spotify says it, Apple names singles
    "… - Single", elsewhere a single is named after its song. None when it can't be told:
    videos, entries without album, compilations (they rank on their own) and any other album name,
    which may just as well be a compilation ("GYM HYPE")."""
    if _is_video(v) or v["album_type"] == "compilation":
        return None
    if v["album_type"] in RELEASES:
        return v["album_type"]
    album = (v["album"] or "").strip()
    if not album:
        return None
    if _SINGLE_SUFFIX.search(album):
        return "single"
    same = matcher.norm_title(_SINGLE_SUFFIX.sub("", album)) == matcher.norm_title(v["title"] or "")
    return "single" if same else None


def user_release(db: Database, user_id: int) -> str:
    pref = db.val("SELECT release_pref FROM users WHERE id=?", (user_id,))
    return pref if pref in RELEASES else "album"


# ==================================================================== reading
def _variants(db: Database, user_id: int, song_ids: set[int] | None = None) -> dict[str, dict]:
    """Every service entry of the user's songs: (service, ref) -> details and where it is."""
    sql = """SELECT ci.source_id, ci.ref_id, ci.song_id, ci.copies, a.service, sr.title, sr.artist, sr.album,
                    sr.duration, sr.isrc, sr.explicit, sr.album_type, sr.video_type, sr.artwork,
                    so.title AS s_title, so.artist AS s_artist, so.album AS s_album, so.duration AS s_duration
             FROM catalog_items ci JOIN catalog_sources s ON s.id=ci.source_id JOIN accounts a ON a.id=s.account_id
             JOIN songs so ON so.id=ci.song_id
             LEFT JOIN song_refs sr ON sr.service=a.service AND sr.ref_id=ci.ref_id
             WHERE a.user_id=? AND s.included=1"""
    args: list = [user_id]
    if song_ids is not None:
        if not song_ids:
            return {}
        sql += f" AND ci.song_id IN ({','.join('?' * len(song_ids))})"
        args += list(song_ids)
    out: dict[str, dict] = {}
    for r in db.all(sql, args):
        key = _vid(r["service"], r["ref_id"])
        v = out.get(key)
        if v is None:
            v = out[key] = {
                "id": key, "service": r["service"], "ref_id": r["ref_id"], "song_id": r["song_id"],
                "title": r["title"] if r["title"] is not None else r["s_title"],
                "artist": r["artist"] if r["title"] is not None else r["s_artist"],
                "album": (r["album"] if r["title"] is not None else r["s_album"]) or "",
                "duration": r["duration"] if r["duration"] is not None else r["s_duration"],
                "isrc": r["isrc"], "explicit": r["explicit"], "album_type": r["album_type"],
                "video_type": r["video_type"], "artwork": r["artwork"], "sources": [], "copies": {}}
            ver = matcher.version(_track(v))
            v["hard"] = sorted(ver.hard)
            v["version"] = ver.label()
            v["compilation"] = ver.compilation
            v["soft"] = len(ver.soft)
            v["release"] = _release(v)
        v["sources"].append(r["source_id"])
        if r["copies"] > 1:
            v["copies"][r["source_id"]] = r["copies"]
    return out


def _fades_by_source(db: Database, user_id: int) -> dict[int, list[dict]]:
    rows = db.all("""SELECT s.id AS source_id, l.id, l.name, l.color, l.sync_deletions
                     FROM catalog_sources s JOIN accounts a ON a.id=s.account_id
                     JOIN line_members m ON m.account_id=s.account_id AND m.kind=s.kind
                          AND m.collection_id=s.collection_id
                     JOIN lines l ON l.id=m.line_id WHERE a.user_id=?""", (user_id,))
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r["source_id"], []).append({"id": r["id"], "name": r["name"]})
    return out


def _dismissed(db: Database, user_id: int) -> set[str]:
    return {r["key"] for r in db.all("SELECT key FROM cleanup_dismissed WHERE user_id=?", (user_id,))}


# ==================================================================== which entry stays
def _rank(v: dict, release: str = "album") -> tuple:
    """Default choice: the plain song (not a video, not a compilation), explicit if known, from the
    release the user prefers (album or single), the entry the user has in most collections, with an
    ISRC, then the oldest."""
    fits = 1 if v["release"] is None else 2 * (v["release"] == release)
    return (not _is_video(v), not v["hard"], {1: 2, None: 1, 0: 0}[v["explicit"]], not v["compilation"], fits,
            len(set(v["sources"])), -v["soft"], bool(v["isrc"]), -v["song_id"])


def _fits_format(v: dict, fmt: str | None) -> bool:
    return v["service"] != YTM or not fmt or _is_video(v) == (fmt == "video")


def plan(variants: list[dict], keep: dict, yt_format: str | None, release: str = "album") -> list[tuple[int, str]]:
    """Entries to remove so that each collection holds the song once. Per service the entry that
    stays is the chosen one, or the one closest to it (on YouTube in the user's preferred form)."""
    ref_track = _track(keep)
    choice: dict[str, str] = {}
    for svc in {v["service"] for v in variants}:
        if svc == keep["service"]:
            choice[svc] = keep["id"]
            continue
        cands = [v for v in variants if v["service"] == svc]
        choice[svc] = max(cands, key=lambda v: (_fits_format(v, yt_format), matcher.assess(ref_track, _track(v)).score,
                                                _rank(v, release)))["id"]
    by_source: dict[int, list[dict]] = {}
    for v in variants:
        for src in set(v["sources"]):
            by_source.setdefault(src, []).append(v)
    out: list[tuple[int, str]] = []
    for src, vs in sorted(by_source.items()):
        if len(vs) < 2:
            continue
        svc = vs[0]["service"]
        stays = next((v for v in vs if v["id"] == choice.get(svc)), None)
        if stays is None:
            stays = max(vs, key=lambda v: (_fits_format(v, yt_format), matcher.assess(ref_track, _track(v)).score,
                                           _rank(v, release)))
        out += [(src, v["ref_id"]) for v in vs if v is not stays]
    return out


# ==================================================================== findings
def _key_of(v: dict) -> tuple | None:
    title = matcher.norm_title(v["title"] or "")
    artists = matcher.artist_list(v["artist"] or "")
    if not title or not artists:
        return None
    return title, artists[0], tuple(v["hard"])


def _alike(a: Track, b: Track) -> bool:
    """The same recording as far as title, artist and length tell – version tags aside (soft ones
    are what duplicates differ in; hard ones are already part of the bucket key)."""
    if a.isrc and b.isrc and a.isrc.upper() == b.isrc.upper():
        return True
    wa, wb = _words(a.title), _words(b.title)
    if not wa or not wb or len(wa & wb) / len(wa | wb) < 0.75:
        return False
    return max(matcher.assess(a, b).score, matcher.assess(b, a).score) >= matcher.ACCEPT - 0.1


_NOISE_WORDS = {"official", "video", "audio", "music", "lyrics", "lyric", "visualizer", "hd", "4k", "version", "edit",
                "main", "radio", "remaster", "remastered", "extended", "explicit", "clean", "mono", "stereo"}


def _words(title: str) -> set[str]:
    """Words of the whole title without bracket and ' - ' qualifiers. Unlike the matcher's reading
    it keeps what follows a '|', where uploads name the episode ("Letztes JBB | Finale #2")."""
    t = re.sub(r"[(\[【][^)\]】]*[)\]】]", " ", title or "")
    t = re.sub(r"\s[-–]\s.*$", " ", t)
    return {w for w in matcher._base(t).split() if w not in _NOISE_WORDS}


def _duplicate_groups(variants: dict[str, dict], yt_format: str | None, dismissed: set[str],
                      release: str = "album") -> list[dict]:
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    # same bare title and first artist is only a candidate: two songs are joined when the matcher
    # also finds them alike (length, full title) – "Letztes JBB | Finale #1" and "… #2" are not
    buckets: dict[tuple, list[dict]] = {}
    for v in variants.values():
        find(v["song_id"])
        k = _key_of(v)
        if k is not None:
            buckets.setdefault(k, []).append(v)
    for vs in buckets.values():
        if len({v["song_id"] for v in vs}) < 2 or len(vs) > 40:
            continue
        tracks = [(v, _track(v)) for v in vs]
        for i, (a, ta) in enumerate(tracks):
            for b, tb in tracks[i + 1:]:
                if find(a["song_id"]) != find(b["song_id"]) and _alike(ta, tb):
                    parent[find(b["song_id"])] = find(a["song_id"])
    comps: dict[int, list[dict]] = {}
    for v in variants.values():
        comps.setdefault(find(v["song_id"]), []).append(v)

    groups = []
    for vs in comps.values():
        songs = sorted({v["song_id"] for v in vs})
        twice = any(len([v for v in vs if src in v["sources"]]) > 1 for src in {s for v in vs for s in v["sources"]})
        if len(songs) < 2 and not twice:
            continue
        key = "d:" + ",".join(map(str, songs))
        if key in dismissed:
            continue
        durations = [v["duration"] for v in vs if v["duration"] and not _is_video(v)]   # videos run longer
        isrcs = {(v["isrc"] or "").upper() for v in vs if not _is_video(v)}
        if len(songs) == 1:
            confidence = "same"
        elif (len(isrcs) == 1 and "" not in isrcs and sum(not _is_video(v) for v in vs) > 1) \
                or (len(durations) > 1 and max(durations) - min(durations) <= DURATION_TOLERANCE) \
                or (durations and len({v["version"] for v in vs}) == 1):
            confidence = "likely"
        else:
            confidence = "check"
        vs.sort(key=lambda v: (SERVICE_ORDER.get(v["service"], 9), not _rank(v)[0], v["version"]))
        keep = max(vs, key=lambda v: _rank(v, release))
        plans = {v["id"]: plan(vs, v, yt_format, release) for v in vs}
        lead = keep
        groups.append({
            "key": key, "confidence": confidence, "songs": songs, "title": lead["title"], "artist": lead["artist"],
            "artwork": next((v["artwork"] for v in vs if v["artwork"] and not _is_video(v)), lead["artwork"]),
            "keep": keep["id"],
            "variants": [{k: v[k] for k in ("id", "service", "ref_id", "song_id", "title", "artist", "album",
                                            "duration", "version", "release", "artwork")}
                         | {"video": _is_video(v), "explicit": v["explicit"], "sources": sorted(set(v["sources"]))}
                         for v in vs],
            "plans": {k: [list(x) for x in p] for k, p in plans.items()},
        })
    order = {"same": 0, "likely": 1, "check": 2}
    groups.sort(key=lambda g: (order[g["confidence"]], (g["artist"] or "").lower(), (g["title"] or "").lower()))
    return groups


def _playlists(db: Database, user_id: int, sources: list[dict], fades: dict[int, list[dict]],
               dismissed: set[str]) -> list[dict]:
    pls = [s for s in sources if s["kind"] == PLAYLIST and s["included"] and s["scanned_at"]]
    members: dict[int, set[int]] = {s["id"]: set() for s in pls}
    if pls:
        for r in db.all(f"SELECT source_id, song_id FROM catalog_items WHERE source_id IN "
                        f"({','.join('?' * len(pls))})", [s["id"] for s in pls]):
            members[r["source_id"]].add(r["song_id"])
    out: list[dict] = []

    def emit(kind: str, ids: list[int], **extra) -> None:
        key = f"p:{kind}:{','.join(map(str, ids))}"
        if key not in dismissed:
            out.append({"key": key, "kind": kind, "sources": ids, **extra})

    for s in pls:
        if not members[s["id"]]:
            emit("empty", [s["id"]])
    seen: set[tuple[int, int]] = set()
    for i, a in enumerate(pls):
        for b in pls[i + 1:]:
            if a["account_id"] != b["account_id"]:
                continue        # the same playlist on two services is what fades are for
            ma, mb = members[a["id"]], members[b["id"]]
            if len(ma) < MIN_PLAYLIST or len(mb) < MIN_PLAYLIST:
                continue
            common = len(ma & mb)
            small, big = (a, b) if len(ma) <= len(mb) else (b, a)
            if ma == mb:
                emit("identical", [small["id"], big["id"]], common=common)
            elif members[small["id"]] <= members[big["id"]]:
                emit("subset", [small["id"], big["id"]], common=common)
            elif common / len(ma | mb) >= SIMILAR:
                emit("similar", [small["id"], big["id"]], common=common,
                     only=[len(members[small["id"]] - members[big["id"]]), len(members[big["id"]] - members[small["id"]])])
            else:
                continue
            seen.add((a["id"], b["id"]))
    names: dict[tuple[int, str], list[dict]] = {}
    for s in pls:
        base = matcher._base(s["name"])
        base = " ".join(w for w in base.split() if not w.isdigit() and w not in ("copy", "kopie", "1", "2")) or base
        names.setdefault((s["account_id"], base), []).append(s)
    for group in names.values():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if (a["id"], b["id"]) not in seen and (b["id"], a["id"]) not in seen:
                    emit("same_name", sorted([a["id"], b["id"]]),
                         common=len(members[a["id"]] & members[b["id"]]))
    return out


def findings(db: Database, user_id: int) -> dict:
    variants = _variants(db, user_id)
    dismissed = _dismissed(db, user_id)
    sources = user_sources(db, user_id)
    fades = _fades_by_source(db, user_id)
    yt = db.one("SELECT id, yt_format, credentials IS NOT NULL AS connected FROM accounts WHERE user_id=? AND service=?",
                (user_id, YTM))
    yt_format = yt["yt_format"] if yt else None
    release = user_release(db, user_id)

    copies = [{"source_id": src, "ref_id": v["ref_id"], "song_id": v["song_id"], "copies": n, "title": v["title"],
               "artist": v["artist"], "artwork": v["artwork"]}
              for v in variants.values() for src, n in v["copies"].items()]
    copies.sort(key=lambda c: ((c["artist"] or "").lower(), (c["title"] or "").lower()))

    unusual: dict[int, dict] = {}
    for v in variants.values():
        tags = [t for t in v["hard"] if t in UNUSUAL]
        if tags and f"u:{v['song_id']}" not in dismissed:
            u = unusual.setdefault(v["song_id"], {"song_id": v["song_id"], "key": f"u:{v['song_id']}", "tags": tags,
                                                  "title": v["title"], "artist": v["artist"],
                                                  "artwork": v["artwork"], "sources": []})
            u["sources"] = sorted(set(u["sources"]) | set(v["sources"]))

    youtube = None
    if yt and yt["connected"]:
        editable = {s["id"]: s for s in sources if s["account_id"] == yt["id"] and s["editable"]
                    and s["kind"] in (LIKED, PLAYLIST)}
        none = {r["video_id"] for r in db.all("SELECT video_id FROM yt_counterparts WHERE counterpart=''")}
        cands, without = [], 0
        for v in variants.values():
            if v["service"] != YTM or _fits_format(v, yt_format):
                continue
            srcs = [s for s in set(v["sources"]) if s in editable]
            if not srcs:
                continue
            if v["ref_id"] in none:
                without += len(srcs)
                continue
            cands += [{"source_id": s, "ref_id": v["ref_id"], "song_id": v["song_id"], "title": v["title"],
                       "artist": v["artist"], "artwork": v["artwork"]} for s in srcs]
        youtube = {"account_id": yt["id"], "format": yt_format, "candidates": cands, "without": without}

    return {
        "duplicates": _duplicate_groups(variants, yt_format, dismissed, release),
        "release": release,
        "copies": copies,
        "unusual": sorted(unusual.values(), key=lambda u: ((u["artist"] or "").lower(), (u["title"] or "").lower())),
        "playlists": _playlists(db, user_id, sources, fades, dismissed),
        "youtube": youtube,
        "fades": {str(k): v for k, v in fades.items()},
        "dismissed": len(dismissed),
        "undo": db.setting(UNDO_KEY.format(user_id)) and {
            k: v for k, v in db.setting(UNDO_KEY.format(user_id)).items() if k in ("at", "action", "n")},
    }


# ==================================================================== actions
class Cleanup(Catalog):
    """The tidy-up actions. They run as catalog jobs (one at a time per user, never during a fade run)."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.undo_removed: list[tuple[int, dict]] = []     # (source id, track) – to add back
        self.undo_added: list[tuple[int, dict]] = []       # (source id, track) – to take out again
        self.problems: list[str] = []

    # ------------------------------------------------------------ helpers
    def _source(self, source_id: int) -> dict | None:
        s = user_sources(self.db, self.user_id, [source_id])
        return s[0] if s else None

    def _provider(self, src: dict):
        prov = self.providers.get(src["account_id"])
        if prov is None or src["account_id"] in self.auth_errors:
            self.problems.append(f"{SERVICE_NAMES[src['service']]} is not connected.")
            return None
        if not src["editable"]:
            self.problems.append(f"“{src['name']}” can't be edited.")
            return None
        return prov

    def _read(self, src: dict, prov) -> list[Track] | None:
        try:
            return prov.read(src["kind"], src["collection_id"])
        except AuthError as e:
            self.auth_errors[src["account_id"]] = str(e)
            self.problems.append(f"{SERVICE_NAMES[src['service']]}: {e}")
        except ApiError as e:
            self.problems.append(f"{_source_label(src)}: {e}")
        return None

    def _recount(self, source_id: int) -> None:
        self.db.run("UPDATE catalog_sources SET item_count=(SELECT COUNT(*) FROM catalog_items WHERE source_id=?) "
                    "WHERE id=?", (source_id, source_id))

    def _remove_twins(self, src: dict, refs: set[str]) -> tuple[int, int]:
        """Remove entries of songs that stay in the collection under another entry. Checked against a
        fresh read: an entry is only removed when another entry of the same song is listed now."""
        prov = self._provider(src)
        if prov is None:
            return 0, len(refs)
        current = self._read(src, prov)
        if current is None:
            return 0, len(refs)
        song_of = {t.id: self.ident.song_for(t) for t in current}
        stays: dict[int, int] = {}
        for ref, sid in song_of.items():
            if ref not in refs:
                stays[sid] = stays.get(sid, 0) + 1
        todo = [t for t in current if t.id in refs and stays.get(song_of[t.id])]
        skipped = len(refs) - len(todo)
        if skipped:
            self.problems.append(f"{_source_label(src)}: {skipped} skipped because the collection changed.")
        if not todo:
            return 0, skipped
        self.progress(f"Removing {len(todo)} duplicate entries from {_source_label(src)} …", 0, 0)
        try:
            bad = set(prov.remove(src["kind"], src["collection_id"], todo))
        except AuthError as e:
            self.auth_errors[src["account_id"]] = str(e)
            self.problems.append(f"{SERVICE_NAMES[src['service']]}: {e}")
            return 0, len(refs)
        except ApiError as e:
            self.problems.append(f"{_source_label(src)}: {e}")
            return 0, len(refs)
        ok = [t for t in todo if t.id not in bad]
        self.db.many("DELETE FROM catalog_items WHERE source_id=? AND ref_id=?", [(src["id"], t.id) for t in ok])
        self._recount(src["id"])
        self.undo_removed += [(src["id"], t.to_dict()) for t in ok]
        return len(ok), len(bad) + skipped

    def _save_undo(self, action: str) -> None:
        n = len(self.undo_removed) + len(self.undo_added)
        if n:
            self.db.set_setting(UNDO_KEY.format(self.user_id), {
                "at": time.time(), "action": action, "n": n,
                "removed": self.undo_removed, "added": self.undo_added})

    # ------------------------------------------------------------ duplicates
    def dedupe(self, groups: list[dict], copies: list[list]) -> dict:
        removed = failed = merged = 0
        removals: dict[int, set[str]] = {}
        yt_format = self.db.val("SELECT yt_format FROM accounts WHERE user_id=? AND service=?", (self.user_id, YTM))
        release = user_release(self.db, self.user_id)
        for i, g in enumerate(groups):
            self._check_stop()
            service, ref = g["keep"]
            keep_song = self.db.val("SELECT song_id FROM song_refs WHERE service=? AND ref_id=?", (service, ref))
            if keep_song is None or keep_song not in g["songs"]:
                self.problems.append("A suggestion is out of date. Please scan again.")
                continue
            self.progress("Merging identical songs …", i, len(groups))
            for sid in g["songs"]:
                if sid != keep_song and self.db.val("SELECT 1 FROM songs WHERE id=?", (sid,)):
                    self.ident.merge(keep_song, sid)
                    merged += 1
            vs = list(_variants(self.db, self.user_id, {keep_song}).values())
            keep = next((v for v in vs if v["id"] == _vid(service, ref)), None)
            if keep is None:
                continue
            for src, r in plan(vs, keep, yt_format, release):
                removals.setdefault(src, set()).add(r)
        for source_id, refs in sorted(removals.items()):
            self._check_stop()
            src = self._source(source_id)
            if not src:
                continue
            ok, bad = self._remove_twins(src, refs)
            removed += ok
            failed += bad
        fixed = 0
        by_source: dict[int, set[str]] = {}
        for source_id, ref in copies:
            by_source.setdefault(source_id, set()).add(ref)
        for source_id, refs in sorted(by_source.items()):
            self._check_stop()
            src = self._source(source_id)
            prov = self._provider(src) if src else None
            if prov is None:
                failed += len(refs)
                continue
            current = self._read(src, prov)
            if current is None:
                failed += len(refs)
                continue
            for t in current:
                if t.id not in refs or not t.handle.get("extra"):
                    continue
                self.progress(f"Removing duplicate entries from {_source_label(src)} …", 0, 0)
                try:
                    prov.remove_copies(src["kind"], src["collection_id"], t)
                except CopiesLost as e:
                    self.undo_removed.append((src["id"], t.to_dict()))
                    self.problems.append(f"{t.label()}: {e} – try again with “Undo”.")
                    failed += 1
                    continue
                except ApiError as e:
                    self.problems.append(f"{t.label()}: {e}")
                    failed += 1
                    continue
                self.db.run("UPDATE catalog_items SET copies=1 WHERE source_id=? AND ref_id=?", (src["id"], t.id))
                fixed += t.handle.get("copies", 2) - 1
        self._save_undo("dedupe")
        return {"removed": removed + fixed, "merged": merged, "failed": failed, "errors": self.problems}

    # ------------------------------------------------------------ YouTube: video or song
    def swap(self, items: list[list]) -> dict:
        swapped = failed = without = 0
        by_source: dict[int, list[str]] = {}
        for source_id, ref in items:
            by_source.setdefault(source_id, []).append(ref)
        done, total = 0, len(items)
        for source_id, refs in sorted(by_source.items()):
            self._check_stop()
            src = self._source(source_id)
            prov = self._provider(src) if src else None
            if prov is None or src["service"] != YTM or src["kind"] not in (LIKED, PLAYLIST):
                failed += len(refs)
                continue
            fmt = src.get("yt_format") or "song"
            there = {r["ref_id"] for r in self.db.all("SELECT ref_id FROM catalog_items WHERE source_id=?", (src["id"],))}
            pairs: list[tuple[int, str, Track]] = []      # song, old ref, new track
            for ref in refs:
                self._check_stop()
                done += 1
                row = self.db.one("SELECT * FROM song_refs WHERE service=? AND ref_id=?", (YTM, ref))
                if not row:
                    failed += 1
                    continue
                old = self.ident._track(row)
                self.progress(f"Looking for the {'music video' if fmt == 'video' else 'song'} of “{old.label()}” …", done, total)
                try:
                    new, _ = self.ident.in_format(row["song_id"], old, prov, fmt)
                except AuthError as e:
                    self.auth_errors[src["account_id"]] = str(e)
                    raise
                if new.id == old.id:
                    without += 1
                    continue
                pairs.append((self.db.val("SELECT song_id FROM song_refs WHERE service=? AND ref_id=?", (YTM, ref)),
                              ref, new))
            if not pairs:
                continue
            to_add = [new for _, _, new in pairs if new.id not in there]
            bad: set[str] = set()
            if to_add:
                self.progress(f"Adding {len(to_add)} songs to {_source_label(src)} …", done, total)
                try:
                    bad = set(prov.add(src["kind"], src["collection_id"], to_add))
                except ApiError as e:
                    self.problems.append(f"{_source_label(src)}: {e}")
                    bad = {t.id for t in to_add}
            now = time.time()
            ok_pairs = [(sid, ref, new) for sid, ref, new in pairs if new.id not in bad]
            failed += len(pairs) - len(ok_pairs)
            for sid, _, new in ok_pairs:
                if new.id not in there:
                    self.db.run("""INSERT INTO catalog_items(source_id,ref_id,song_id,position,first_seen)
                                   VALUES(?,?,?,?,?) ON CONFLICT(source_id,ref_id) DO NOTHING""",
                                (src["id"], new.id, sid, 1_000_000, now))
                    self.undo_added.append((src["id"], new.to_dict()))
            ok, bad_rm = self._remove_twins(src, {ref for _, ref, _ in ok_pairs})
            swapped += ok
            failed += bad_rm
        self._save_undo("swap")
        return {"swapped": swapped, "without": without, "failed": failed, "errors": self.problems}

    # ------------------------------------------------------------ playlists
    def playlists(self, delete: list[int], merge: list[list[int]]) -> dict:
        deleted = moved = failed = 0
        for from_id, into_id in merge:
            self._check_stop()
            songs = [r["song_id"] for r in self.db.all(
                "SELECT song_id FROM catalog_items WHERE source_id=? AND song_id NOT IN "
                "(SELECT song_id FROM catalog_items WHERE source_id=?) ORDER BY position", (from_id, into_id))]
            if songs:
                try:
                    res = self.add(songs, source_id=into_id)
                except ApiError as e:
                    self.problems.append(str(e))
                    failed += 1
                    continue
                moved += res["added"]
                if res["failed"] or res["missing"]:
                    src = self._source(from_id)
                    self.problems.append(f"“{src['name'] if src else from_id}” stays: "
                                         f"{res['failed'] + len(res['missing'])} songs couldn't be moved.")
                    failed += 1
                    continue
            if from_id not in delete:
                delete = [*delete, from_id]
        for source_id in delete:
            self._check_stop()
            src = self._source(source_id)
            prov = self._provider(src) if src else None
            if prov is None:
                failed += 1
                continue
            self.progress(f"Deleting {_source_label(src)} …", 0, 0)
            try:
                prov.delete_playlist(src["collection_id"])
            except AuthError as e:
                self.auth_errors[src["account_id"]] = str(e)
                self.problems.append(f"{SERVICE_NAMES[src['service']]}: {e}")
                failed += 1
                continue
            except ApiError as e:
                self.problems.append(f"“{src['name']}”: {e}")
                failed += 1
                continue
            self.db.run("DELETE FROM catalog_sources WHERE id=?", (src["id"],))
            deleted += 1
        return {"deleted": deleted, "moved": moved, "failed": failed, "errors": self.problems}

    # ------------------------------------------------------------ undo
    def undo(self) -> dict:
        rec = self.db.setting(UNDO_KEY.format(self.user_id))
        if not rec:
            raise ApiError("There's nothing to undo.")
        restored = failed = 0
        groups: dict[int, list[Track]] = {}
        for source_id, d in rec.get("removed", []):
            groups.setdefault(source_id, []).append(Track.from_dict(d))
        now = time.time()
        for source_id, tracks in groups.items():
            self._check_stop()
            src = self._source(source_id)
            prov = self._provider(src) if src else None
            if prov is None:
                failed += len(tracks)
                continue
            self.progress(f"Restoring {len(tracks)} songs to {_source_label(src)} …", 0, 0)
            try:
                bad = set(prov.add(src["kind"], src["collection_id"], tracks))
            except ApiError as e:
                self.problems.append(f"{_source_label(src)}: {e}")
                failed += len(tracks)
                continue
            for t in tracks:
                if t.id in bad:
                    continue
                sid = self.ident.song_for(t)
                self.db.run("""INSERT INTO catalog_items(source_id,ref_id,song_id,position,first_seen)
                               VALUES(?,?,?,?,?) ON CONFLICT(source_id,ref_id) DO NOTHING""",
                            (src["id"], t.id, sid, 1_000_000, now))
            restored += len(tracks) - len(bad)
            failed += len(bad)
            self._recount(src["id"])
        taken: dict[int, set[str]] = {}
        for source_id, d in rec.get("added", []):
            taken.setdefault(source_id, set()).add(d["id"])
        for source_id, refs in taken.items():
            self._check_stop()
            src = self._source(source_id)
            prov = self._provider(src) if src else None
            if prov is None:
                failed += len(refs)
                continue
            ok, bad = self._remove_twins(src, refs)
            restored += ok
            failed += bad
        self.db.set_setting(UNDO_KEY.format(self.user_id), None)
        return {"restored": restored, "failed": failed, "errors": self.problems}
