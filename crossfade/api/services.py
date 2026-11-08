"""Connecting services and browsing their collections."""
from __future__ import annotations

import secrets
import threading
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from .. import notify
from ..config import config
from ..db import Database
from ..models import APPLE, SERVICE_NAMES, SERVICES, SPOTIFY, YTM, ApiError, AuthError
from ..providers import app_credential, build_provider, make_provider
from ..providers import spotify as sp
from ..providers.apple import clean_user_token
from ..providers.ytm import DeviceFlow, parse_pasted
from ..security import encrypt
from ..worker import mark_account
from .deps import current_user, db_dep

router = APIRouter(prefix="/api/services", tags=["services"])

# short-lived login flows (in memory; a restart just means "start again")
_flows: dict[str, dict] = {}
_flows_lock = threading.Lock()
_collections_cache: dict[int, tuple[float, list]] = {}


def _put_flow(data: dict, ttl: int = 900) -> str:
    key = secrets.token_urlsafe(24)
    with _flows_lock:
        now = time.time()
        for k in [k for k, v in _flows.items() if v["expires"] < now]:
            _flows.pop(k, None)
        _flows[key] = {**data, "expires": now + ttl}
    return key


def _get_flow(key: str, user_id: int, kind: str) -> dict:
    f = _flows.get(key)
    if not f or f["expires"] < time.time() or f["user_id"] != user_id or f["kind"] != kind:
        raise HTTPException(404, "This sign-in has expired. Please start again.")
    return f


def _save_account(db: Database, user_id: int, service: str, creds: dict, name: str) -> dict:
    now = time.time()
    old = db.one("SELECT id, display_name FROM accounts WHERE user_id=? AND service=?", (user_id, service))
    if old and old["display_name"] and old["display_name"] != name:
        _forget_account_state(db, old["id"])
    try:
        expires = make_provider(db, service, creds).login_expires_at()
    except Exception:  # noqa: BLE001 – only a hint
        expires = None
    db.run("""INSERT INTO accounts(user_id,service,display_name,credentials,status,status_message,created_at,updated_at,
              connected_at,checked_at,expires_at)
              VALUES(?,?,?,?, 'ok', NULL, ?, ?, ?, ?, ?)
              ON CONFLICT(user_id,service) DO UPDATE SET display_name=excluded.display_name,
              credentials=excluded.credentials, status='ok', status_message=NULL, updated_at=excluded.updated_at,
              connected_at=excluded.connected_at, checked_at=excluded.checked_at, expires_at=excluded.expires_at""",
           (user_id, service, name, encrypt(creds), now, now, now, now, expires))
    acc_id = db.val("SELECT id FROM accounts WHERE user_id=? AND service=?", (user_id, service))
    _collections_cache.pop(acc_id, None)
    # a fresh login: what was reported about the old one is over
    for key in (f"auth:{acc_id}", f"expiring:{acc_id}"):
        notify.resolve(db, user_id, key)
    return {"service": service, "display_name": name}


def _forget_account_state(db: Database, account_id: int) -> None:
    """Another account of the same service was connected: what the fades saw in the old one says
    nothing about the new one. Without this, the old account's songs would count as removed."""
    with db.tx() as c:
        members = "SELECT id FROM line_members WHERE account_id=?"
        c.execute(f"DELETE FROM member_items WHERE member_id IN ({members})", (account_id,))
        c.execute(f"DELETE FROM issues WHERE member_id IN ({members})", (account_id,))
        c.execute("UPDATE line_members SET initialized=0, item_count=NULL WHERE account_id=?", (account_id,))
        c.execute("DELETE FROM catalog_sources WHERE account_id=?", (account_id,))


def _verify(db: Database, service: str, creds: dict) -> str:
    try:
        return make_provider(db, service, creds).whoami()
    except AuthError as e:
        raise HTTPException(400, str(e)) from e
    except ApiError as e:
        raise HTTPException(502, str(e)) from e


# ---------------------------------------------------------------------- overview
@router.get("")
def services(user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    accounts = {a["service"]: a for a in db.all("SELECT * FROM accounts WHERE user_id=?", (user["id"],))}
    out = []
    for svc in SERVICES:
        a = accounts.get(svc)
        connected = bool(a and a["credentials"])
        methods: list[str] = []
        if svc == SPOTIFY and app_credential(db, "spotify_client_id"):
            methods.append("oauth")
        if svc == YTM:
            if app_credential(db, "google_client_id") and app_credential(db, "google_client_secret"):
                methods.append("device")
            methods.append("paste")
        if svc == APPLE:
            methods.append("token")
        lines = db.val("""SELECT COUNT(DISTINCT m.line_id) FROM line_members m WHERE m.account_id=?""",
                       (a["id"],)) if a else 0
        out.append({
            "service": svc, "name": SERVICE_NAMES[svc], "connected": connected,
            "account_id": a["id"] if a else None, "display_name": a["display_name"] if a else None,
            "status": (a["status"] if connected else "disconnected") if a else "disconnected",
            "status_message": a["status_message"] if a else None,
            "updated_at": a["updated_at"] if a else None, "methods": methods, "lines": lines,
            "checked_at": a["checked_at"] if a else None, "expires_at": a["expires_at"] if connected else None,
            "connected_at": a["connected_at"] if a else None,
            "admin_setup_needed": svc == SPOTIFY and not methods,
            "yt_format": (a["yt_format"] if a else "song") if svc == YTM else None,
        })
    return out


class FormatBody(BaseModel):
    format: Literal["song", "video"]


@router.put("/ytm/format")
def ytm_format(body: FormatBody, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    """Whether songs land on YouTube as music video or as audio track ("song")."""
    if not db.val("SELECT 1 FROM accounts WHERE user_id=? AND service=?", (user["id"], YTM)):
        raise HTTPException(400, "YouTube Music is not connected.")
    db.run("UPDATE accounts SET yt_format=? WHERE user_id=? AND service=?", (body.format, user["id"], YTM))
    return {"ok": True}


@router.delete("/{service}")
def disconnect(service: str, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    if service not in SERVICES:
        raise HTTPException(404)
    # Keep the account row so lines keep their stops; they pause until reconnected.
    db.run("UPDATE accounts SET credentials=NULL, status='ok', status_message=NULL, updated_at=? "
           "WHERE user_id=? AND service=?", (time.time(), user["id"], service))
    return {"ok": True}


# ---------------------------------------------------------------------- Spotify
@router.post("/spotify/start")
def spotify_start(user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    cid = app_credential(db, "spotify_client_id")
    if not cid:
        raise HTTPException(400, "Spotify isn't set up on this instance yet. An admin needs to enter the Spotify client ID under "
                                 "Settings → App credentials.")
    verifier, challenge = sp.pkce_pair()
    state = _put_flow({"user_id": user["id"], "verifier": verifier, "kind": "spotify"})
    redirect = f"{config.base_url}/api/services/spotify/callback"
    return {"url": sp.authorize_url(cid, redirect, state, challenge)}


@router.get("/spotify/callback")
def spotify_callback(request: Request, state: str = "", code: str = "", error: str = "",
                     db: Database = Depends(db_dep)):
    with _flows_lock:
        f = _flows.pop(state, None)
    # The browser coming back must be the one that started the sign-in. Otherwise someone could send
    # their own sign-in link to another person and get that person's Spotify linked to their account.
    try:
        same_user = bool(f) and current_user(request, db)["id"] == f["user_id"]
    except HTTPException:
        same_user = False
    if error or not f or f["kind"] != "spotify" or f["expires"] < time.time() or not code or not same_user:
        reason = "cancelled" if error == "access_denied" else "failed"
        return RedirectResponse(f"/services?error=spotify-{reason}", status_code=303)
    cid = app_credential(db, "spotify_client_id")
    try:
        creds = sp.exchange_code(code, f["verifier"], f"{config.base_url}/api/services/spotify/callback", cid,
                                 app_credential(db, "spotify_client_secret"))
        name = make_provider(db, SPOTIFY, creds).whoami()
    except (AuthError, ApiError) as e:
        msg = "spotify-premium" if "Premium" in str(e) else "spotify-failed"
        return RedirectResponse(f"/services?error={msg}", status_code=303)
    _save_account(db, f["user_id"], SPOTIFY, creds, name)
    return RedirectResponse("/services?connected=spotify", status_code=303)


# ---------------------------------------------------------------------- YouTube Music
@router.post("/ytm/device")
def ytm_device_start(user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    cid, secret = app_credential(db, "google_client_id"), app_credential(db, "google_client_secret")
    if not (cid and secret):
        raise HTTPException(400, "Google sign-in isn't set up on this instance. Use “Via the browser” "
                                 "or ask an admin to set it up under Settings → App credentials.")
    try:
        code = DeviceFlow(cid, secret).start()
    except AuthError as e:
        raise HTTPException(400, str(e)) from e
    flow = _put_flow({"user_id": user["id"], "device_code": code["device_code"], "kind": "ytm"},
                     ttl=int(code["expires_at"] - time.time()))
    return {"flow": flow, "user_code": code["user_code"], "verification_url": code["verification_url"],
            "interval": code["interval"], "expires_at": code["expires_at"]}


class FlowRef(BaseModel):
    flow: str


@router.post("/ytm/device/poll")
def ytm_device_poll(body: FlowRef, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    f = _get_flow(body.flow, user["id"], "ytm")
    cid, secret = app_credential(db, "google_client_id"), app_credential(db, "google_client_secret")
    try:
        creds = DeviceFlow(cid, secret).poll(f["device_code"])
    except AuthError as e:
        raise HTTPException(400, str(e)) from e
    if creds is None:
        return {"status": "pending"}
    name = _verify(db, YTM, creds)
    _flows.pop(body.flow, None)
    return {"status": "connected", **_save_account(db, user["id"], YTM, creds, name)}


class PasteBody(BaseModel):
    text: str = Field(min_length=10, max_length=60000)


@router.post("/ytm/paste")
def ytm_paste(body: PasteBody, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    try:
        creds = parse_pasted(body.text)
    except AuthError as e:
        raise HTTPException(400, str(e)) from e
    name = _verify(db, YTM, creds)
    return _save_account(db, user["id"], YTM, creds, name)


# ---------------------------------------------------------------------- Apple Music
class AppleBody(BaseModel):
    token: str = Field(min_length=20, max_length=8000)


@router.post("/apple/token")
def apple_token(body: AppleBody, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    creds = {"media_user_token": clean_user_token(body.token)}
    prov = make_provider(db, APPLE, creds)
    try:
        name = prov.whoami()
    except AuthError as e:
        raise HTTPException(400, "Apple Music doesn't accept this token. Copy the value of "
                                 "“media-user-token” again while you're signed in on music.apple.com.") from e
    return _save_account(db, user["id"], APPLE, prov.credentials, name)


# ---------------------------------------------------------------------- collections
def _account(db: Database, user_id: int, service: str) -> dict:
    a = db.one("SELECT * FROM accounts WHERE user_id=? AND service=?", (user_id, service))
    if not a or not a["credentials"]:
        raise HTTPException(400, f"{SERVICE_NAMES.get(service, service)} is not connected.")
    return a


@router.get("/{service}/collections")
def collections(service: str, refresh: bool = False, user: dict = Depends(current_user),
                db: Database = Depends(db_dep)):
    a = _account(db, user["id"], service)
    cached = _collections_cache.get(a["id"])
    if cached and not refresh and time.time() - cached[0] < 300:
        return cached[1]
    try:
        cols = [c.to_dict() for c in build_provider(db, a).collections()]
    except AuthError as e:
        mark_account(db, a["id"], "error", str(e))
        raise HTTPException(401, str(e)) from e
    except ApiError as e:
        raise HTTPException(502, str(e)) from e
    _collections_cache[a["id"]] = (time.time(), cols)
    return cols


class PlaylistBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.post("/{service}/playlists")
def create_playlist(service: str, body: PlaylistBody, user: dict = Depends(current_user),
                    db: Database = Depends(db_dep)):
    a = _account(db, user["id"], service)
    try:
        col = build_provider(db, a).create_playlist(body.name.strip())
    except AuthError as e:
        raise HTTPException(401, str(e)) from e
    except ApiError as e:
        raise HTTPException(502, str(e)) from e
    _collections_cache.pop(a["id"], None)
    return col.to_dict()




@router.post("/{service}/check")
def check_now(service: str, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    """Try the login right now (the app does this by itself about twice a day)."""
    from ..worker import check_account
    a = _account(db, user["id"], service)
    return {"result": check_account(db, a["id"])}
