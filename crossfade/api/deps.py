from __future__ import annotations

import time

from fastapi import Depends, HTTPException, Request

from ..config import config
from ..db import Database, get_db
from ..security import token_hash

SESSION_COOKIE = "cf_session"
# all a start password (handed over by an admin) is good for: replacing it
_BEFORE_PASSWORD_CHANGE = {"/api/auth/state", "/api/auth/logout", "/api/auth/me", "/api/auth/me/password"}


def db_dep() -> Database:
    return get_db()


def current_user(request: Request, db: Database = Depends(db_dep)) -> dict:
    tok = request.cookies.get(SESSION_COOKIE)
    if not tok:
        raise HTTPException(401, "Not signed in.")
    row = db.one("""SELECT u.* , s.expires_at FROM sessions s JOIN users u ON u.id=s.user_id
                    WHERE s.token_hash=?""", (token_hash(tok),))
    if not row or row["expires_at"] < time.time():
        raise HTTPException(401, "Your session expired. Please sign in again.")
    # sliding expiry, at most once a day
    if row["expires_at"] - time.time() < (config.session_days - 1) * 86400:
        db.run("UPDATE sessions SET expires_at=? WHERE token_hash=?",
               (time.time() + config.session_days * 86400, token_hash(tok)))
    row.pop("password_hash", None)
    if row["must_change_password"] and request.url.path not in _BEFORE_PASSWORD_CHANGE:
        raise HTTPException(403, "Choose your own password first.")
    return row


def admin_user(user: dict = Depends(current_user)) -> dict:
    if not user["is_admin"]:
        raise HTTPException(403, "Admins only.")
    return user


def public_user(u: dict) -> dict:
    return {"id": u["id"], "username": u["username"], "display_name": u["display_name"],
            "is_admin": bool(u["is_admin"]), "must_change_password": bool(u.get("must_change_password")),
            "theme": u.get("theme") or "system", "lang": u.get("lang") or "system",
            "release_pref": u.get("release_pref") or "album",
            "share_stats": bool(u.get("share_stats", 1))}
