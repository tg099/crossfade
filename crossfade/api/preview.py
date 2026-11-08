"""Listening samples for the player (see crossfade/preview.py)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .. import preview as pv
from ..db import Database
from ..identity import Identity
from ..models import APPLE, SERVICES, ApiError, AuthError
from ..providers import search_provider
from ..sharing import VISIBLE
from .deps import current_user, db_dep

router = APIRouter(prefix="/api/preview", tags=["preview"])


def _lookup(db: Database, user_id: int, service: str, track_id: str) -> dict | None:
    storefront, amp = "us", None
    if service == APPLE:
        try:
            prov = search_provider(db, APPLE, user_id)
            storefront = prov.storefront()
            amp = lambda path: prov._req("GET", path, user=prov._has_user, patient=False)  # noqa: E731
        except (ApiError, AuthError, KeyError):
            pass
    return pv.preview(service, track_id, storefront, amp)


def _visible_song(db: Database, user_id: int, song_id: int) -> bool:
    """In the user's library, or in (the history of) a fade they can see – nothing else."""
    return bool(db.val(
        "SELECT 1 FROM catalog_items ci JOIN catalog_sources s ON s.id=ci.source_id "
        "JOIN accounts a ON a.id=s.account_id WHERE a.user_id=? AND ci.song_id=? LIMIT 1", (user_id, song_id))
        or db.val(f"SELECT 1 FROM line_items li JOIN lines l ON l.id=li.line_id WHERE {VISIBLE} AND li.song_id=? "
                  f"UNION SELECT 1 FROM changes c JOIN lines l ON l.id=c.line_id WHERE {VISIBLE} AND c.song_id=? LIMIT 1",
                  (user_id, user_id, song_id, user_id, user_id, song_id)))


@router.get("/song/{song_id}")
def playable(song_id: int, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    """A version of the song that has a sample: the one it came in as, else the first catalog id."""
    if not _visible_song(db, user["id"], song_id):
        raise HTTPException(404, "Unknown song.")
    ident = Identity(db)
    origin = ident.describe(song_id)
    if pv.valid_id(origin.service, origin.id):
        return origin.to_dict()
    refs = db.all("SELECT * FROM song_refs WHERE song_id=? ORDER BY service='ytm', "
                  "video_type IN ('MUSIC_VIDEO_TYPE_OMV','MUSIC_VIDEO_TYPE_UGC'), service", (song_id,))
    for r in refs:
        t = Identity._track(r)
        if pv.valid_id(t.service, t.id):
            return t.to_dict()
    raise HTTPException(404, "There's no preview for this song.")


@router.get("/{service}/{track_id}")
def get_preview(service: str, track_id: str, user: dict = Depends(current_user),
                db: Database = Depends(db_dep)):
    if service not in SERVICES or not pv.valid_id(service, track_id):
        raise HTTPException(404, "Unknown song.")
    found = _lookup(db, user["id"], service, track_id)
    if not found:
        raise HTTPException(404, "There's no preview for this song.")
    return found
