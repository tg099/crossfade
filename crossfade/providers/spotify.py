"""Spotify Web API (2026 Development Mode endpoints) with OAuth Authorization Code + PKCE."""
from __future__ import annotations

import base64
import hashlib
import re
import secrets
import time
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

from .. import health
from ..models import LIKED, PLAYLIST, SPOTIFY, ApiError, AuthError, Collection, NotSupported, Track
from .base import Provider, chunks, unique

API = "https://api.spotify.com/v1"
AUTH = "https://accounts.spotify.com/authorize"
TOKEN = "https://accounts.spotify.com/api/token"
SCOPES = ("user-read-private user-library-read user-library-modify playlist-read-private "
          "playlist-read-collaborative playlist-modify-private playlist-modify-public")


# ---------------------------------------------------------------------- OAuth (PKCE)
def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    return AUTH + "?" + urlencode({
        "client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri, "scope": SCOPES,
        "state": state, "code_challenge_method": "S256", "code_challenge": challenge,
    })


def _token_request(data: dict, client_id: str, client_secret: str | None) -> dict:
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if client_secret:
        headers["Authorization"] = "Basic " + base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    else:
        data = {**data, "client_id": client_id}
    r = httpx.post(TOKEN, data=data, headers=headers, timeout=30)
    if r.status_code in (400, 401):
        raise AuthError(f"Spotify rejected the sign-in: {r.json().get('error_description', r.text[:200])}")
    r.raise_for_status()
    tok = r.json()
    tok["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 60
    return tok


def exchange_code(code: str, verifier: str, redirect_uri: str, client_id: str, client_secret: str | None) -> dict:
    tok = _token_request({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
                          "code_verifier": verifier}, client_id, client_secret)
    return {"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token"),
            "expires_at": tok["expires_at"], "client_id": client_id}


def _cover(images: list[dict]) -> str | None:
    """Smallest image that is at least 250 px (Spotify lists them largest first)."""
    fit = [i for i in images if (i.get("width") or 0) >= 250]
    pick = (fit[-1] if fit else (images[0] if images else None))
    return pick.get("url") if pick else None


# ---------------------------------------------------------------------- provider
class SpotifyProvider(Provider):
    service = SPOTIFY

    def __init__(self, credentials, save_credentials=None, client_secret: str | None = None):
        super().__init__(credentials, save_credentials)
        self.client_secret = client_secret
        self.http = httpx.Client(timeout=30)
        self._me: dict | None = None

    # ------------------------------------------------------------------ transport
    def _token(self) -> str:
        c = self.credentials
        if time.time() >= c.get("expires_at", 0):
            if not c.get("refresh_token"):
                raise AuthError("Spotify sign-in expired. Please reconnect.")
            tok = _token_request({"grant_type": "refresh_token", "refresh_token": c["refresh_token"]},
                                 c["client_id"], self.client_secret)
            c["access_token"] = tok["access_token"]
            c["expires_at"] = tok["expires_at"]
            if tok.get("refresh_token"):
                c["refresh_token"] = tok["refresh_token"]
            self.persist()
        return c["access_token"]

    def _req(self, method: str, path: str, params: dict | None = None, body: Any = None,
             _attempt: int = 0) -> Any:
        url = path if path.startswith("http") else API + path
        try:
            r = self.http.request(method, url, params=params, json=body,
                                  headers={"Authorization": f"Bearer {self._token()}"})
        except httpx.HTTPError as e:
            if _attempt < 3:
                time.sleep(2 * (_attempt + 1))
                return self._req(method, path, params, body, _attempt + 1)
            raise ApiError(f"Spotify is unreachable: {e}") from e
        if r.status_code == 401 and _attempt == 0:
            self.credentials["expires_at"] = 0  # force refresh
            return self._req(method, path, params, body, 1)
        if r.status_code == 401:
            raise AuthError("Spotify sign-in is invalid. Please reconnect.")
        if r.status_code == 403:
            msg = r.text[:300]
            if "premium" in msg.lower() or "user not registered" in msg.lower() or "not registered" in msg.lower():
                raise AuthError("Spotify denied access: the account needs Premium and must be added as a user "
                                "in the admin's Spotify app.")
            raise ApiError(f"Spotify {method} {path}: access denied ({msg})", 403)
        if r.status_code == 429:
            body_json = {}
            try:
                body_json = r.json()
            except ValueError:
                pass
            if (body_json.get("error") or {}).get("reason") == "QUOTA_EXCEEDED":
                health.rate_limited(SPOTIFY)
                raise ApiError("The app's Spotify quota is used up. Try again later.", 429)
            if _attempt < 5:
                health.rate_limited(SPOTIFY)
                time.sleep(min(int(r.headers.get("Retry-After", "5")), 60) + 1)
                return self._req(method, path, params, body, _attempt + 1)
        if r.status_code >= 500 and _attempt < 3:
            time.sleep(3 * (_attempt + 1))
            return self._req(method, path, params, body, _attempt + 1)
        if r.status_code >= 400:
            raise ApiError(f"Spotify {method} {path}: HTTP {r.status_code} {r.text[:300]}", r.status_code)
        if not r.content:
            return None
        try:
            return r.json()
        except ValueError:
            return None

    def _paged(self, path: str, params: dict) -> list[dict]:
        out: list[dict] = []
        d = self._req("GET", path, params)
        while d:
            out.extend(d.get("items") or [])
            nxt = d.get("next")
            d = self._req("GET", nxt) if nxt else None
        return out

    # ------------------------------------------------------------------ account
    def me(self) -> dict:
        if self._me is None:
            self._me = self._req("GET", "/me")
        return self._me

    def whoami(self) -> str:
        m = self.me()
        return m.get("display_name") or m.get("id") or "Spotify"

    # ------------------------------------------------------------------ parsing
    @staticmethod
    def _track(t: dict | None) -> Track | None:
        if not t or t.get("type", "track") != "track" or t.get("is_local") or not t.get("id"):
            return None
        dur = t.get("duration_ms")
        return Track(service=SPOTIFY, id=t["id"], title=t.get("name") or "",
                     artist=", ".join(a.get("name", "") for a in t.get("artists") or []),
                     album=(t.get("album") or {}).get("name") or "",
                     duration=round(dur / 1000) if dur else None,
                     isrc=(t.get("external_ids") or {}).get("isrc"),
                     url=(t.get("external_urls") or {}).get("spotify"),
                     explicit=t.get("explicit"), album_type=(t.get("album") or {}).get("album_type"),
                     artwork=_cover((t.get("album") or {}).get("images") or []))

    # ------------------------------------------------------------------ collections
    def collections(self) -> list[Collection]:
        liked_total = (self._req("GET", "/me/tracks", {"limit": 1}) or {}).get("total")
        out = [Collection(LIKED, "", "Liked Songs", liked_total)]
        my_id = self.me().get("id")
        for p in self._paged("/me/playlists", {"limit": 50}):
            if not p:
                continue
            own = (p.get("owner") or {}).get("id") == my_id
            if not (own or p.get("collaborative")):
                continue
            total = (p.get("items") or p.get("tracks") or {}).get("total")
            out.append(Collection(PLAYLIST, p["id"], p.get("name") or "Playlist", total))
        return out

    def read(self, kind: str, collection_id: str) -> list[Track]:
        if kind == LIKED:
            items = self._paged("/me/tracks", {"limit": 50})
            tracks = [self._track(i.get("track")) for i in items]
        elif kind == PLAYLIST:
            items = self._paged(f"/playlists/{collection_id}/items", {"limit": 50, "additional_types": "track"})
            tracks = [self._track(i.get("item") or i.get("track")) for i in items if not i.get("is_local")]
        else:
            raise NotSupported("Spotify has no separate library. Use “Liked Songs”.")
        return unique(tracks)

    def add(self, kind: str, collection_id: str, tracks: list[Track]) -> list[str]:
        failed: list[str] = []
        uris = [f"spotify:track:{t.id}" for t in tracks]
        if kind == LIKED:
            for batch in chunks(uris, 40):
                try:
                    self._req("PUT", "/me/library", {"uris": ",".join(batch)})
                except ApiError:
                    failed += [u.rsplit(":", 1)[1] for u in batch]
        elif kind == PLAYLIST:
            for batch in chunks(uris, 100):
                try:
                    self._req("POST", f"/playlists/{collection_id}/items", body={"uris": batch})
                except ApiError:
                    failed += [u.rsplit(":", 1)[1] for u in batch]
        else:
            raise NotSupported("Not supported")
        return failed

    def remove(self, kind: str, collection_id: str, tracks: list[Track]) -> list[str]:
        failed: list[str] = []
        uris = [f"spotify:track:{t.id}" for t in tracks]
        if kind == LIKED:
            for batch in chunks(uris, 40):
                try:
                    self._req("DELETE", "/me/library", {"uris": ",".join(batch)})
                except ApiError:
                    failed += [u.rsplit(":", 1)[1] for u in batch]
        elif kind == PLAYLIST:
            for batch in chunks(uris, 100):
                try:
                    self._req("DELETE", f"/playlists/{collection_id}/items",
                              body={"items": [{"uri": u} for u in batch]})
                except ApiError:
                    failed += [u.rsplit(":", 1)[1] for u in batch]
        else:
            raise NotSupported("Not supported")
        return failed

    def delete_playlist(self, collection_id: str) -> None:
        """Spotify has no deleting: the owner unfollows the playlist, and it disappears from the library."""
        try:
            self._req("DELETE", "/me/library", {"uris": f"spotify:playlist:{collection_id}"})
        except ApiError as e:
            if e.status not in (400, 404, 405, 410):
                raise
            self._req("DELETE", f"/playlists/{collection_id}/followers")

    def create_playlist(self, name: str, description: str = "") -> Collection:
        p = self._req("POST", "/me/playlists", body={"name": name, "public": False,
                                                     "description": description or "Synced with Crossfade"})
        return Collection(PLAYLIST, p["id"], p.get("name") or name, 0)

    # ------------------------------------------------------------------ catalog
    def search(self, query: str, limit: int = 10) -> list[Track]:
        out: list[Track] = []
        for offset in range(0, max(limit, 1), 10):
            d = self._req("GET", "/search", {"q": query[:250], "type": "track", "limit": min(10, limit - offset),
                                             "offset": offset}) or {}
            items = ((d.get("tracks") or {}).get("items")) or []
            out += [t for t in (self._track(i) for i in items) if t]
            if len(items) < 10:
                break
        return out

    def by_isrc(self, isrc: str) -> list[Track]:
        return self.search(f"isrc:{isrc}", limit=5)

    def track(self, track_id: str) -> Track | None:
        try:
            return self._track(self._req("GET", f"/tracks/{track_id}"))
        except ApiError as e:
            if e.status in (400, 404):
                return None
            raise

    @staticmethod
    def parse_link(text: str) -> str | None:
        text = (text or "").strip()
        m = re.fullmatch(r"spotify:track:([A-Za-z0-9]{22})", text)
        if m:
            return m.group(1)
        if re.fullmatch(r"[A-Za-z0-9]{22}", text):
            return text
        u = urlparse(text)
        if "spotify.com" in u.netloc:
            m = re.search(r"/track/([A-Za-z0-9]{22})", u.path)
            if m:
                return m.group(1)
        return None
