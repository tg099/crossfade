"""Build providers for stored accounts and expose instance-wide app credentials."""
from __future__ import annotations

import json
import os
import time

from ..db import Database
from ..health import measured
from ..models import APPLE, DEEZER, SPOTIFY, TIDAL, YTM, AuthError
from ..security import decrypt, encrypt
from .apple import AppleProvider, DevTokenStore
from .base import Provider
from .deezer import DeezerProvider
from .spotify import SpotifyProvider
from .tidal import TidalProvider
from .ytm import YTMProvider

# Instance credentials: env vars win over values entered by the admin in the UI.
APP_KEYS = {
    "spotify_client_id": "SPOTIFY_CLIENT_ID",
    "spotify_client_secret": "SPOTIFY_CLIENT_SECRET",
    "google_client_id": "GOOGLE_CLIENT_ID",
    "google_client_secret": "GOOGLE_CLIENT_SECRET",
    "tidal_client_id": "TIDAL_CLIENT_ID",
    "tidal_client_secret": "TIDAL_CLIENT_SECRET",
}


def app_credential(db: Database, key: str) -> str | None:
    env = os.environ.get(APP_KEYS[key])
    if env:
        return env
    blob = db.setting("app_" + key)
    return decrypt(blob) if blob else None


def set_app_credential(db: Database, key: str, value: str | None) -> None:
    db.set_setting("app_" + key, encrypt(value) if value else None)


def app_credential_source(db: Database, key: str) -> str | None:
    if os.environ.get(APP_KEYS[key]):
        return "env"
    return "ui" if db.setting("app_" + key) else None


def dev_token_store(db: Database) -> DevTokenStore:
    return DevTokenStore(lambda: db.setting("apple_dev_token"), lambda t: db.set_setting("apple_dev_token", t))


class DbMetaStore:
    """YouTube video -> song details, kept across runs (see YTMProvider._enrich)."""

    def __init__(self, db: Database):
        self.db = db

    def get_many(self, ids: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            rows = self.db.all(f"SELECT video_id, data FROM ytm_meta WHERE video_id IN ({','.join('?' * len(part))})",
                               tuple(part))
            out.update({r["video_id"]: json.loads(r["data"]) for r in rows})
        return out

    def put(self, video_id: str, meta: dict) -> None:
        self.db.run("INSERT INTO ytm_meta(video_id,data,created_at) VALUES(?,?,?) ON CONFLICT(video_id) "
                    "DO UPDATE SET data=excluded.data, created_at=excluded.created_at",
                    (video_id, json.dumps(meta), time.time()))


def save_account_credentials(db: Database, account_id: int, creds: dict) -> None:
    # only while still connected: a sync that refreshes a token must not undo "disconnect"
    db.run("UPDATE accounts SET credentials=?, updated_at=? WHERE id=? AND credentials IS NOT NULL",
           (encrypt(creds), time.time(), account_id))


def build_provider(db: Database, account: dict) -> Provider:
    creds = decrypt(account["credentials"])
    if not creds:
        raise AuthError("Service is not connected.")
    return make_provider(db, account["service"], creds,
                         lambda c: save_account_credentials(db, account["id"], c))


def make_provider(db: Database, service: str, creds: dict, save=None) -> Provider:
    return measured(_make_provider(db, service, creds, save))


def _make_provider(db: Database, service: str, creds: dict, save=None) -> Provider:
    if service == SPOTIFY:
        return SpotifyProvider(creds, save, client_secret=app_credential(db, "spotify_client_secret"))
    if service == APPLE:
        return AppleProvider(creds, save, dev_tokens=dev_token_store(db))
    if service == YTM:
        cid, secret = app_credential(db, "google_client_id"), app_credential(db, "google_client_secret")
        return YTMProvider(creds, save, oauth_client=(cid, secret) if cid and secret else None,
                           meta_store=DbMetaStore(db))
    if service == TIDAL:
        return TidalProvider(creds, save, client_secret=app_credential(db, "tidal_client_secret"))
    if service == DEEZER:
        return DeezerProvider(creds, save)
    raise ValueError(service)


def search_provider(db: Database, service: str, user_id: int | None = None) -> Provider | None:
    """A provider usable for catalog searches (the user's own account if connected)."""
    if user_id is not None:
        acc = db.one("SELECT * FROM accounts WHERE user_id=? AND service=?", (user_id, service))
        if acc and acc["credentials"]:
            try:
                return build_provider(db, acc)
            except AuthError:
                pass
    if service == YTM:
        return measured(YTMProvider({}))
    if service == APPLE:
        return measured(AppleProvider({"media_user_token": "", "storefront": "us"}, dev_tokens=dev_token_store(db)))
    if service == DEEZER:
        return measured(DeezerProvider({}))      # the catalog needs no login
    if service == TIDAL:
        cid, secret = app_credential(db, "tidal_client_id"), app_credential(db, "tidal_client_secret")
        if cid and secret:                       # app-only token: catalog search without a user
            return measured(TidalProvider({"client_id": cid, "app_only": True, "expires_at": 0}, None,
                                          client_secret=secret))
    return None
