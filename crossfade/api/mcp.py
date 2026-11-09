"""Browser-only MCP connection management, approvals and authoritative music corrections."""
from __future__ import annotations

import json
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from .. import mcp_actions, music
from ..config import config
from ..db import Database
from ..mcp_access import issue_token
from .deps import current_user, db_dep

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


@router.get("")
def overview(user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    tokens = db.all("SELECT id,name,scopes,created_at,expires_at,last_used_at FROM mcp_tokens WHERE user_id=? "
                    "ORDER BY created_at DESC", (user["id"],))
    for token in tokens:
        token["scopes"] = json.loads(token["scopes"])
    return {"enabled": config.mcp_enabled, "url": config.base_url + "/mcp", "tokens": tokens,
            "actions": mcp_actions.action_summaries(db, user["id"], pending_first=True),
            "audit": db.all("SELECT connection,tool,status,created_at FROM mcp_audit WHERE user_id=? "
                            "ORDER BY id DESC LIMIT 100", (user["id"],))}


class TokenBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    scopes: list[Literal["read", "sync", "manage"]] = Field(default_factory=lambda: ["read"], max_length=3)
    days: int = Field(90, ge=1, le=365)


@router.post("/tokens")
def create_token(body: TokenBody, response: Response, user: dict = Depends(current_user),
                 db: Database = Depends(db_dep)):
    if not config.mcp_enabled:
        raise HTTPException(400, "MCP was disabled by the administrator.")
    response.headers["Cache-Control"] = "no-store"
    return issue_token(db, user["id"], body.name, body.scopes, body.days)


@router.delete("/tokens/{token_id}")
def revoke_token(token_id: int, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    with db.tx() as c:
        if not c.execute("SELECT 1 FROM mcp_tokens WHERE id=? AND user_id=?", (token_id, user["id"])).fetchone():
            raise HTTPException(404, "Connection not found.")
        c.execute("UPDATE mcp_actions SET state='cancelled' WHERE token_id=? AND user_id=? AND state='pending'",
                  (token_id, user["id"]))
        c.execute("DELETE FROM mcp_tokens WHERE id=? AND user_id=?", (token_id, user["id"]))
    return {"ok": True}


@router.get("/actions/{action_id}")
def action_detail(action_id: str, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    return mcp_actions.get_action(db, user["id"], action_id)


@router.post("/actions/{action_id}/apply")
def apply(action_id: str, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    # Deliberately authenticated by the browser's session + CSRF header, never by an MCP token.
    result = mcp_actions.apply_action(db, user, action_id, browser=True)
    db.run("INSERT INTO mcp_audit(user_id,connection,tool,status,created_at) VALUES(?, 'Crossfade', ?, 'ok', ?)",
           (user["id"], "approve:" + result["operation"], time.time()))
    return result


@router.delete("/actions/{action_id}")
def cancel(action_id: str, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    mcp_actions.get_action(db, user["id"], action_id)
    db.run("UPDATE mcp_actions SET state='cancelled' WHERE id=? AND user_id=? AND state='pending'",
           (action_id, user["id"]))
    return {"ok": True}


@router.get("/songs")
def songs(query: str = Query("", max_length=300), limit: int = Query(50, ge=1, le=250),
          offset: int = Query(0, ge=0), user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    return music.library(db, user["id"], query=query, limit=limit, offset=offset)


@router.put("/annotations/{song_id}")
def correct(song_id: int, body: music.Annotation, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    if song_id != body.song_id:
        raise HTTPException(400, "Song id does not match.")
    return music.save_annotations(db, user["id"], [body], origin="user")


@router.delete("/annotations/{song_id}")
def forget(song_id: int, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    music.require_songs(db, user["id"], [song_id])
    db.run("DELETE FROM music_annotations WHERE song_id=? AND user_id=?", (song_id, user["id"]))
    return {"ok": True}
