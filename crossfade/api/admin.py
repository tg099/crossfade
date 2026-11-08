"""Admin: users (only admins create and manage them), app credentials for Spotify/Google."""
from __future__ import annotations

import re
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import notify as alert
from ..config import config
from ..db import Database
from ..providers import APP_KEYS, app_credential, app_credential_source, set_app_credential
from ..security import hash_password
from ..sharing import notify, participants
from ..worker import get_worker
from .deps import admin_user, db_dep

router = APIRouter(prefix="/api/admin", tags=["admin"])

# Start passwords are handed over in person and must be replaced at the first sign-in:
# easy to read out loud, still ~25 bits plus the rate-limited login.
_WORDS = ("amber atlas birch cedar cloud comet coral delta dune ember fern fjord flint frost glade grove "
          "harbor hazel iris ivory jade juniper kelp lagoon lark lotus lumen maple marble meadow mint moss "
          "nova oasis ocean olive onyx opal orbit pebble pine pixel polar prism quartz raven reef river "
          "sage sonar spruce stone summit tempo thistle tidal tulip velvet willow wren zephyr").split()


def _start_password() -> str:
    words = [secrets.choice(_WORDS) for _ in range(3)]
    return f"{'-'.join(words)}-{secrets.randbelow(90) + 10}"


def _check_username(db: Database, username: str, user_id: int | None = None) -> str:
    username = username.strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{2,32}", username):
        raise HTTPException(400, "Username: 2–32 characters, only letters, digits, dot, hyphen and underscore.")
    other = db.one("SELECT id FROM users WHERE username=?", (username,))
    if other and other["id"] != user_id:
        raise HTTPException(409, "This username is already taken.")
    return username


def _user_or_404(db: Database, user_id: int) -> dict:
    u = db.one("SELECT * FROM users WHERE id=?", (user_id,))
    if not u:
        raise HTTPException(404, "User not found.")
    return u


@router.get("/users")
def users(_: dict = Depends(admin_user), db: Database = Depends(db_dep)):
    rows = db.all("""SELECT u.id, u.username, u.display_name, u.is_admin, u.must_change_password, u.created_at,
                     (SELECT COUNT(*) FROM lines l WHERE l.user_id=u.id) AS lines,
                     (SELECT GROUP_CONCAT(a.service) FROM accounts a
                      WHERE a.user_id=u.id AND a.credentials IS NOT NULL) AS services
                     FROM users u ORDER BY u.created_at""")
    for r in rows:
        r["is_admin"] = bool(r["is_admin"])
        r["must_change_password"] = bool(r["must_change_password"])
        r["services"] = (r["services"] or "").split(",") if r["services"] else []
    return rows


class NewUser(BaseModel):
    display_name: str = Field(min_length=1, max_length=60)
    username: str
    is_admin: bool = False


@router.post("/users")
def create_user(body: NewUser, _: dict = Depends(admin_user), db: Database = Depends(db_dep)):
    username = _check_username(db, body.username)
    password = _start_password()
    uid = db.run("""INSERT INTO users(username,display_name,password_hash,is_admin,must_change_password,created_at)
                    VALUES(?,?,?,?,1,?)""", (username, body.display_name.strip(), hash_password(password),
                                            int(body.is_admin), time.time()))
    return {"id": uid, "password": password}


class UserEdit(BaseModel):
    display_name: str = Field(min_length=1, max_length=60)
    username: str


@router.put("/users/{user_id}")
def update_user(user_id: int, body: UserEdit, _: dict = Depends(admin_user), db: Database = Depends(db_dep)):
    _user_or_404(db, user_id)
    username = _check_username(db, body.username, user_id)
    db.run("UPDATE users SET display_name=?, username=? WHERE id=?", (body.display_name.strip(), username, user_id))
    return {"ok": True}


@router.post("/users/{user_id}/password")
def reset_password(user_id: int, admin: dict = Depends(admin_user), db: Database = Depends(db_dep)):
    if user_id == admin["id"]:
        raise HTTPException(400, "Change your own password in your account.")
    _user_or_404(db, user_id)
    password = _start_password()
    db.run("UPDATE users SET password_hash=?, must_change_password=1 WHERE id=?", (hash_password(password), user_id))
    db.run("DELETE FROM sessions WHERE user_id=?", (user_id,))   # signed out everywhere
    return {"password": password}


@router.delete("/users/{user_id}")
def delete_user(user_id: int, admin: dict = Depends(admin_user), db: Database = Depends(db_dep)):
    if user_id == admin["id"]:
        raise HTTPException(400, "You can't remove yourself.")
    # their fades and their collections in friends' fades go with them
    affected = [r["id"] for r in db.all("""SELECT id FROM lines WHERE user_id=? UNION
                                           SELECT m.line_id FROM line_members m JOIN accounts a ON a.id=m.account_id
                                           WHERE a.user_id=?""", (user_id, user_id))]
    w = get_worker()
    if any(w.job(lid) for lid in affected) or w.catalog_job(user_id):
        raise HTTPException(409, "Something of this user is syncing right now. Try again in a moment.")
    in_fades = set().union(*(participants(db, lid) for lid in affected)) - {user_id}
    friends = {r["uid"] for r in db.all("""SELECT CASE WHEN requester_id=? THEN addressee_id ELSE requester_id END AS uid
                                           FROM friendships WHERE requester_id=? OR addressee_id=?""",
                                        (user_id, user_id, user_id))}
    db.run("DELETE FROM users WHERE id=?", (user_id,))
    notify(in_fades, "fades")
    notify(friends - in_fades)
    return {"ok": True}


class AdminFlag(BaseModel):
    is_admin: bool


@router.put("/users/{user_id}/admin")
def set_admin(user_id: int, body: AdminFlag, admin: dict = Depends(admin_user), db: Database = Depends(db_dep)):
    if user_id == admin["id"] and not body.is_admin:
        raise HTTPException(400, "You can't take away your own admin rights.")
    if not body.is_admin and db.val("SELECT COUNT(*) FROM users WHERE is_admin=1 AND id<>?", (user_id,)) == 0:
        raise HTTPException(400, "There must be at least one admin.")
    db.run("UPDATE users SET is_admin=? WHERE id=?", (int(body.is_admin), user_id))
    return {"ok": True}


@router.get("/apps")
def app_settings(_: dict = Depends(admin_user), db: Database = Depends(db_dep)):
    out = {}
    for key in APP_KEYS:
        src = app_credential_source(db, key)
        val = app_credential(db, key)
        public = key.endswith("_id")
        out[key] = {"set": bool(val), "source": src, "value": val if public else None}
    out["spotify_redirect_uri"] = f"{config.base_url}/api/services/spotify/callback"
    out["base_url"] = config.base_url
    return out


class AppBody(BaseModel):
    spotify_client_id: str | None = None
    spotify_client_secret: str | None = None
    google_client_id: str | None = None
    google_client_secret: str | None = None


@router.put("/apps")
def save_app_settings(body: AppBody, _: dict = Depends(admin_user), db: Database = Depends(db_dep)):
    for key, value in body.model_dump().items():
        if value is None:
            continue           # untouched
        if app_credential_source(db, key) == "env":
            continue           # environment wins
        set_app_credential(db, key, value.strip() or None)
    return {"ok": True}


# ---------------------------------------------------------------------- e-mail for notifications
@router.get("/smtp")
def smtp(_: dict = Depends(admin_user), db: Database = Depends(db_dep)):
    s = alert.smtp_settings(db)
    if not s:
        return {"set": False}
    return {"set": True, **{k: s.get(k) for k in alert.SMTP_KEYS}, "password_set": bool(s.get("password"))}


class SmtpBody(BaseModel):
    host: str = Field(default="", max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    security: str = "starttls"
    username: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, max_length=1000)   # None = keep, "" = remove
    sender: str = Field(default="", max_length=320)


@router.put("/smtp")
def save_smtp(body: SmtpBody, _: dict = Depends(admin_user), db: Database = Depends(db_dep)):
    try:
        alert.save_smtp(db, body.model_dump())
    except alert.ChannelError as e:
        raise HTTPException(400, str(e)) from e
    return smtp(_, db)


class SmtpTest(BaseModel):
    to: str = Field(min_length=3, max_length=254)


@router.post("/smtp/test")
def test_smtp(body: SmtpTest, _: dict = Depends(admin_user), db: Database = Depends(db_dep)):
    s = alert.smtp_settings(db)
    if not s:
        raise HTTPException(400, "Save the mail server first.")
    try:
        cfg = alert.clean_config("email", {"to": body.to})
        alert._send("email", cfg, {"event": "test", "title": "Test from Crossfade",
                                    "message": "The mail server works.", "url": config.base_url}, s)
    except alert.ChannelError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
    return {"ok": True, "error": None}
