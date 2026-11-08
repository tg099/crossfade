"""Music catalog: all songs of the user across services, with bulk actions."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from .. import matcher
from ..catalog import user_sources
from ..cleanup import UNDO_KEY, findings
from ..db import Database
from ..identity import Identity
from ..sharing import VISIBLE
from ..worker import get_worker
from .deps import current_user, db_dep

router = APIRouter(prefix="/api/catalog", tags=["catalog"])

MAX_SONGS = 20000
VIDEO_TYPES = matcher.VIDEO_TYPES


def _sources(db: Database, user_id: int) -> list[dict]:
    rows = user_sources(db, user_id)
    members = db.all(f"""SELECT m.account_id, m.kind, m.collection_id, m.line_id FROM line_members m
                         JOIN lines l ON l.id=m.line_id WHERE {VISIBLE}""", (user_id, user_id))
    by_key: dict[tuple, list[int]] = {}
    for m in members:
        by_key.setdefault((m["account_id"], m["kind"], m["collection_id"]), []).append(m["line_id"])
    return [{
        "id": r["id"], "account_id": r["account_id"], "service": r["service"], "kind": r["kind"],
        "collection_id": r["collection_id"], "name": r["name"], "editable": bool(r["editable"]),
        "included": bool(r["included"]), "item_count": r["item_count"], "scanned_at": r["scanned_at"],
        "error": r["error"], "lines": by_key.get((r["account_id"], r["kind"], r["collection_id"]), []),
    } for r in rows]


def _catalog_song_ids(db: Database, user_id: int) -> set[int]:
    return {r["song_id"] for r in db.all(
        """SELECT DISTINCT ci.song_id FROM catalog_items ci JOIN catalog_sources s ON s.id=ci.source_id
           JOIN accounts a ON a.id=s.account_id WHERE a.user_id=?""", (user_id,))}


@router.get("")
def catalog(user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    """The whole catalog in one compact payload – filtering and sorting happen in the browser."""
    uid = user["id"]
    items = db.all("""SELECT ci.song_id, ci.source_id, ci.ref_id, ci.first_seen, sr.artwork, sr.explicit,
                      sr.video_type, sr.album
                      FROM catalog_items ci JOIN catalog_sources s ON s.id=ci.source_id
                      JOIN accounts a ON a.id=s.account_id
                      LEFT JOIN song_refs sr ON sr.service=a.service AND sr.ref_id=ci.ref_id
                      WHERE a.user_id=? AND s.included=1 ORDER BY ci.source_id, ci.position""", (uid,))
    songs: dict[int, dict] = {}
    for r in items:
        s = songs.get(r["song_id"])
        if s is None:
            s = songs[r["song_id"]] = {"id": r["song_id"], "c": None, "x": False, "v": True, "al": "",
                                       "f": r["first_seen"], "in": []}
        s["in"].append([r["source_id"], r["ref_id"]])
        s["f"] = min(s["f"], r["first_seen"])
        s["c"] = s["c"] or r["artwork"]
        s["x"] = s["x"] or bool(r["explicit"])
        s["v"] = s["v"] and r["video_type"] in VIDEO_TYPES
        s["al"] = s["al"] or (r["album"] or "")
    ids = list(songs)
    for i in range(0, len(ids), 900):
        part = ids[i:i + 900]
        for r in db.all(f"SELECT id, title, artist, album, duration FROM songs WHERE id IN ({','.join('?' * len(part))})",
                        part):
            s = songs[r["id"]]
            s.update(t=r["title"], a=r["artist"], d=r["duration"])
            s["al"] = r["album"] or s["al"]
    lines = db.all(f"""SELECT l.id, l.name, l.color FROM lines l WHERE {VISIBLE}
                       ORDER BY l.user_id<>?, CASE WHEN l.user_id=? THEN l.position ELSE 0 END, l.id""",
                   (uid, uid, uid, uid))
    for n, line in enumerate(lines, 1):
        line["number"] = n
        line["songs"] = [r["song_id"] for r in db.all("SELECT song_id FROM line_items WHERE line_id=?", (line["id"],))
                         if r["song_id"] in songs]
    return {
        "sources": _sources(db, uid),
        "songs": [s for s in songs.values() if "t" in s],
        "lines": lines,
        "scanned_at": db.setting(f"catalog_scanned_{uid}"),
        "job": get_worker().catalog_job(uid),
    }


@router.get("/songs/{song_id}")
def song(song_id: int, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    """Where a song is, in which version, and in which lines."""
    uid = user["id"]
    rows = db.all("""SELECT sr.*, ci.source_id, ci.ref_id AS rid, ci.first_seen, s.name, s.kind, a.service AS svc
                     FROM catalog_items ci JOIN catalog_sources s ON s.id=ci.source_id
                     JOIN accounts a ON a.id=s.account_id
                     LEFT JOIN song_refs sr ON sr.service=a.service AND sr.ref_id=ci.ref_id
                     WHERE a.user_id=? AND ci.song_id=?""", (uid, song_id))
    if not rows:
        raise HTTPException(404, "Song is not in your library.")
    origin = Identity(db).describe(song_id)
    refs: dict[tuple, dict] = {}
    for r in rows:
        key = (r["svc"], r["rid"])
        if key not in refs:
            t = Identity._track(r) if r["ref_id"] is not None else None
            info = {"service": r["svc"], "ref_id": r["rid"], "track": t.to_dict() if t else None,
                    "version": matcher.version(t).label() if t else None, "notes": [], "collections": []}
            if t and not (t.service == origin.service and t.id == origin.id):
                info["notes"] = matcher.assess(origin, t).notes
            refs[key] = info
        refs[key]["collections"].append({"source_id": r["source_id"], "name": r["name"], "kind": r["kind"],
                                         "first_seen": r["first_seen"]})
    lines = db.all(f"""SELECT l.id, l.name, l.color FROM line_items li JOIN lines l ON l.id=li.line_id
                       WHERE {VISIBLE} AND li.song_id=? ORDER BY l.position, l.id""", (uid, uid, song_id))
    return {"song_id": song_id, "origin": origin.to_dict(), "refs": list(refs.values()), "lines": lines}


class SourceBody(BaseModel):
    included: bool


@router.put("/sources/{source_id}")
def set_source(source_id: int, body: SourceBody, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    if not user_sources(db, user["id"], [source_id]):
        raise HTTPException(404, "Collection not found.")
    db.run("UPDATE catalog_sources SET included=? WHERE id=?", (int(body.included), source_id))
    return {"ok": True}


def _start(user_id: int, action: str, params: dict | None = None) -> dict:
    if not get_worker().start_catalog(user_id, action, params):
        raise HTTPException(409, "Something is already running in your library. Wait until it's done.")
    return {"ok": True}


@router.post("/scan")
def scan(user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    if not db.val("SELECT 1 FROM accounts WHERE user_id=? AND credentials IS NOT NULL", (user["id"],)):
        raise HTTPException(400, "Connect a service first.")
    return _start(user["id"], "scan")


@router.post("/stop")
def stop(user: dict = Depends(current_user)):
    get_worker().stop_catalog(user["id"])
    return {"ok": True}


class RemoveBody(BaseModel):
    song_ids: list[int] = Field(min_length=1, max_length=MAX_SONGS)
    source_ids: list[int] = Field(min_length=1, max_length=500)


@router.post("/remove")
def remove(body: RemoveBody, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    sources = user_sources(db, user["id"], body.source_ids)
    if len(sources) != len(set(body.source_ids)):
        raise HTTPException(404, "Collection not found.")
    locked = [s["name"] for s in sources if not s["editable"]]
    if locked:
        raise HTTPException(400, f"“{locked[0]}” can't be edited.")
    return _start(user["id"], "remove", {"song_ids": body.song_ids, "source_ids": [s["id"] for s in sources]})


class AddBody(BaseModel):
    song_ids: list[int] = Field(min_length=1, max_length=MAX_SONGS)
    source_id: int | None = None
    account_id: int | None = None
    new_playlist: str | None = Field(None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def _target(self):
        if not self.source_id and not (self.account_id and self.new_playlist and self.new_playlist.strip()):
            raise ValueError("Target missing")
        return self


@router.post("/add")
def add(body: AddBody, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    uid = user["id"]
    if body.source_id:
        src = user_sources(db, uid, [body.source_id])
        if not src:
            raise HTTPException(404, "Collection not found.")
        if not src[0]["editable"]:
            raise HTTPException(400, f"“{src[0]['name']}” can't be edited.")
    else:
        acc = db.one("SELECT * FROM accounts WHERE id=? AND user_id=? AND credentials IS NOT NULL",
                     (body.account_id, uid))
        if not acc:
            raise HTTPException(400, "Service is not connected.")
    # only songs this user actually has (song ids are shared by all users)
    own = _catalog_song_ids(db, uid)
    ids = [i for i in body.song_ids if i in own]
    if not ids:
        raise HTTPException(400, "None of these songs are in your library.")
    return _start(uid, "add", {"song_ids": ids, "source_id": body.source_id,
                               "account_id": None if body.source_id else body.account_id,
                               "new_playlist": None if body.source_id else body.new_playlist.strip()})


# ---------------------------------------------------------------------- tidying up
@router.get("/cleanup")
def cleanup(user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    """Suggestions to tidy the library (duplicates, unusual versions, playlists, YouTube format)."""
    return findings(db, user["id"])


class DismissBody(BaseModel):
    key: str = Field(min_length=3, max_length=2000)


@router.post("/cleanup/dismiss")
def dismiss(body: DismissBody, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    db.run("INSERT OR IGNORE INTO cleanup_dismissed(user_id,key,created_at) VALUES(?,?,?)",
           (user["id"], body.key, time.time()))
    return {"ok": True}


@router.delete("/cleanup/dismissed")
def undismiss(user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    db.run("DELETE FROM cleanup_dismissed WHERE user_id=?", (user["id"],))
    return {"ok": True}


class DedupeGroup(BaseModel):
    songs: list[int] = Field(min_length=1, max_length=50)
    keep: tuple[str, str]


class DedupeBody(BaseModel):
    groups: list[DedupeGroup] = Field(default_factory=list, max_length=5000)
    copies: list[tuple[int, str]] = Field(default_factory=list, max_length=5000)


def _own_sources(db: Database, user_id: int, ids: set[int]) -> dict[int, dict]:
    found = {s["id"]: s for s in user_sources(db, user_id, list(ids))}
    if len(found) != len(ids):
        raise HTTPException(404, "Collection not found.")
    return found


@router.post("/cleanup/dedupe")
def dedupe(body: DedupeBody, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    uid = user["id"]
    if not body.groups and not body.copies:
        raise HTTPException(400, "Nothing selected.")
    own = _catalog_song_ids(db, uid)
    for g in body.groups:
        if not set(g.songs) <= own:
            raise HTTPException(400, "None of these songs are in your library.")
        if db.val("SELECT song_id FROM song_refs WHERE service=? AND ref_id=?", g.keep) not in g.songs:
            raise HTTPException(400, "The chosen version doesn't belong to these songs.")
    _own_sources(db, uid, {c[0] for c in body.copies})
    return _start(uid, "dedupe", {"groups": [{"songs": g.songs, "keep": list(g.keep)} for g in body.groups],
                                  "copies": [list(c) for c in body.copies]})


class SwapBody(BaseModel):
    items: list[tuple[int, str]] = Field(min_length=1, max_length=MAX_SONGS)


@router.post("/cleanup/swap")
def swap(body: SwapBody, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    sources = _own_sources(db, user["id"], {i[0] for i in body.items})
    if any(s["service"] != "ytm" or s["kind"] not in ("liked", "playlist") or not s["editable"]
           for s in sources.values()):
        raise HTTPException(400, "Swapping only works for YouTube likes and your own playlists.")
    return _start(user["id"], "swap", {"items": [list(i) for i in body.items]})


class PlaylistsBody(BaseModel):
    delete: list[int] = Field(default_factory=list, max_length=500)
    merge: list[tuple[int, int]] = Field(default_factory=list, max_length=500)


@router.post("/cleanup/playlists")
def playlists(body: PlaylistsBody, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    uid = user["id"]
    gone = set(body.delete) | {m[0] for m in body.merge}
    if not gone:
        raise HTTPException(400, "Nothing selected.")
    sources = _own_sources(db, uid, gone | {m[1] for m in body.merge})
    in_fades = {s["id"]: s["lines"] for s in _sources(db, uid)}
    for sid in gone:
        s = sources[sid]
        if s["kind"] != "playlist" or not s["editable"]:
            raise HTTPException(400, f"“{s['name']}” can't be deleted.")
        if in_fades.get(sid):
            raise HTTPException(400, f"“{s['name']}” is part of a fade. Remove it there first.")
    for a, b in body.merge:
        if a == b or sources[a]["account_id"] != sources[b]["account_id"] or not sources[b]["editable"]:
            raise HTTPException(400, "Merging only works between your own playlists on the same service.")
    return _start(uid, "playlists", {"delete": body.delete, "merge": [list(m) for m in body.merge]})


@router.post("/undo")
def undo(user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    if not db.setting(UNDO_KEY.format(user["id"])):
        raise HTTPException(400, "There's nothing to undo.")
    return _start(user["id"], "undo")
