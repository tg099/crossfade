"""Your notification channels (ntfy, webhook, e-mail) and which events each one gets."""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import notify
from ..db import Database
from ..security import decrypt, encrypt
from .deps import current_user, db_dep

router = APIRouter(prefix="/api/notifications", tags=["notifications"])
MAX_CHANNELS = 10


def _out(row: dict) -> dict:
    cfg = decrypt(row["config"])
    return {"id": row["id"], "kind": row["kind"], "config": notify.public_config(row["kind"], cfg),
            "events": json.loads(row["events"]), "enabled": bool(row["enabled"]), "created_at": row["created_at"],
            "last_sent_at": row["last_sent_at"], "last_error": row["last_error"],
            "flavor": notify.webhook_flavor(cfg["url"]) if row["kind"] == "webhook" else None}


def _channel(db: Database, channel_id: int, user_id: int) -> dict:
    row = db.one("SELECT * FROM notify_channels WHERE id=? AND user_id=?", (channel_id, user_id))
    if not row:
        raise HTTPException(404, "Channel not found.")
    return row


def _events(events: list[str]) -> str:
    bad = [e for e in events if e not in notify.EVENTS]
    if bad:
        raise HTTPException(400, f"Unknown event: {bad[0]}")
    return json.dumps(sorted(set(events), key=notify.EVENTS.index))


@router.get("")
def overview(user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    rows = db.all("SELECT * FROM notify_channels WHERE user_id=? ORDER BY id", (user["id"],))
    return {"channels": [_out(r) for r in rows], "events": list(notify.EVENTS),
            "email": notify.smtp_settings(db) is not None, "ntfy_default": notify.NTFY_DEFAULT}


class ChannelIn(BaseModel):
    kind: str
    config: dict = Field(default_factory=dict)
    events: list[str] = Field(default_factory=lambda: list(notify.EVENTS))


@router.post("/channels")
def create(body: ChannelIn, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    if db.val("SELECT COUNT(*) FROM notify_channels WHERE user_id=?", (user["id"],)) >= MAX_CHANNELS:
        raise HTTPException(400, f"At most {MAX_CHANNELS} channels.")
    if body.kind == "email" and notify.smtp_settings(db) is None:
        raise HTTPException(400, "E-mail isn't set up on this instance. An admin can add a mail server.")
    try:
        cfg = notify.clean_config(body.kind, body.config)
    except notify.ChannelError as e:
        raise HTTPException(400, str(e)) from e
    cid = db.run("INSERT INTO notify_channels(user_id,kind,config,events,enabled,created_at) VALUES(?,?,?,?,1,?)",
                 (user["id"], body.kind, encrypt(cfg), _events(body.events), time.time()))
    return _out(_channel(db, cid, user["id"]))


class ChannelEdit(BaseModel):
    config: dict | None = None
    events: list[str] | None = None
    enabled: bool | None = None


@router.put("/channels/{channel_id}")
def update(channel_id: int, body: ChannelEdit, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    row = _channel(db, channel_id, user["id"])
    if body.config is not None:
        old = decrypt(row["config"])
        merged = {**old, **{k: v for k, v in body.config.items() if v is not None}}
        if row["kind"] == "ntfy" and body.config.get("token") == "":
            merged["token"] = None          # "" removes the token, None keeps it
        try:
            cfg = notify.clean_config(row["kind"], merged)
        except notify.ChannelError as e:
            raise HTTPException(400, str(e)) from e
        db.run("UPDATE notify_channels SET config=?, last_error=NULL WHERE id=?", (encrypt(cfg), channel_id))
    if body.events is not None:
        db.run("UPDATE notify_channels SET events=? WHERE id=?", (_events(body.events), channel_id))
    if body.enabled is not None:
        db.run("UPDATE notify_channels SET enabled=? WHERE id=?", (int(body.enabled), channel_id))
    return _out(_channel(db, channel_id, user["id"]))


@router.delete("/channels/{channel_id}")
def remove(channel_id: int, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    _channel(db, channel_id, user["id"])
    db.run("DELETE FROM notify_channels WHERE id=?", (channel_id,))
    return {"ok": True}


@router.post("/channels/{channel_id}/test")
def test(channel_id: int, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    err = notify.send_test(db, _channel(db, channel_id, user["id"]))
    return {"ok": err is None, "error": err}
