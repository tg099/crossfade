"""Opaque, revocable MCP credentials. Browser sessions never authenticate MCP requests."""
from __future__ import annotations

import json
import time

from fastapi import HTTPException

from .db import Database
from .security import new_token, token_hash

SCOPES = {"read", "sync", "manage"}
AUDIT_KEEP = 500


def issue_token(db: Database, user_id: int, name: str, scopes: list[str], days: int) -> dict:
    name = name.strip()
    if not name or not set(scopes) <= SCOPES:
        raise HTTPException(400, "Choose a name and valid permissions.")
    if db.val("SELECT COUNT(*) FROM mcp_tokens WHERE user_id=?", (user_id,)) >= 30:
        raise HTTPException(400, "Remove an old connection before adding another one.")
    token = "cf_mcp_" + new_token()
    permissions = sorted(set(scopes) | {"read"})
    now = time.time()
    tid = db.run("INSERT INTO mcp_tokens(user_id,name,token_hash,scopes,created_at,expires_at) VALUES(?,?,?,?,?,?)",
                 (user_id, name, token_hash(token), json.dumps(permissions), now, now + days * 86400))
    return {"id": tid, "token": token, "name": name, "scopes": permissions, "expires_at": now + days * 86400}


def revoke(db: Database, user_id: int, token_id: int | None = None) -> None:
    """Remove one connection (or all of a user's) and cancel what they proposed but nobody ran yet."""
    which, args = ("token_id=? AND ", (token_id, user_id)) if token_id is not None else ("", (user_id,))
    with db.tx() as c:
        c.execute(f"UPDATE mcp_actions SET state='cancelled' WHERE {which}user_id=? AND state='pending'", args)
        c.execute(f"DELETE FROM mcp_tokens WHERE {which.replace('token_id', 'id')}user_id=?", args)


def authenticate(db: Database, authorization: str) -> dict:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.startswith("cf_mcp_") or len(token) > 200:
        raise HTTPException(401, "A personal MCP access token is required.")
    row = db.one("""SELECT u.id, u.username, u.display_name, u.must_change_password, t.id AS token_id,
                    t.name AS connection, t.scopes, t.expires_at FROM mcp_tokens t
                    JOIN users u ON u.id=t.user_id WHERE t.token_hash=?""", (token_hash(token),))
    if not row or row["expires_at"] <= time.time() or row["must_change_password"]:
        raise HTTPException(401, "This MCP connection has expired or was revoked.")
    row["scopes"] = json.loads(row["scopes"])
    db.run("UPDATE mcp_tokens SET last_used_at=? WHERE id=? AND (last_used_at IS NULL OR last_used_at<?)",
           (time.time(), row["token_id"], time.time() - 60))
    return row


def require_scope(principal: dict, scope: str) -> None:
    if scope not in principal["scopes"]:
        raise HTTPException(403, f"This connection needs the '{scope}' permission.")


def audit(db: Database, principal: dict, tool: str, status: str) -> None:
    record(db, principal["id"], principal["connection"], tool, status)


def record(db: Database, user_id: int, connection: str, tool: str, status: str) -> None:
    entry = db.run("INSERT INTO mcp_audit(user_id,connection,tool,status,created_at) VALUES(?,?,?,?,?)",
                   (user_id, connection, tool, status, time.time()))
    # Trimming on every call would rescan the user's log each time; every 50th entry is plenty.
    if entry % 50 == 0:
        db.run("DELETE FROM mcp_audit WHERE user_id=? AND id NOT IN "
               "(SELECT id FROM mcp_audit WHERE user_id=? ORDER BY id DESC LIMIT ?)",
               (user_id, user_id, AUDIT_KEEP))
