"""Fades (table `lines`, the pre-rename name): CRUD, runs, issues, history, sharing."""
from __future__ import annotations

import json
import re
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from .. import matcher
from .. import notify as alert
from ..db import Database
from ..identity import Identity
from ..models import PLAYLIST, SERVICE_NAMES, SERVICES, ApiError, AuthError, Track
from ..providers import build_provider
from ..sharing import VISIBLE, are_friends, notify, participants, public_person, visible_line
from ..worker import get_worker
from .deps import current_user, db_dep

router = APIRouter(prefix="/api/lines", tags=["lines"])

PALETTE = ["#0072CE", "#E4A100", "#C4007A", "#2E9E44", "#7A8B1E", "#009CA6", "#6A3FA0", "#8C5A2B"]


# ---------------------------------------------------------------------- serialization
def _line_or_404(db: Database, line_id: int, user_id: int) -> dict:
    """The fade, if the user owns it (settings, members, deleting)."""
    line = db.one("SELECT * FROM lines WHERE id=? AND user_id=?", (line_id, user_id))
    if not line:
        if visible_line(db, line_id, user_id):
            raise HTTPException(403, "Only the fade's owner can do that.")
        raise HTTPException(404, "Fade not found.")
    return line


def _visible_or_404(db: Database, line_id: int, user_id: int) -> dict:
    """The fade, if the user owns it or takes part in it with a collection."""
    line = visible_line(db, line_id, user_id)
    if not line:
        raise HTTPException(404, "Fade not found.")
    return line


def _own_member(db: Database, line_id: int, member_id: int, user_id: int) -> dict:
    """A collection of this fade that belongs to the user: only they act on it."""
    m = db.one("""SELECT m.*, a.service, a.user_id FROM line_members m JOIN accounts a ON a.id=m.account_id
                  WHERE m.id=? AND m.line_id=?""", (member_id, line_id))
    if not m:
        raise HTTPException(404, "Collection not found.")
    if m["user_id"] != user_id:
        raise HTTPException(403, "This collection belongs to someone else.")
    return m


def _members(db: Database, line_id: int, viewer: int | None = None) -> list[dict]:
    rows = db.all("""SELECT m.*, a.service, a.status AS account_status, a.status_message AS account_message,
                     a.user_id, u.display_name AS user_name,
                     (a.credentials IS NOT NULL) AS account_connected,
                     (SELECT COUNT(*) FROM member_items i WHERE i.member_id=m.id AND i.confirmed=0 AND i.excluded=0) AS pending,
                     (SELECT COUNT(*) FROM issues s WHERE s.member_id=m.id AND s.kind='unmatched') AS unmatched
                     FROM line_members m JOIN accounts a ON a.id=m.account_id JOIN users u ON u.id=a.user_id
                     WHERE m.line_id=? ORDER BY m.position, m.id""", (line_id,))
    for r in rows:
        r["mine"] = viewer is None or r["user_id"] == viewer
        if not r["mine"]:   # a friend's sign-in trouble: that it exists, not the details
            r["account_message"] = None
        r["account_connected"] = bool(r["account_connected"])
        r["initialized"] = bool(r["initialized"])
        r["follow_deletions"] = bool(r["follow_deletions"])
        r["covers"] = [c["artwork"] for c in db.all(
            """SELECT DISTINCT sr.artwork FROM member_items mi
               JOIN song_refs sr ON sr.ref_id=mi.ref_id AND sr.service=? AND sr.artwork IS NOT NULL
               WHERE mi.member_id=? AND mi.excluded=0 ORDER BY mi.rowid DESC LIMIT 3""", (r["service"], r["id"]))]
    return rows


def _warnings(db: Database, line: dict, members: list[dict], viewer: int) -> list[dict]:
    w = []
    if len(members) < 2:
        w.append({"kind": "too_few", "text": "A fade needs at least two collections."})
    if members and not any(m["role"] in ("both", "send") for m in members):
        w.append({"kind": "no_sender", "text": "No collection sends, so nothing can be synced."})
    if members and not any(m["role"] in ("both", "receive") or (m["follow_deletions"] and line["sync_deletions"])
                           for m in members):
        w.append({"kind": "no_receiver", "text": "No collection receives, so nothing will change."})
    for m in members:
        whose = "" if m["user_id"] == line["user_id"] else f" ({m['user_name']})"
        if not m["account_connected"]:
            w.append({"kind": "disconnected", "member_id": m["id"],
                      "text": f"{SERVICE_NAMES[m['service']]}{whose} is not connected, so the fade is paused."})
        elif m["account_status"] == "error":
            w.append({"kind": "auth", "member_id": m["id"],
                      "text": (m["account_message"] or "Sign-in expired.") if not whose
                      else f"{SERVICE_NAMES[m['service']]}{whose}: sign-in expired."})
        # The same collection receiving deletions from two lines can bounce songs between them.
        # Only told to the collection's owner: a friend's other fades are none of your business.
        if (m["role"] in ("both", "receive") or m["follow_deletions"]) and line["sync_deletions"] \
                and m["user_id"] == viewer:
            others = db.all("""SELECT l.id, l.name FROM line_members x JOIN lines l ON l.id=x.line_id
                               WHERE x.account_id=? AND x.kind=? AND x.collection_id=? AND x.line_id<>?
                                 AND (x.role IN ('both','receive') OR x.follow_deletions=1)
                                 AND l.sync_deletions=1""",
                            (m["account_id"], m["kind"], m["collection_id"], line["id"]))
            for o in others:
                w.append({"kind": "interchange", "member_id": m["id"], "line_id": o["id"],
                          "text": f"“{m['collection_name']}” also receives deletions in the fade “{o['name']}”. "
                                  "A song removed by one fade then disappears from the other as well."})
    return w


def _serialize(db: Database, line: dict, number: int | None = None, viewer: int | None = None) -> dict:
    viewer = line["user_id"] if viewer is None else viewer
    members = _members(db, line["id"], viewer)
    job = get_worker().job(line["id"])
    mine = line["user_id"] == viewer
    owner = db.one("SELECT id, username, display_name FROM users WHERE id=?", (line["user_id"],))
    # only the owner sees whom they invited; the invitee's music stays hidden until they join
    invites = db.all("""SELECT i.id, i.created_at, i.request, i.requested_at, u.id AS uid, u.username,
                        u.display_name FROM fade_invites i
                        JOIN users u ON u.id=i.to_user WHERE i.line_id=? ORDER BY i.created_at""",
                     (line["id"],)) if mine else []
    return {
        **{k: line[k] for k in ("id", "name", "color", "icon", "interval_minutes", "delete_guard_min", "version_policy",
                                "delete_guard_ratio", "last_run_at", "last_status", "position",
                                "created_at")},
        "number": number,
        "mine": mine,
        "owner": public_person(owner),
        "invites": [{"id": i["id"], "created_at": i["created_at"],
                     "user": {"id": i["uid"], "username": i["username"], "display_name": i["display_name"]},
                     "request": join_request(i)}
                    for i in invites],
        "sync_deletions": bool(line["sync_deletions"]),
        "paused": bool(line["paused"]),
        "last_summary": json.loads(line["last_summary"]) if line["last_summary"] else None,
        "members": members,
        "songs": db.val("SELECT COUNT(*) FROM line_items WHERE line_id=?", (line["id"],)),
        # everyone answers the open songs of their own collections
        "issues": db.val("""SELECT COUNT(*) FROM issues i JOIN line_members m ON m.id=i.member_id
                            JOIN accounts a ON a.id=m.account_id
                            WHERE i.line_id=? AND i.kind='unmatched' AND a.user_id=?""", (line["id"], viewer)),
        "warnings": _warnings(db, line, members, viewer),
        "job": job,
        "next_run_at": (line["last_run_at"] or 0) + line["interval_minutes"] * 60
        if line["interval_minutes"] and not line["paused"] else None,
    }


@router.get("")
def list_lines(user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    return [_serialize(db, line, i + 1, user["id"]) for i, line in enumerate(_visible_lines(db, user["id"]))]


def _visible_lines(db: Database, user_id: int) -> list[dict]:
    """Your own fades in your order, then the ones friends share with you."""
    return db.all(f"""SELECT l.* FROM lines l WHERE {VISIBLE}
                      ORDER BY l.user_id<>?, CASE WHEN l.user_id=? THEN l.position ELSE 0 END, l.id""",
                  (user_id, user_id, user_id, user_id))


def _clamp(limit: int, most: int) -> int:
    """A page size between 1 and `most` (SQLite reads a negative LIMIT as "no limit")."""
    return max(1, min(limit, most))


SECONDS_PER_SONG = 20   # rough time to find and add (or remove) one song by hand – shown as an estimate


@router.get("/activity")
def activity(limit: int = 24, tz_offset: int = 0, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    """Songs that recently arrived somewhere – for the "recently added" strip on the home page."""
    rows = db.all(f"""SELECT c.song_id, MAX(c.ts) AS ts, c.label, l.id AS line_id, l.name AS line_name, l.color,
                     (SELECT a.service FROM line_members m JOIN accounts a ON a.id=m.account_id WHERE m.id=c.member_id) AS service,
                     (SELECT artwork FROM song_refs sr WHERE sr.song_id=c.song_id AND sr.artwork IS NOT NULL LIMIT 1) AS artwork,
                     s.title, s.artist
                     FROM changes c JOIN lines l ON l.id=c.line_id LEFT JOIN songs s ON s.id=c.song_id
                     WHERE {VISIBLE} AND c.action='add' AND c.ok=1
                     GROUP BY c.song_id ORDER BY ts DESC LIMIT ?""",
                  (user["id"], user["id"], _clamp(limit, 60)))
    day = db.one(f"""SELECT SUM(c.action='add') AS added, SUM(c.action='remove') AS removed FROM changes c
                     JOIN lines l ON l.id=c.line_id WHERE {VISIBLE} AND c.ok=1 AND c.ts > ?""",
                 (user["id"], user["id"], time.time() - 86400))
    return {"items": rows, "last_24h": {"added": day["added"] or 0, "removed": day["removed"] or 0},
            "week": _week(db, user["id"], tz_offset)}


def _week(db: Database, user_id: int, tz_offset: int) -> dict:
    """New songs per day over the last 7 days (local days, oldest first), plus who brought most.
    Only what Crossfade itself added: it never sees what anyone listens to."""
    shift = -tz_offset * 60                      # JS getTimezoneOffset(): minutes behind UTC
    today = int((time.time() + shift) // 86400)
    rows = db.all(f"""SELECT c.ts, c.line_id, c.song_id FROM changes c JOIN lines l ON l.id=c.line_id
                      WHERE {VISIBLE} AND c.action='add' AND c.ok=1 AND c.ts > ?""",
                  (user_id, user_id, (today - 13) * 86400 - shift))
    days = [0] * 7
    per_line: dict[int, int] = {}
    songs: set[int] = set()
    before: set[int] = set()                            # the 7 days before, for the comparison
    for r in rows:
        d = int((r["ts"] + shift) // 86400) - (today - 6)
        if d < 0:
            if d >= -7:
                before.add(r["song_id"])
            continue
        if d < 7 and r["song_id"] not in songs:          # a song arriving in three services is one new song
            songs.add(r["song_id"])
            days[d] += 1
        per_line[r["line_id"]] = per_line.get(r["line_id"], 0) + 1
    top_artist = None
    if songs:
        ids = list(songs)[:900]
        counts: dict[str, int] = {}
        for r in db.all(f"SELECT artist FROM songs WHERE id IN ({','.join('?' * len(ids))})", ids):
            name = (r["artist"] or "").split(",")[0].strip()
            if name:
                counts[name] = counts.get(name, 0) + 1
        if counts:
            name = max(counts, key=lambda k: counts[k])
            top_artist = {"name": name, "songs": counts[name]}
    top_line = max(per_line, key=lambda k: per_line[k]) if per_line else None
    # the work done in the user's own collections (a friend's collections saved the friend's time)
    done = db.val("""SELECT COUNT(*) FROM changes c JOIN line_members m ON m.id=c.member_id
                     JOIN accounts a ON a.id=m.account_id WHERE a.user_id=? AND c.ok=1 AND c.ts > ?""",
                  (user_id, (today - 6) * 86400 - shift))
    return {"days": days, "total": sum(days), "prev_total": len(before), "top_artist": top_artist,
            "top_line": top_line, "saved_s": done * SECONDS_PER_SONG}


@router.get("/{line_id}")
def get_line(line_id: int, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    line = _visible_or_404(db, line_id, user["id"])
    ids = [r["id"] for r in _visible_lines(db, user["id"])]
    return _serialize(db, line, ids.index(line_id) + 1, user["id"])


# ---------------------------------------------------------------------- create / update
class MemberIn(BaseModel):
    service: str
    kind: Literal["liked", "library", "playlist"]
    collection_id: str = Field("", max_length=200, pattern=r"^[A-Za-z0-9._:-]*$")   # goes into the services' URLs
    collection_name: str = Field(min_length=1, max_length=120)
    role: Literal["both", "send", "receive"] = "both"
    follow_deletions: bool = False   # send-only: still apply deletions made elsewhere
    create: bool = False          # create a new playlist with collection_name


class LineIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    color: str = PALETTE[0]
    icon: str = Field("note", pattern=r"^[a-z]{2,20}$")
    sync_deletions: bool = True
    interval_minutes: int = Field(60, ge=0, le=10080)
    delete_guard_min: int = Field(5, ge=0, le=100000)
    delete_guard_ratio: float = Field(0.2, ge=0, le=1)
    paused: bool = False
    version_policy: Literal["closest", "exact"] = "closest"
    # one is enough when a friend is to bring the other one
    members: list[MemberIn] = Field(min_length=1, max_length=12)

    @field_validator("color")
    @classmethod
    def _color(cls, v: str) -> str:
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", v):
            raise ValueError("Colour must be a hex value")
        return v.upper()


def _resolve_members(db: Database, user_id: int, members: list[MemberIn], create: bool = True) -> list[dict]:
    """create=False only checks: a playlist to be created is not created yet (a join request
    creates it once the owner approves)."""
    out, seen = [], set()
    for m in members:
        if m.service not in SERVICES:
            raise HTTPException(400, f"Unknown service: {m.service}")
        acc = db.one("SELECT * FROM accounts WHERE user_id=? AND service=?", (user_id, m.service))
        if not acc or not acc["credentials"]:
            raise HTTPException(400, f"{SERVICE_NAMES[m.service]} is not connected.")
        if m.kind != PLAYLIST:
            m.collection_id = ""
        if m.kind == PLAYLIST and m.create and not create:
            out.append({"account_id": acc["id"], "kind": m.kind, "collection_id": "",
                        "collection_name": m.collection_name.strip(), "role": m.role,
                        "follow_deletions": int(m.follow_deletions and m.role == "send")})
            continue
        if m.kind == PLAYLIST and m.create:
            try:
                col = build_provider(db, acc).create_playlist(m.collection_name.strip())
            except (AuthError, ApiError) as e:
                raise HTTPException(502, f"Couldn't create the playlist on {SERVICE_NAMES[m.service]}: {e}") from e
            m.collection_id = col.id
        if m.kind == PLAYLIST and not m.collection_id:
            raise HTTPException(400, "Please choose a playlist.")
        key = (acc["id"], m.kind, m.collection_id)
        if key in seen:
            raise HTTPException(400, f"“{m.collection_name}” is in the fade twice.")
        seen.add(key)
        out.append({"account_id": acc["id"], "kind": m.kind, "collection_id": m.collection_id,
                    "collection_name": m.collection_name.strip(), "role": m.role,
                    # only meaningful for a member that does not receive anyway
                    "follow_deletions": int(m.follow_deletions and m.role == "send")})
    return out


@router.post("")
def create_line(body: LineIn, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    members = _resolve_members(db, user["id"], body.members)
    now = time.time()
    pos = (db.val("SELECT MAX(position) FROM lines WHERE user_id=?", (user["id"],)) or 0) + 1
    with db.tx() as c:
        lid = c.execute("""INSERT INTO lines(user_id,name,color,icon,sync_deletions,interval_minutes,delete_guard_min,
                           delete_guard_ratio,paused,position,version_policy,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (user["id"], body.name.strip(), body.color, body.icon, int(body.sync_deletions), body.interval_minutes,
                         body.delete_guard_min, body.delete_guard_ratio, int(body.paused), pos, body.version_policy,
                         now, now)).lastrowid
        for i, m in enumerate(members):
            c.execute("""INSERT INTO line_members(line_id,account_id,kind,collection_id,collection_name,role,
                         follow_deletions,position) VALUES(?,?,?,?,?,?,?,?)""",
                      (lid, m["account_id"], m["kind"], m["collection_id"], m["collection_name"], m["role"],
                       m["follow_deletions"], i))
    return get_line(lid, user, db)


@router.put("/{line_id}")
def update_line(line_id: int, body: LineIn, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    _line_or_404(db, line_id, user["id"])
    if get_worker().job(line_id):
        raise HTTPException(409, "The fade is syncing right now. Wait until it's done.")
    current = _members(db, line_id, user["id"])
    # friends' collections are theirs: the editor neither shows nor removes them
    guests = [m for m in current if not m["mine"]]
    if len(body.members) + len(guests) > 12:     # checked first: resolving may create playlists
        raise HTTPException(400, "At most 12 collections per fade.")
    members = _resolve_members(db, user["id"], body.members)
    existing = {(m["account_id"], m["kind"], m["collection_id"]): m for m in current if m["mine"]}
    keep_ids = set()
    with db.tx() as c:
        c.execute("""UPDATE lines SET name=?, color=?, icon=?, sync_deletions=?, interval_minutes=?, delete_guard_min=?,
                     delete_guard_ratio=?, paused=?, version_policy=?, updated_at=? WHERE id=?""",
                  (body.name.strip(), body.color, body.icon, int(body.sync_deletions), body.interval_minutes,
                   body.delete_guard_min, body.delete_guard_ratio, int(body.paused), body.version_policy,
                   time.time(), line_id))
        for i, m in enumerate(members):
            old = existing.get((m["account_id"], m["kind"], m["collection_id"]))
            if old:   # keep its sync state
                keep_ids.add(old["id"])
                c.execute("UPDATE line_members SET role=?, follow_deletions=?, position=?, collection_name=? WHERE id=?",
                          (m["role"], m["follow_deletions"], i, m["collection_name"], old["id"]))
            else:
                c.execute("""INSERT INTO line_members(line_id,account_id,kind,collection_id,collection_name,role,
                             follow_deletions,position) VALUES(?,?,?,?,?,?,?,?)""",
                          (line_id, m["account_id"], m["kind"], m["collection_id"], m["collection_name"], m["role"],
                           m["follow_deletions"], i))
        for old in existing.values():
            if old["id"] not in keep_ids:
                c.execute("DELETE FROM line_members WHERE id=?", (old["id"],))
        for j, g in enumerate(guests):
            c.execute("UPDATE line_members SET position=? WHERE id=?", (len(members) + j, g["id"]))
    _notify_others(db, line_id, user["id"])
    return get_line(line_id, user, db)


class PausedBody(BaseModel):
    paused: bool


@router.put("/{line_id}/paused")
def set_paused(line_id: int, body: PausedBody, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    _line_or_404(db, line_id, user["id"])
    db.run("UPDATE lines SET paused=? WHERE id=?", (int(body.paused), line_id))
    return get_line(line_id, user, db)


class OrderBody(BaseModel):
    ids: list[int] = Field(max_length=1000)


@router.put("")
def reorder(body: OrderBody, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    with db.tx() as c:
        c.executemany("UPDATE lines SET position=? WHERE id=? AND user_id=?",
                      [(pos, lid, user["id"]) for pos, lid in enumerate(body.ids)])
    return list_lines(user, db)


@router.delete("/{line_id}")
def delete_line(line_id: int, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    _line_or_404(db, line_id, user["id"])
    if get_worker().job(line_id):
        raise HTTPException(409, "The fade is syncing right now. Stop it or wait until it's done.")
    others = participants(db, line_id) - {user["id"]}
    db.run("DELETE FROM lines WHERE id=?", (line_id,))
    notify(others, "fades")
    return {"ok": True}


# ---------------------------------------------------------------------- runs
class RunBody(BaseModel):
    preview: bool = False
    allow_deletions: bool = False


@router.post("/{line_id}/run")
def run_line(line_id: int, body: RunBody, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    line = _visible_or_404(db, line_id, user["id"])
    if body.allow_deletions and line["user_id"] != user["id"]:
        raise HTTPException(403, "Only the fade's owner can confirm held deletions.")
    members = _members(db, line_id)
    if len(members) < 2:
        raise HTTPException(400, "A fade needs at least two collections.")
    for m in members:
        if not m["account_connected"]:
            raise HTTPException(400, f"{SERVICE_NAMES[m['service']]} is not connected.")
    if not get_worker().start(line["id"], "manual", apply=not body.preview, allow_deletions=body.allow_deletions):
        raise HTTPException(409, "This fade is already syncing.")
    return {"ok": True}


@router.post("/{line_id}/stop")
def stop_line(line_id: int, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    _visible_or_404(db, line_id, user["id"])
    get_worker().stop(line_id)
    return {"ok": True}


@router.get("/{line_id}/live")
def live(line_id: int, since: int = 0, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    """Results of a running job so far (plan entries, unmatched songs, applied changes), from entry `since` on."""
    _visible_or_404(db, line_id, user["id"])
    return get_worker().live_results(line_id, since)


@router.get("/{line_id}/plan")
def last_plan(line_id: int, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    _visible_or_404(db, line_id, user["id"])
    r = db.one("SELECT id, trigger, started_at, status, summary, plan FROM runs WHERE line_id=? AND plan IS NOT NULL "
               "ORDER BY id DESC LIMIT 1", (line_id,))
    if not r:
        return None
    return {"run_id": r["id"], "trigger": r["trigger"], "started_at": r["started_at"], "status": r["status"],
            "summary": json.loads(r["summary"] or "{}"), "plan": json.loads(r["plan"])}


@router.get("/{line_id}/changes")
def changes(line_id: int, limit: int = 200, before: int | None = None, user: dict = Depends(current_user),
            db: Database = Depends(db_dep)):
    _visible_or_404(db, line_id, user["id"])
    sql = """SELECT c.id, c.run_id, c.action, c.label, c.ok, c.message, c.note, c.ts, c.member_id, c.song_id,
             (SELECT artwork FROM song_refs sr WHERE sr.song_id=c.song_id AND sr.artwork IS NOT NULL LIMIT 1) AS artwork,
             m.collection_name, a.service FROM changes c
             LEFT JOIN line_members m ON m.id=c.member_id LEFT JOIN accounts a ON a.id=m.account_id
             WHERE c.line_id=?"""
    args: list = [line_id]
    if before:
        sql += " AND c.id < ?"
        args.append(before)
    sql += " ORDER BY c.id DESC LIMIT ?"
    args.append(_clamp(limit, 500))
    return db.all(sql, args)


@router.get("/{line_id}/stats")
def stats(line_id: int, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    """What this fade has done so far, from its own history only (never listening data).
    complete: songs that are in every collection that receives (sent, confirmed or deliberately
    removed there); collections that were never synced yet don't count against it."""
    line = _visible_or_404(db, line_id, user["id"])
    total = db.val("SELECT COUNT(*) FROM line_items WHERE line_id=?", (line_id,))
    complete = db.val("""SELECT COUNT(*) FROM line_items li WHERE li.line_id=? AND NOT EXISTS(
                         SELECT 1 FROM line_members m WHERE m.line_id=li.line_id AND m.initialized=1
                         AND m.role IN ('both','receive') AND NOT EXISTS(
                           SELECT 1 FROM member_items mi WHERE mi.member_id=m.id AND mi.song_id=li.song_id))""",
                      (line_id,))
    per = {r["member_id"]: r for r in db.all(
        """SELECT member_id, SUM(action='add') AS added, SUM(action='remove') AS removed FROM changes
           WHERE line_id=? AND ok=1 AND member_id IS NOT NULL GROUP BY member_id""", (line_id,))}
    members = db.all("SELECT id FROM line_members WHERE line_id=? ORDER BY position, id", (line_id,))
    first = db.val("SELECT MIN(started_at) FROM runs WHERE line_id=? AND trigger<>'preview'", (line_id,))
    return {
        "songs": total,
        "complete": complete,
        "syncs": db.val("""SELECT COUNT(*) FROM runs WHERE line_id=? AND trigger<>'preview'
                           AND status IN ('ok','partial')""", (line_id,)),
        "since": first or line["created_at"],
        "added": sum(r["added"] or 0 for r in per.values()),
        "removed": sum(r["removed"] or 0 for r in per.values()),
        "saved_s": sum((r["added"] or 0) + (r["removed"] or 0) for r in per.values()) * SECONDS_PER_SONG,
        "failed_30d": db.val("SELECT COUNT(*) FROM changes WHERE line_id=? AND ok=0 AND ts > ?",
                             (line_id, time.time() - 30 * 86400)),
        "members": [{"member_id": m["id"], "added": (per.get(m["id"]) or {}).get("added") or 0,
                     "removed": (per.get(m["id"]) or {}).get("removed") or 0} for m in members],
    }


@router.get("/{line_id}/songs")
def songs(line_id: int, q: str = "", limit: int = 100, offset: int = 0, user: dict = Depends(current_user),
          db: Database = Depends(db_dep)):
    _visible_or_404(db, line_id, user["id"])
    like = f"%{q.strip()}%"
    rows = db.all("""SELECT s.id, s.title, s.artist, s.album, s.duration,
                     (SELECT artwork FROM song_refs sr WHERE sr.song_id=s.id AND sr.artwork IS NOT NULL LIMIT 1) AS artwork
                     FROM line_items li JOIN songs s ON s.id=li.song_id
                     WHERE li.line_id=? AND (s.title LIKE ? OR s.artist LIKE ?)
                     ORDER BY s.artist COLLATE NOCASE, s.title COLLATE NOCASE LIMIT ? OFFSET ?""",
                  (line_id, like, like, _clamp(limit, 500), max(offset, 0)))
    total = db.val("""SELECT COUNT(*) FROM line_items li JOIN songs s ON s.id=li.song_id
                      WHERE li.line_id=? AND (s.title LIKE ? OR s.artist LIKE ?)""", (line_id, like, like))
    member_ids = [m["id"] for m in db.all("SELECT id FROM line_members WHERE line_id=? ORDER BY position, id",
                                          (line_id,))]
    song_ids = [r["id"] for r in rows]
    states: dict[tuple[int, int], str] = {}
    if song_ids and member_ids:
        for x in db.all(f"""SELECT member_id, song_id, confirmed, excluded FROM member_items
                            WHERE member_id IN ({','.join('?' * len(member_ids))})
                            AND song_id IN ({','.join('?' * len(song_ids))})""", (*member_ids, *song_ids)):
            states[(x["member_id"], x["song_id"])] = ("ok" if x["confirmed"] else
                                                      "excluded" if x["excluded"] else "pending")
    for r in rows:
        r["at"] = [{"member_id": mid, "state": states.get((mid, r["id"]), "missing")} for mid in member_ids]
    return {"total": total, "items": rows}


# ---------------------------------------------------------------------- issues
@router.get("/{line_id}/issues")
def issues(line_id: int, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    _visible_or_404(db, line_id, user["id"])
    rows = db.all("""SELECT i.id, i.kind, i.member_id, i.song_id, i.data, i.created_at, m.collection_name, a.service
                     FROM issues i JOIN line_members m ON m.id=i.member_id JOIN accounts a ON a.id=m.account_id
                     WHERE i.line_id=? AND a.user_id=? ORDER BY i.kind DESC, i.created_at""", (line_id, user["id"]))
    for r in rows:
        r["data"] = json.loads(r["data"] or "{}")
        if r["kind"] == "ignored" and not r["data"].get("track"):
            r["data"]["track"] = Identity(db).describe(r["song_id"]).to_dict()
    return rows


def _issue(db: Database, line_id: int, issue_id: int, user_id: int) -> dict:
    _visible_or_404(db, line_id, user_id)
    row = db.one("""SELECT i.*, m.kind AS member_kind, m.collection_id, a.service, a.id AS account_id
                    FROM issues i JOIN line_members m ON m.id=i.member_id JOIN accounts a ON a.id=m.account_id
                    WHERE i.id=? AND i.line_id=? AND a.user_id=?""", (issue_id, line_id, user_id))
    if not row:
        raise HTTPException(404, "Entry not found.")
    return row


class ResolveBody(BaseModel):
    target: str      # catalog id or share link on the member's service


@router.post("/{line_id}/issues/{issue_id}/resolve")
def resolve_issue(line_id: int, issue_id: int, body: ResolveBody, user: dict = Depends(current_user),
                  db: Database = Depends(db_dep)):
    if get_worker().job(line_id):
        raise HTTPException(409, "The fade is syncing right now.")
    row = _issue(db, line_id, issue_id, user["id"])
    acc = db.one("SELECT * FROM accounts WHERE id=?", (row["account_id"],))
    try:
        prov = build_provider(db, acc)
        tid = prov.parse_link(body.target)
        if not tid:
            raise HTTPException(400, f"That's not a valid {SERVICE_NAMES[row['service']]} link or ID.")
        t = prov.track(tid) or Track(service=row["service"], id=tid, title="")
        ident = Identity(db)
        dropped = ident.link(row["song_id"], t)
        failed = prov.add(row["member_kind"], row["collection_id"], [t])
    except AuthError as e:
        raise HTTPException(401, str(e)) from e
    except ApiError as e:
        raise HTTPException(502, str(e)) from e
    if failed:
        raise HTTPException(502, f"{SERVICE_NAMES[row['service']]} refused to add it.")
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO member_items(member_id,song_id,ref_id,confirmed,excluded,misses) "
                  "VALUES(?,?,?,0,0,0)", (row["member_id"], row["song_id"], t.id))
        c.execute("INSERT INTO search_cache(service,song_id,best_ref,score,candidates,created_at) VALUES(?,?,?,?,?,?) "
                  "ON CONFLICT(service,song_id) DO UPDATE SET best_ref=excluded.best_ref, score=excluded.score, "
                  "candidates=excluded.candidates, created_at=excluded.created_at",
                  (row["service"], row["song_id"], t.id, 1.0, json.dumps([dict(t.to_dict(), score=1.0)]), time.time()))
        c.execute("DELETE FROM issues WHERE id=?", (issue_id,))
        c.execute("INSERT INTO changes(run_id,line_id,member_id,song_id,action,label,ok,message,ts) "
                  "VALUES(NULL,?,?,?,'add',?,1,'matched by hand',?)",
                  (line_id, row["member_id"], row["song_id"], t.label(), time.time()))
    return {"ok": True, "track": t.to_dict(), "merged": dropped}


@router.post("/{line_id}/issues/{issue_id}/ignore")
def ignore_issue(line_id: int, issue_id: int, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    row = _issue(db, line_id, issue_id, user["id"])
    data = json.loads(row["data"] or "{}")
    with db.tx() as c:
        c.execute("DELETE FROM issues WHERE id=?", (issue_id,))
        c.execute("INSERT OR IGNORE INTO issues(line_id,member_id,song_id,kind,data,created_at) "
                  "VALUES(?,?,?,'ignored',?,?)", (line_id, row["member_id"], row["song_id"],
                                                  json.dumps({"track": data.get("track")}), time.time()))
    return {"ok": True}


@router.post("/{line_id}/issues/{issue_id}/restore")
def restore_issue(line_id: int, issue_id: int, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    row = _issue(db, line_id, issue_id, user["id"])
    if row["kind"] != "ignored":
        raise HTTPException(400, "Only ignored songs can be restored.")
    db.run("DELETE FROM issues WHERE id=?", (issue_id,))
    db.run("DELETE FROM search_cache WHERE service=? AND song_id=?", (row["service"], row["song_id"]))
    return {"ok": True}


@router.post("/{line_id}/issues/{issue_id}/search")
def research_issue(line_id: int, issue_id: int, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    row = _issue(db, line_id, issue_id, user["id"])
    acc = db.one("SELECT * FROM accounts WHERE id=?", (row["account_id"],))
    try:
        match = Identity(db).find(row["song_id"], row["service"], build_provider(db, acc), use_cache=False)
    except AuthError as e:
        raise HTTPException(401, str(e)) from e
    except ApiError as e:
        raise HTTPException(502, str(e)) from e
    data = json.loads(row["data"] or "{}")
    data["candidates"] = match.candidates
    db.run("UPDATE issues SET data=? WHERE id=?", (json.dumps(data), issue_id))
    return {"candidates": match.candidates, "confident": match.accepted("exact")}


# ---------------------------------------------------------------------- versions
def _song_detail(db: Database, line_id: int, song_id: int) -> dict:
    """Which version sits at each stop, compared with the version the song came in as."""
    ident = Identity(db)
    origin = ident.describe(song_id)
    members = _members(db, line_id)
    items = {r["member_id"]: r for r in db.all(
        "SELECT * FROM member_items WHERE song_id=? AND member_id IN (SELECT id FROM line_members WHERE line_id=?)",
        (song_id, line_id))}
    stops = []
    for m in members:
        it = items.get(m["id"])
        ref = None
        if it and it["ref_id"]:
            r = db.one("SELECT * FROM song_refs WHERE service=? AND ref_id=?", (m["service"], it["ref_id"]))
            ref = Identity._track(r) if r else None
        info = {"member_id": m["id"], "service": m["service"], "collection_name": m["collection_name"],
                "state": ("ok" if it["confirmed"] else "excluded" if it["excluded"] else "pending") if it else "missing",
                "track": ref.to_dict() if ref else None, "version": None, "exact": None, "notes": []}
        if ref:
            a = matcher.assess(origin, ref) if ref.id != origin.id else None
            info["version"] = matcher.version(ref).label()
            info["exact"] = True if a is None else a.exact
            info["notes"] = [] if a is None else a.notes
        stops.append(info)
    return {"song_id": song_id, "origin": origin.to_dict(), "origin_version": matcher.version(origin).label(),
            "stops": stops}


@router.get("/{line_id}/songs/{song_id}")
def song_detail(line_id: int, song_id: int, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    _visible_or_404(db, line_id, user["id"])
    _song_in_fade(db, line_id, song_id)
    return _song_detail(db, line_id, song_id)


def _song_in_fade(db: Database, line_id: int, song_id: int) -> None:
    if not db.val("SELECT 1 FROM line_items WHERE line_id=? AND song_id=?", (line_id, song_id)):
        raise HTTPException(404, "Song is not in this fade.")


@router.get("/{line_id}/versions")
def version_audit(line_id: int, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    """Songs that sit somewhere in this line as a different version than the one they came in as."""
    _visible_or_404(db, line_id, user["id"])
    ident = Identity(db)
    rows = db.all("""SELECT mi.song_id, mi.member_id, sr.*, m.collection_name, a.service AS member_service
                     FROM member_items mi JOIN line_members m ON m.id=mi.member_id
                     JOIN accounts a ON a.id=m.account_id
                     JOIN song_refs sr ON sr.service=a.service AND sr.ref_id=mi.ref_id
                     WHERE m.line_id=? AND mi.excluded=0""", (line_id,))
    origins: dict[int, Track] = {}
    out: dict[int, dict] = {}
    for r in rows:
        sid = r["song_id"]
        origin = origins.get(sid) or origins.setdefault(sid, ident.describe(sid))
        ref = Identity._track(r)
        if ref.service == origin.service and ref.id == origin.id:
            continue
        a = matcher.assess(origin, ref)
        if a.exact:
            continue
        entry = out.setdefault(sid, {"song_id": sid, "origin": origin.to_dict(), "stops": []})
        entry["stops"].append({"member_id": r["member_id"], "service": ref.service,
                               "collection_name": r["collection_name"], "track": ref.to_dict(), "notes": a.notes})
    return sorted(out.values(), key=lambda e: (e["origin"]["artist"].lower(), e["origin"]["title"].lower()))


class MemberRef(BaseModel):
    member_id: int


@router.post("/{line_id}/songs/{song_id}/candidates")
def version_candidates(line_id: int, song_id: int, body: MemberRef, user: dict = Depends(current_user),
                       db: Database = Depends(db_dep)):
    _visible_or_404(db, line_id, user["id"])
    _song_in_fade(db, line_id, song_id)
    m = _own_member(db, line_id, body.member_id, user["id"])
    acc = db.one("SELECT * FROM accounts WHERE id=?", (m["account_id"],))
    ident = Identity(db)
    origin = ident.describe(song_id)
    try:
        prov = build_provider(db, acc)
        # search fresh and ignore the ref the song already has on this service
        seen: dict[str, Track] = {}
        for q in dict.fromkeys([f"{matcher.norm_title(origin.title)} {origin.artist.split(',')[0]}",
                                f"{origin.title} {origin.artist}"]):
            for t in prov.search(q, limit=10):
                seen.setdefault(t.id, t)
        if origin.isrc:
            for t in prov.by_isrc(origin.isrc):
                seen.setdefault(t.id, t)
    except AuthError as e:
        raise HTTPException(401, str(e)) from e
    except ApiError as e:
        raise HTTPException(502, str(e)) from e
    scored = sorted(((t, matcher.assess(origin, t)) for t in seen.values()), key=lambda x: x[1].rank_key, reverse=True)
    return [dict(t.to_dict(), score=a.score, exact=a.exact, notes=a.notes, version=matcher.version(t).label())
            for t, a in scored[:8] if a.score >= matcher.SUGGEST]


class ReplaceBody(BaseModel):
    member_id: int
    target: str


@router.post("/{line_id}/songs/{song_id}/replace")
def replace_version(line_id: int, song_id: int, body: ReplaceBody, user: dict = Depends(current_user),
                    db: Database = Depends(db_dep)):
    """Swap the version of a song at one stop: add the chosen one, then remove the old one."""
    _visible_or_404(db, line_id, user["id"])
    if get_worker().job(line_id):
        raise HTTPException(409, "The fade is syncing right now.")
    _song_in_fade(db, line_id, song_id)
    m = _own_member(db, line_id, body.member_id, user["id"])
    acc = db.one("SELECT * FROM accounts WHERE id=?", (m["account_id"],))
    item = db.one("SELECT * FROM member_items WHERE member_id=? AND song_id=?", (m["id"], song_id))
    try:
        prov = build_provider(db, acc)
        tid = prov.parse_link(body.target)
        if not tid:
            raise HTTPException(400, f"That's not a valid {SERVICE_NAMES[m['service']]} link or ID.")
        new = prov.track(tid) or Track(service=m["service"], id=tid, title="")
        old = None
        if item and item["ref_id"] and item["ref_id"] != tid:
            # the remove call needs the collection's own handle for the item
            old = next((t for t in prov.read(m["kind"], m["collection_id"]) if t.id == item["ref_id"]), None)
        if prov.add(m["kind"], m["collection_id"], [new]):
            raise HTTPException(502, f"{SERVICE_NAMES[m['service']]} refused to add it.")
        removed_old = bool(old) and not prov.remove(m["kind"], m["collection_id"], [old])
    except AuthError as e:
        raise HTTPException(401, str(e)) from e
    except ApiError as e:
        raise HTTPException(502, str(e)) from e
    # The old version stays known (other collections may contain it); the chosen one is pinned
    # through the search cache below and wins from now on.
    Identity(db).link(song_id, new)
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO member_items(member_id,song_id,ref_id,confirmed,excluded,misses) "
                  "VALUES(?,?,?,0,0,0)", (m["id"], song_id, new.id))
        c.execute("INSERT INTO search_cache(service,song_id,best_ref,score,candidates,created_at,exact,notes) "
                  "VALUES(?,?,?,1,?,?,1,'[]') ON CONFLICT(service,song_id) DO UPDATE SET best_ref=excluded.best_ref, "
                  "score=1, candidates=excluded.candidates, created_at=excluded.created_at, exact=1, notes='[]'",
                  (m["service"], song_id, new.id, json.dumps([dict(new.to_dict(), score=1.0, exact=True)]), time.time()))
        c.execute("INSERT INTO changes(run_id,line_id,member_id,song_id,action,label,ok,message,ts,note) "
                  "VALUES(NULL,?,?,?,'add',?,1,NULL,?,'version chosen by hand')",
                  (line_id, m["id"], song_id, new.label(), time.time()))
        if old:
            c.execute("INSERT INTO changes(run_id,line_id,member_id,song_id,action,label,ok,message,ts,note) "
                      "VALUES(NULL,?,?,?,'remove',?,?,?,?,'wrong version replaced')",
                      (line_id, m["id"], song_id, old.label(), int(removed_old),
                       None if removed_old else "couldn't remove the old version", time.time()))
    return _song_detail(db, line_id, song_id)


# ---------------------------------------------------------------------- sharing with friends
def join_request(inv: dict) -> dict | None:
    """What a friend asked to join with: one collection and its direction. Shown to the owner
    (to approve it) and to the friend (to see what is waiting)."""
    if not inv.get("request"):
        return None
    r = json.loads(inv["request"])
    return {"service": r["service"], "kind": r["kind"], "collection_name": r["collection_name"],
            "role": r["role"], "follow_deletions": bool(r["follow_deletions"]), "create": bool(r["create"]),
            "requested_at": inv["requested_at"]}


def _notify_others(db: Database, line_id: int, user_id: int) -> None:
    notify(participants(db, line_id) - {user_id}, "fades")


class InviteBody(BaseModel):
    user_id: int


@router.post("/{line_id}/invites")
def invite(line_id: int, body: InviteBody, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    """Invite a friend. They only learn the fade's name and look until they join with a collection."""
    _line_or_404(db, line_id, user["id"])
    if not are_friends(db, user["id"], body.user_id):
        raise HTTPException(403, "You can only invite friends.")
    if body.user_id in participants(db, line_id):
        raise HTTPException(409, "This person is already in.")
    if db.val("SELECT 1 FROM fade_invites WHERE line_id=? AND to_user=?", (line_id, body.user_id)):
        raise HTTPException(409, "The invite has already been sent.")
    db.run("INSERT INTO fade_invites(line_id,from_user,to_user,created_at) VALUES(?,?,?,?)",
           (line_id, user["id"], body.user_id, time.time()))
    fade = db.val("SELECT name FROM lines WHERE id=?", (line_id,))
    alert.safely(alert.social, db, body.user_id, "fade_invite", "/fades", name=user["display_name"], fade=fade)
    notify([body.user_id], "fades")
    return get_line(line_id, user, db)


@router.delete("/{line_id}/invites/{invite_id}")
def cancel_invite(line_id: int, invite_id: int, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    _line_or_404(db, line_id, user["id"])
    to = db.val("SELECT to_user FROM fade_invites WHERE id=? AND line_id=?", (invite_id, line_id))
    if to is None:
        raise HTTPException(404, "Invite not found.")
    db.run("DELETE FROM fade_invites WHERE id=?", (invite_id,))
    notify([to], "fades")
    return get_line(line_id, user, db)


class ApproveBody(BaseModel):
    # the owner may change the direction the friend asked for
    role: Literal["both", "send", "receive"] | None = None
    follow_deletions: bool | None = None


def _requested_invite(db: Database, line_id: int, invite_id: int) -> dict:
    inv = db.one("SELECT * FROM fade_invites WHERE id=? AND line_id=?", (invite_id, line_id))
    if not inv:
        raise HTTPException(404, "Invite not found.")
    if not inv["request"]:
        raise HTTPException(409, "Nothing to approve yet: your friend hasn't picked a collection.")
    return inv


@router.post("/{line_id}/invites/{invite_id}/approve")
def approve_request(line_id: int, invite_id: int, body: ApproveBody, user: dict = Depends(current_user),
                    db: Database = Depends(db_dep)):
    """The owner lets a friend's collection in, as asked or with another direction."""
    _line_or_404(db, line_id, user["id"])
    inv = _requested_invite(db, line_id, invite_id)
    guest = inv["to_user"]
    if not are_friends(db, user["id"], guest):
        db.run("DELETE FROM fade_invites WHERE id=?", (invite_id,))
        raise HTTPException(410, "You're no longer friends.")
    if get_worker().job(line_id):
        raise HTTPException(409, "The fade is syncing right now. Try again in a moment.")
    if db.val("SELECT COUNT(*) FROM line_members WHERE line_id=?", (line_id,)) >= 12:
        raise HTTPException(400, "The fade is full (at most 12 collections).")
    req = MemberIn(**json.loads(inv["request"]))
    if body.role:
        req.role = body.role
    if body.follow_deletions is not None:
        req.follow_deletions = body.follow_deletions
    member = _resolve_members(db, guest, [req])[0]   # creates the friend's new playlist only now
    if db.val("SELECT 1 FROM line_members WHERE line_id=? AND account_id=? AND kind=? AND collection_id=?",
              (line_id, member["account_id"], member["kind"], member["collection_id"])):
        raise HTTPException(409, "This collection is already in the fade.")
    with db.tx() as c:
        pos = (c.execute("SELECT MAX(position) FROM line_members WHERE line_id=?", (line_id,)).fetchone()[0] or 0) + 1
        c.execute("""INSERT INTO line_members(line_id,account_id,kind,collection_id,collection_name,role,
                     follow_deletions,position) VALUES(?,?,?,?,?,?,?,?)""",
                  (line_id, member["account_id"], member["kind"], member["collection_id"],
                   member["collection_name"], member["role"], member["follow_deletions"], pos))
        c.execute("DELETE FROM fade_invites WHERE id=?", (invite_id,))
    fade = db.val("SELECT name FROM lines WHERE id=?", (line_id,))
    alert.safely(alert.social, db, guest, "fade_joined", f"/fades/{line_id}", name=user["display_name"], fade=fade)
    notify([guest], "fades")
    return get_line(line_id, user, db)


@router.post("/{line_id}/invites/{invite_id}/reject")
def reject_request(line_id: int, invite_id: int, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    """The owner says no to this collection. The invite stays, so the friend can pick another one."""
    _line_or_404(db, line_id, user["id"])
    inv = _requested_invite(db, line_id, invite_id)
    db.run("UPDATE fade_invites SET request=NULL, requested_at=NULL WHERE id=?", (invite_id,))
    notify([inv["to_user"]], "fades")
    return get_line(line_id, user, db)


def _drop_members(db: Database, line_id: int, of_user: int) -> None:
    if get_worker().job(line_id):
        raise HTTPException(409, "The fade is syncing right now. Wait until it's done.")
    db.run("""DELETE FROM line_members WHERE line_id=? AND account_id IN (SELECT id FROM accounts WHERE user_id=?)""",
           (line_id, of_user))


@router.delete("/{line_id}/guests/{guest_id}")
def remove_guest(line_id: int, guest_id: int, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    """The owner takes a friend's collections out of the fade. Nothing is deleted at the friend's."""
    _line_or_404(db, line_id, user["id"])
    if guest_id == user["id"]:
        raise HTTPException(400, "Change your own collections in the editor.")
    _drop_members(db, line_id, guest_id)
    notify([guest_id], "fades")
    return get_line(line_id, user, db)


@router.delete("/{line_id}/membership")
def leave(line_id: int, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    """A friend leaves a shared fade: their collections leave it, nothing is deleted anywhere."""
    line = _visible_or_404(db, line_id, user["id"])
    if line["user_id"] == user["id"]:
        raise HTTPException(400, "You can delete your own fade, but not leave it.")
    _drop_members(db, line_id, user["id"])
    notify([line["user_id"]], "fades")
    return {"ok": True}


class MembershipBody(BaseModel):
    role: Literal["both", "send", "receive"]
    follow_deletions: bool = False


@router.put("/{line_id}/members/{member_id}")
def set_member_role(line_id: int, member_id: int, body: MembershipBody, user: dict = Depends(current_user),
                    db: Database = Depends(db_dep)):
    """Everyone decides the direction of their own collection, also in a friend's fade. The owner
    may change it for every collection in their fade."""
    line = _visible_or_404(db, line_id, user["id"])
    if get_worker().job(line_id):
        raise HTTPException(409, "The fade is syncing right now. Wait until it's done.")
    if line["user_id"] == user["id"]:
        if not db.val("SELECT 1 FROM line_members WHERE id=? AND line_id=?", (member_id, line_id)):
            raise HTTPException(404, "Collection not found.")
    else:
        _own_member(db, line_id, member_id, user["id"])
    db.run("UPDATE line_members SET role=?, follow_deletions=? WHERE id=?",
           (body.role, int(body.follow_deletions and body.role == "send"), member_id))
    _notify_others(db, line_id, user["id"])
    return get_line(line_id, user, db)
