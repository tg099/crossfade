"""Songs matched by hand: look at the choices you made, pick another version, or take one back.

A choice counts for everyone (every fade, friends' too, and library adds), see Identity.pin. Each
person sees and manages the choices they made themselves; whoever chooses last for a song and service
owns that choice.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import matcher
from ..db import Database
from ..identity import Identity
from ..models import SERVICE_NAMES, ApiError, AuthError, Track
from ..providers import build_provider
from ..sharing import VISIBLE
from .deps import current_user, db_dep
from .lines import versions_on

router = APIRouter(prefix="/api/matches", tags=["matches"])

_PINS = """SELECT p.song_id, p.created_at, r.* FROM manual_matches p JOIN song_refs r
           ON r.service=p.service AND r.ref_id=p.ref_id AND r.song_id=p.song_id"""


def _entry(db: Database, ident: Identity, row: dict, user_id: int) -> dict:
    t = Identity._track(row)
    origin = ident.describe(row["song_id"], away_from=t.service)
    a = matcher.assess(origin, t) if origin.id != t.id else None
    fades = db.val(f"SELECT COUNT(*) FROM line_items li JOIN lines l ON l.id=li.line_id WHERE li.song_id=? AND {VISIBLE}",
                   (row["song_id"], user_id, user_id))
    return {"song_id": row["song_id"], "service": t.service, "created_at": row["created_at"],
            "track": t.to_dict(), "version": matcher.version(t).label(), "origin": origin.to_dict(),
            "exact": a is None or a.exact, "notes": [] if a is None else a.notes, "fades": fades}


def _own_pin(db: Database, song_id: int, service: str, user_id: int) -> dict:
    row = db.one(f"{_PINS} WHERE p.song_id=? AND p.service=? AND p.user_id=?", (song_id, service, user_id))
    if not row:
        raise HTTPException(404, "Match not found.")
    return row


def _provider(db: Database, service: str, user_id: int):
    acc = db.one("SELECT * FROM accounts WHERE user_id=? AND service=? AND credentials IS NOT NULL", (user_id, service))
    if not acc:
        raise HTTPException(400, f"{SERVICE_NAMES.get(service, service)} is not connected.")
    return build_provider(db, acc)


@router.get("")
def list_matches(user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    ident = Identity(db)
    rows = db.all(f"{_PINS} WHERE p.user_id=? ORDER BY p.created_at DESC", (user["id"],))
    return [_entry(db, ident, r, user["id"]) for r in rows]


@router.post("/{song_id}/{service}/candidates")
def candidates(song_id: int, service: str, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    row = _own_pin(db, song_id, service, user["id"])
    origin = Identity(db).describe(song_id, away_from=service)
    try:
        found = versions_on(_provider(db, service, user["id"]), origin)
    except AuthError as e:
        raise HTTPException(401, str(e)) from e
    except ApiError as e:
        raise HTTPException(502, str(e)) from e
    return [c for c in found if c["id"] != row["ref_id"]]


class MatchBody(BaseModel):
    target: str = Field(min_length=1, max_length=500)    # catalog id or share link on that service


@router.put("/{song_id}/{service}")
def change(song_id: int, service: str, body: MatchBody, user: dict = Depends(current_user),
           db: Database = Depends(db_dep)):
    _own_pin(db, song_id, service, user["id"])
    try:
        prov = _provider(db, service, user["id"])
        tid = prov.parse_link(body.target.strip())
        if not tid:
            raise HTTPException(400, f"That's not a valid {SERVICE_NAMES[service]} link or ID.")
        t = prov.track(tid) or Track(service=service, id=tid, title="")
    except AuthError as e:
        raise HTTPException(401, str(e)) from e
    except ApiError as e:
        raise HTTPException(502, str(e)) from e
    ident = Identity(db)
    ident.pin(song_id, t, user["id"])
    return _entry(db, ident, _own_pin(db, song_id, service, user["id"]), user["id"])


@router.delete("/{song_id}/{service}")
def remove(song_id: int, service: str, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    _own_pin(db, song_id, service, user["id"])
    Identity(db).unpin(song_id, service)
    return {"ok": True}
