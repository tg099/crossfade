from __future__ import annotations

import time

from fastapi import Depends, HTTPException, Request

from ..config import config
from ..db import Database, get_db
from ..security import token_hash

SESSION_COOKIE = "cf_session"
# while an admin is signed in as someone else: the admin's own session, to go back to
RETURN_COOKIE = "cf_return"
# all a start password (handed over by an admin) is good for: replacing it
_BEFORE_PASSWORD_CHANGE = {"/api/auth/state", "/api/auth/logout", "/api/auth/me", "/api/auth/me/password"}


def db_dep() -> Database:
    return get_db()


def current_user(request: Request, db: Database = Depends(db_dep)) -> dict:
    tok = request.cookies.get(SESSION_COOKIE)
    if not tok:
        raise HTTPException(401, "Not signed in.")
    row = db.one("""SELECT u.*, s.expires_at, s.impersonator_id, i.display_name AS impersonator_name,
                           i.is_admin AS impersonator_is_admin
                    FROM sessions s JOIN users u ON u.id=s.user_id LEFT JOIN users i ON i.id=s.impersonator_id
                    WHERE s.token_hash=?""", (token_hash(tok),))
    if not row or row["expires_at"] < time.time():
        raise HTTPException(401, "Your session expired. Please sign in again.")
    impersonated = row["impersonator_id"] is not None
    if impersonated and not row["impersonator_is_admin"]:
        # whoever signed in as this person is no admin any more
        db.run("DELETE FROM sessions WHERE token_hash=?", (token_hash(tok),))
        raise HTTPException(401, "Your session expired. Please sign in again.")
    # sliding expiry, at most once a day (an admin's visit as someone else has a fixed end)
    if not impersonated and row["expires_at"] - time.time() < (config.session_days - 1) * 86400:
        db.run("UPDATE sessions SET expires_at=? WHERE token_hash=?",
               (time.time() + config.session_days * 86400, token_hash(tok)))
    row.pop("password_hash", None)
    row.pop("impersonator_is_admin", None)
    # the start password is the person's own business: an admin setting things up isn't held back by it
    if row["must_change_password"] and not impersonated and request.url.path not in _BEFORE_PASSWORD_CHANGE:
        raise HTTPException(403, "Choose your own password first.")
    return row


def own_user(user: dict = Depends(current_user)) -> dict:
    """For what only the person themselves may do (password, MCP credentials), never an admin
    signed in as them."""
    if user.get("impersonator_id"):
        raise HTTPException(403, "Only the person themselves can do this.")
    return user


def admin_user(user: dict = Depends(current_user)) -> dict:
    if not user["is_admin"] or user.get("impersonator_id"):
        raise HTTPException(403, "Admins only.")
    return user


def public_user(u: dict) -> dict:
    out = {"id": u["id"], "username": u["username"], "display_name": u["display_name"],
           "is_admin": bool(u["is_admin"]), "must_change_password": bool(u.get("must_change_password")) and not u.get("impersonator_id"),
           "theme": u.get("theme") or "system", "lang": u.get("lang") or "system",
           "release_pref": u.get("release_pref") or "album",
           "share_stats": bool(u.get("share_stats", 1)), "intro_done": bool(u.get("intro_done", 0))}
    if u.get("impersonator_id"):
        out["impersonator"] = u.get("impersonator_name")
    return out
