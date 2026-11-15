"""First-run setup, login/logout, own profile and preferences. Users are created by admins (admin.py)."""
from __future__ import annotations

import re
import threading
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..config import config
from ..db import Database
from ..mcp_access import revoke as revoke_mcp
from ..security import hash_password, new_token, token_hash, verify_password
from .deps import RETURN_COOKIE, SESSION_COOKIE, current_user, db_dep, own_user, public_user

router = APIRouter(prefix="/api/auth", tags=["auth"])

_attempts: dict[str, list[float]] = {}
_attempts_lock = threading.Lock()


def _check_limit(key: str, limit: int = 8, window: int = 600) -> None:
    """Refuse when there were too many *failed* attempts recently."""
    now = time.time()
    with _attempts_lock:
        if len(_attempts) > 10000:   # forget clients that stopped trying
            for k in [k for k, v in _attempts.items() if not v or now - v[-1] >= window]:
                del _attempts[k]
        hits = [t for t in _attempts.get(key, []) if now - t < window]
        _attempts[key] = hits
        if len(hits) >= limit:
            raise HTTPException(429, "Too many failed attempts. Wait ten minutes.")


def _failed(key: str) -> None:
    with _attempts_lock:
        _attempts.setdefault(key, []).append(time.time())


def _rate_limit(key: str, limit: int = 8, window: int = 600) -> None:
    """Count every attempt (for actions that should be rare anyway)."""
    _check_limit(key, limit, window)
    _failed(key)


def _client(request: Request) -> str:
    # uvicorn --proxy-headers already resolves the real client behind the reverse proxy
    return request.client.host if request.client else "?"


def _validate(username: str, password: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9._-]{2,32}", username):
        raise HTTPException(400, "Username: 2–32 characters, only letters, digits, dot, hyphen and underscore.")
    if len(password) < 8:
        raise HTTPException(400, "The password needs at least 8 characters.")


# an admin's visit as someone else ends by itself after this long
IMPERSONATE_HOURS = 2


def _set_cookie(response: Response, request: Request, name: str, tok: str, max_age: int) -> None:
    # Secure only when this request came over HTTPS: on Unraid people also open http://IP:8765
    # in the LAN, where a Secure cookie would silently not be stored.
    secure = config.secure_cookies and request.url.scheme == "https"
    response.set_cookie(name, tok, max_age=max_age, httponly=True, secure=secure, samesite="lax", path="/")


def _login(db: Database, response: Response, request: Request, user_id: int) -> None:
    tok = new_token()
    db.run("INSERT INTO sessions(token_hash,user_id,created_at,expires_at,user_agent) VALUES(?,?,?,?,?)",
           (token_hash(tok), user_id, time.time(), time.time() + config.session_days * 86400,
            (request.headers.get("user-agent") or "")[:200]))
    db.run("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
    _set_cookie(response, request, SESSION_COOKIE, tok, config.session_days * 86400)


def impersonate(db: Database, response: Response, request: Request, admin: dict, user_id: int) -> None:
    """Sign this browser in as `user_id` for a while. The admin's own session stays as it is and
    comes back with stop_impersonating()."""
    own = request.cookies.get(SESSION_COOKIE, "")
    tok = new_token()
    db.run("""INSERT INTO sessions(token_hash,user_id,created_at,expires_at,user_agent,impersonator_id,return_hash)
              VALUES(?,?,?,?,?,?,?)""",
           (token_hash(tok), user_id, time.time(), time.time() + IMPERSONATE_HOURS * 3600,
            (request.headers.get("user-agent") or "")[:200], admin["id"], token_hash(own)))
    db.run("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
    _set_cookie(response, request, SESSION_COOKIE, tok, IMPERSONATE_HOURS * 3600)
    _set_cookie(response, request, RETURN_COOKIE, own, config.session_days * 86400)


def _admin_session(db: Database, tok: str | None) -> dict | None:
    """The admin behind a return cookie, when that is still a valid session of an admin of their own."""
    if not tok:
        return None
    row = db.one("""SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id
                    WHERE s.token_hash=? AND s.expires_at>? AND s.impersonator_id IS NULL AND u.is_admin=1""",
                 (token_hash(tok), time.time()))
    if row:
        row.pop("password_hash", None)
    return row


def browser_lang(request: Request) -> str | None:
    """de or en from Accept-Language, whichever comes first; None when neither is there."""
    for part in (request.headers.get("accept-language") or "").split(","):
        code = part.split(";")[0].strip().lower()[:2]
        if code in ("de", "en"):
            return code
    return None


def _remember_lang(db: Database, request: Request, user: dict) -> None:
    """Notifications of a user on "system" follow the browser they last opened Crossfade in."""
    if user.get("impersonator_id"):
        return             # that's the admin's browser, not theirs
    lang = browser_lang(request)
    if lang and lang != user.get("browser_lang"):
        db.run("UPDATE users SET browser_lang=? WHERE id=?", (lang, user["id"]))


@router.get("/state")
def auth_state(request: Request, db: Database = Depends(db_dep)):
    setup_needed = not db.val("SELECT COUNT(*) FROM users")
    user = None
    if not setup_needed and request.cookies.get(SESSION_COOKIE):
        try:
            row = current_user(request, db)
            _remember_lang(db, request, row)
            user = public_user(row)
        except HTTPException:
            user = None
    return {"setup_needed": setup_needed, "user": user}


# passwords are hashed with scrypt: cap their length so a huge one can't tie up the server
Password = Field(max_length=256)


class SetupBody(BaseModel):
    username: str = Field(max_length=32)
    display_name: str = Field(min_length=1, max_length=60)
    password: str = Password


@router.post("/setup")
def setup(body: SetupBody, request: Request, response: Response, db: Database = Depends(db_dep)):
    _validate(body.username, body.password)
    with db.tx() as c:
        if c.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            raise HTTPException(409, "This instance is already set up.")
        uid = c.execute("INSERT INTO users(username,display_name,password_hash,is_admin,created_at) "
                        "VALUES(?,?,?,1,?)", (body.username, body.display_name.strip(),
                                              hash_password(body.password), time.time())).lastrowid
    _login(db, response, request, uid)
    _remember_lang(db, request, {"id": uid})
    return {"user": public_user(db.one("SELECT * FROM users WHERE id=?", (uid,)))}


class LoginBody(BaseModel):
    username: str = Field(max_length=64)
    password: str = Password


# checked when the username is unknown, so a wrong name takes as long as a wrong password
_DUMMY_HASH = hash_password(new_token())


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response, db: Database = Depends(db_dep)):
    key = "login:" + _client(request)
    # also per account: the client address can be spoofed by anyone allowed to set X-Forwarded-For
    account = "account:" + body.username.strip().lower()
    _check_limit(key)
    _check_limit(account, limit=30)
    u = db.one("SELECT * FROM users WHERE username=?", (body.username.strip(),))
    if not verify_password(body.password, u["password_hash"] if u else _DUMMY_HASH) or not u:
        _failed(key)
        _failed(account)
        raise HTTPException(401, "Wrong username or password.")
    _login(db, response, request, u["id"])
    _remember_lang(db, request, u)
    return {"user": public_user(u)}


@router.post("/logout")
def logout(request: Request, response: Response, db: Database = Depends(db_dep)):
    # while signed in as someone else, signing out ends the admin's own session too
    for name in (SESSION_COOKIE, RETURN_COOKIE):
        tok = request.cookies.get(name)
        if tok:
            db.run("DELETE FROM sessions WHERE token_hash=?", (token_hash(tok),))
        response.delete_cookie(name, path="/")
    return {"ok": True}


@router.post("/impersonate/stop")
def stop_impersonating(request: Request, response: Response, db: Database = Depends(db_dep)):
    """Back to the admin's own account (also works when the visit already ran out)."""
    tok = request.cookies.get(SESSION_COOKIE)
    ret = request.cookies.get(RETURN_COOKIE)
    visit = db.one("SELECT impersonator_id, return_hash FROM sessions WHERE token_hash=?",
                   (token_hash(tok),)) if tok else None
    if visit and visit["impersonator_id"] is None:
        raise HTTPException(400, "You're signed in as yourself.")
    if tok:
        db.run("DELETE FROM sessions WHERE token_hash=? AND impersonator_id IS NOT NULL", (token_hash(tok),))
    response.delete_cookie(RETURN_COOKIE, path="/")
    admin = _admin_session(db, ret)
    if not admin or (visit and visit["return_hash"] != token_hash(ret or "")):
        response.delete_cookie(SESSION_COOKIE, path="/")
        raise HTTPException(401, "Please sign in again.")
    _set_cookie(response, request, SESSION_COOKIE, ret, config.session_days * 86400)
    return {"user": public_user(admin)}


class ProfileBody(BaseModel):
    display_name: str | None = Field(None, min_length=1, max_length=60)
    theme: Literal["system", "light", "dark"] | None = None
    lang: Literal["system", "de", "en"] | None = None
    release_pref: Literal["album", "single"] | None = None
    share_stats: bool | None = None
    intro_done: bool | None = None


@router.put("/me")
def update_me(body: ProfileBody, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    """Own name, appearance, language and preferred release (each user chooses their own)."""
    changes = {k: (v.strip() if isinstance(v, str) and k == "display_name" else v)
               for k, v in body.model_dump().items() if v is not None}
    if changes:
        db.run(f"UPDATE users SET {', '.join(f'{k}=?' for k in changes)} WHERE id=?", (*changes.values(), user["id"]))
    return {"user": public_user({**user, **changes})}


class PasswordBody(BaseModel):
    current: str = Password
    new: str = Password


@router.put("/me/password")
def change_password(body: PasswordBody, request: Request, user: dict = Depends(own_user),
                    db: Database = Depends(db_dep)):
    _rate_limit("pw:" + str(user["id"]))
    u = db.one("SELECT * FROM users WHERE id=?", (user["id"],))
    if not verify_password(body.current, u["password_hash"]):
        raise HTTPException(400, "Your current password is wrong.")
    _validate(u["username"], body.new)
    if body.new == body.current:
        raise HTTPException(400, "Choose a new password.")
    db.run("UPDATE users SET password_hash=?, must_change_password=0 WHERE id=?", (hash_password(body.new), user["id"]))
    # sign out other sessions
    db.run("DELETE FROM sessions WHERE user_id=? AND token_hash<>?",
           (user["id"], token_hash(request.cookies.get(SESSION_COOKIE, ""))))
    revoke_mcp(db, user["id"])
    return {"ok": True}
