"""Time travel: earlier states of your own collections, and restoring one."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import history
from ..db import Database
from ..worker import get_worker
from .deps import current_user, db_dep

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("")
def collections(user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    """Every collection of yours that Crossfade has seen, with how many states it kept."""
    return history.collections(db, user["id"])


@router.get("/timeline")
def timeline(account_id: int, kind: str, collection_id: str = "", user: dict = Depends(current_user),
             db: Database = Depends(db_dep)):
    acc = db.one("SELECT id, service FROM accounts WHERE id=? AND user_id=?", (account_id, user["id"]))
    if not acc:
        raise HTTPException(404, "Collection not found.")
    return {"name": history.collection_name(db, account_id, kind, collection_id), "service": acc["service"],
            "fades": db.val("SELECT COUNT(DISTINCT line_id) FROM line_members WHERE account_id=? AND kind=? "
                            "AND collection_id=?", (account_id, kind, collection_id)),
            "snapshots": history.timeline(db, account_id, kind, collection_id)}


def _snapshot(db: Database, snapshot_id: int, user_id: int) -> dict:
    snap = history.own_snapshot(db, snapshot_id, user_id)
    if not snap:
        raise HTTPException(404, "Snapshot not found.")
    return snap


@router.get("/snapshots/{snapshot_id}")
def snapshot(snapshot_id: int, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    snap = _snapshot(db, snapshot_id, user["id"])
    return {"id": snap["id"], "taken_at": snap["taken_at"], "last_seen": snap["last_seen"], "count": snap["count"],
            "service": snap["service"], "account_id": snap["account_id"], "kind": snap["kind"],
            "collection_id": snap["collection_id"], **history.compare(db, snap)}


class RestoreBody(BaseModel):
    mode: Literal["missing", "exact"] = "missing"
    # only these entries (service ids) of the missing ones; None = all of them
    only: list[str] | None = Field(default=None, max_length=20000)


@router.post("/snapshots/{snapshot_id}/restore")
def restore(snapshot_id: int, body: RestoreBody, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    snap = _snapshot(db, snapshot_id, user["id"])
    busy = db.all("SELECT DISTINCT line_id FROM line_members WHERE account_id=? AND kind=? AND collection_id=?",
                  (snap["account_id"], snap["kind"], snap["collection_id"]))
    w = get_worker()
    if any(w.job(r["line_id"]) for r in busy):
        raise HTTPException(409, "A fade with this collection is syncing right now. Try again when it's done.")
    if not w.start_catalog(user["id"], "restore", {"snapshot_id": snapshot_id, "mode": body.mode, "only": body.only}):
        raise HTTPException(409, "Something in your library is already running. Try again when it's done.")
    return {"ok": True}
