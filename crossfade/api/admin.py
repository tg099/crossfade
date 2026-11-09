"""Admin: users (only admins create and manage them), app credentials for Spotify/Google."""
from __future__ import annotations

import os
import platform
import re
import secrets
import shutil
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import backup, updates
from .. import notify as alert
from ..config import config
from ..db import Database
from ..models import APPLE, SERVICE_NAMES, SERVICES
from ..providers import APP_KEYS, app_credential, app_credential_source, set_app_credential
from ..security import hash_password, token_hash
from ..sharing import notify, participants
from ..worker import get_worker
from .deps import SESSION_COOKIE, admin_user, db_dep

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
    db.run("DELETE FROM mcp_tokens WHERE user_id=?", (user_id,))
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
    out["tidal_redirect_uri"] = f"{config.base_url}/api/services/tidal/callback"
    out["base_url"] = config.base_url
    return out


class AppBody(BaseModel):
    spotify_client_id: str | None = None
    spotify_client_secret: str | None = None
    google_client_id: str | None = None
    google_client_secret: str | None = None
    tidal_client_id: str | None = None
    tidal_client_secret: str | None = None


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
def test_smtp(body: SmtpTest, admin: dict = Depends(admin_user), db: Database = Depends(db_dep)):
    s = alert.smtp_settings(db)
    if not s:
        raise HTTPException(400, "Save the mail server first.")
    try:
        cfg = alert.clean_config("email", {"to": body.to})
        title, message = alert.text(alert.language(db, admin["id"]), "smtp_test")
        alert._send("email", cfg, {"event": "test", "title": title, "message": message, "url": config.base_url}, s)
    except alert.ChannelError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
    return {"ok": True, "error": None}


# ---------------------------------------------------------------------- health
@router.get("/health")
def instance_health(_: dict = Depends(admin_user), db: Database = Depends(db_dep)):
    """How the instance and the services are doing. Counts only: no songs, no fade names, no
    account details of other users."""
    from .. import health
    from ..providers.apple import AppleProvider, jwt_expiry
    health.flush(db)
    now = time.time()
    hour = health._hour(now)
    w = get_worker()

    services = []
    for svc in SERVICES:
        rows = {r["hour"]: r for r in db.all("SELECT * FROM service_stats WHERE service=? AND hour>?",
                                             (svc, hour - 7 * 24))}

        def total(hours: int, rows=rows) -> dict:
            sel = [r for h, r in rows.items() if h > hour - hours]
            out = {f: sum(r[f] for r in sel) for f in health.FIELDS}
            out["avg_ms"] = round(out.pop("ms") / out["calls"]) if out["calls"] else None
            return out
        acc = db.one("""SELECT COUNT(*) AS connected, SUM(status='error') AS failing,
                        SUM(expires_at IS NOT NULL AND expires_at < ?) AS expiring,
                        MIN(checked_at) AS oldest_check FROM accounts WHERE service=? AND credentials IS NOT NULL""",
                     (now + 7 * 86400, svc))
        services.append({
            "service": svc, "name": SERVICE_NAMES[svc], "day": total(24), "week": total(24 * 7),
            "hours": [{"hour": h * 3600, "calls": rows[h]["calls"] if h in rows else 0,
                       "errors": (rows[h]["errors"] + rows[h]["auth_errors"]) if h in rows else 0}
                      for h in range(hour - 23, hour + 1)],
            "accounts": {"connected": acc["connected"] or 0, "failing": acc["failing"] or 0,
                         "expiring": acc["expiring"] or 0, "oldest_check": acc["oldest_check"]},
        })
    apple = next(s for s in services if s["service"] == APPLE)
    dev = db.setting("apple_dev_token")
    blocked = AppleProvider._amp_blocked_until
    apple["notes"] = {"search_blocked_until": blocked if blocked > now else None,
                      "web_token_expires": jwt_expiry(dev) if dev else None}

    runs = db.all("""SELECT status, COUNT(*) AS n, AVG(finished_at-started_at) AS secs FROM runs
                     WHERE trigger<>'preview' AND started_at>? AND finished_at IS NOT NULL GROUP BY status""",
                  (now - 86400,))
    fades = db.one("""SELECT COUNT(*) AS total, SUM(paused) AS paused,
                      SUM(paused=0 AND last_status IN ('error','auth_error')) AS failing,
                      SUM(paused=0 AND last_status IN ('needs_confirm','blocked')) AS waiting FROM lines""")
    overdue = db.val("""SELECT COUNT(*) FROM lines WHERE paused=0 AND interval_minutes>0 AND last_run_at IS NOT NULL
                        AND last_run_at + interval_minutes*60*3 < ?""", (now,))

    files = {p.name: p.stat().st_size for p in config.data_dir.glob("crossfade.sqlite3*") if p.is_file()}
    backups = sorted((config.data_dir / "backups").glob("crossfade-*.sqlite3"), key=lambda p: p.stat().st_mtime)
    disk = shutil.disk_usage(config.data_dir)
    counts = {k: db.val(f"SELECT COUNT(*) FROM {t}") for k, t in
              (("users", "users"), ("songs", "songs"), ("refs", "song_refs"), ("cached_searches", "search_cache"),
               ("snapshots", "snapshots"))
              if db.val("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,))}
    return {
        "now": now,
        "instance": {"version": config.version, "started_at": health.STARTED_AT, "scheduler": config.scheduler,
                     "workers": config.worker_threads, "python": platform.python_version(),
                     "schema": db.val("PRAGMA user_version"),
                     "database_bytes": sum(files.values()), "disk_free_bytes": disk.free, "disk_total_bytes": disk.total,
                     "backups": len(backups),
                     "last_backup": {"at": backups[-1].stat().st_mtime, "bytes": backups[-1].stat().st_size}
                     if backups else None, "counts": counts},
        "services": services,
        "syncs": {"day": {r["status"]: {"n": r["n"], "avg_secs": round(r["secs"] or 0, 1)} for r in runs},
                  "running": sum(1 for j in list(w.jobs.values()) if j.get("state") == "running"),
                  "queued": sum(1 for j in list(w.jobs.values()) if j.get("state") == "queued"),
                  "library_jobs": len(w.catalog_jobs),
                  "fades": {k: fades[k] or 0 for k in ("total", "paused", "failing", "waiting")},
                  "overdue": overdue or 0},
        "errors": health.recent_errors(),
    }


# ---------------------------------------------------------------------- backups
MAX_UPLOAD = 4 * 1024 ** 3


@router.get("/backups")
def backups(_: dict = Depends(admin_user), db: Database = Depends(db_dep)):
    return {"backups": backup.listing(db), "daily": config.backup_daily, "on_start": config.backup_on_start,
            "key_from_env": bool(os.environ.get("CROSSFADE_SECRET_KEY")), "keep": backup.KEEP,
            "schema": db.val("PRAGMA user_version")}


@router.post("/backups")
def make_backup(_: dict = Depends(admin_user), db: Database = Depends(db_dep)):
    if get_worker().restoring:
        raise HTTPException(409, "A restore is running.")
    target = db.backup("manual")
    if not target:
        raise HTTPException(400, "There is nothing to back up yet.")
    return backups(_, db)


def _backup_file(db: Database, name: str) -> Path:
    try:
        return backup.path_of(db, name)
    except backup.BackupError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/backups/{name}")
def download_backup(name: str, _: dict = Depends(admin_user), db: Database = Depends(db_dep)):
    return FileResponse(_backup_file(db, name), media_type="application/vnd.sqlite3", filename=name)


@router.delete("/backups/{name}")
def delete_backup(name: str, _: dict = Depends(admin_user), db: Database = Depends(db_dep)):
    _backup_file(db, name).unlink(missing_ok=True)
    return backups(_, db)


def _restore(db: Database, request: Request, path: Path) -> dict:
    w = get_worker()
    if not w.begin_restore():
        raise HTTPException(409, "Something is syncing right now. Try again when it's done.")
    try:
        tok = request.cookies.get(SESSION_COOKIE)
        info = backup.restore(db, path, keep_session=token_hash(tok) if tok else None)
    except backup.BackupError as e:
        raise HTTPException(400, str(e)) from e
    finally:
        w.end_restore()
    # every open app reloads: what it shows is gone
    for uid in [r["id"] for r in db.all("SELECT id FROM users")]:
        w.bus.publish(uid, {"type": "restored"})
    return info


@router.post("/backups/{name}/restore")
def restore_backup(name: str, request: Request, _: dict = Depends(admin_user), db: Database = Depends(db_dep)):
    return _restore(db, request, _backup_file(db, name))


@router.post("/backup-upload/check")
async def check_upload(request: Request, _: dict = Depends(admin_user), db: Database = Depends(db_dep)):
    """What an uploaded backup holds, before anything is replaced."""
    path = await _receive(request, db)
    try:
        return backup.inspect(path)
    except backup.BackupError as e:
        raise HTTPException(400, str(e)) from e
    finally:
        path.unlink(missing_ok=True)


@router.post("/backup-upload/restore")
async def restore_upload(request: Request, _: dict = Depends(admin_user), db: Database = Depends(db_dep)):
    path = await _receive(request, db)
    try:
        import asyncio
        return await asyncio.to_thread(_restore, db, request, path)
    finally:
        path.unlink(missing_ok=True)


async def _receive(request: Request, db: Database) -> Path:
    """The request body (the raw file) into a private temporary file next to the backups."""
    folder = backup.folder_of(db.path)
    folder.mkdir(exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".upload-", suffix=".sqlite3", dir=folder)
    size = 0
    try:
        with os.fdopen(fd, "wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(413, "The file is too big.")
                out.write(chunk)
        if size < 512:
            raise HTTPException(400, "This isn't a Crossfade backup.")
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    return Path(name)


# ---------------------------------------------------------------------- updates
@router.get("/update")
def update_state(_: dict = Depends(admin_user), db: Database = Depends(db_dep)):
    return updates.state(db)


@router.post("/update/check")
def update_check(_: dict = Depends(admin_user), db: Database = Depends(db_dep)):
    if not updates.enabled():
        return updates.state(db)
    return updates.check(db)
