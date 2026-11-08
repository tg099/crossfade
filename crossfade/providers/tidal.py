"""TIDAL through its official API (openapi.tidal.com/v2, JSON:API) with OAuth Authorization Code + PKCE.

The admin registers an app on developer.tidal.com once; every user then signs in with their own TIDAL
account. "Liked" is the user's collection of tracks (My Collection → Tracks); playlists are the ones
the user owns. Track details (artists, album, cover) come from /tracks with `include`, at most 20 ids
per call.
"""
from __future__ import annotations

import base64
import re
import time
from dataclasses import replace
from typing import Any
from urllib.parse import quote, urlencode, urlparse

import httpx

from .. import health
from ..models import LIKED, PLAYLIST, TIDAL, ApiError, AuthError, Collection, NotSupported, Track
from .base import Provider, chunks, unique
from .spotify import pkce_pair  # noqa: F401 – same PKCE pair, re-exported for the services API

API = "https://openapi.tidal.com/v2"
AUTH = "https://login.tidal.com/authorize"
TOKEN = "https://auth.tidal.com/v1/oauth2/token"
SCOPES = "user.read collection.read collection.write playlists.read playlists.write search.read"
JSONAPI = "application/vnd.api+json"


def authorize_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    return AUTH + "?" + urlencode({
        "client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri, "scope": SCOPES,
        "state": state, "code_challenge_method": "S256", "code_challenge": challenge,
    })


def _token_request(data: dict, client_id: str, client_secret: str | None) -> dict:
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if client_secret:
        headers["Authorization"] = "Basic " + base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    data = {**data, "client_id": client_id}
    try:
        r = httpx.post(TOKEN, data=data, headers=headers, timeout=30)
    except httpx.HTTPError as e:
        raise ApiError(f"TIDAL is unreachable: {e}") from e
    if r.status_code in (400, 401):
        try:
            msg = r.json().get("error_description") or r.json().get("error") or r.text[:200]
        except ValueError:
            msg = r.text[:200]
        raise AuthError(f"TIDAL rejected the sign-in: {msg}")
    if r.status_code >= 400:
        raise ApiError(f"TIDAL sign-in: HTTP {r.status_code} {r.text[:200]}", r.status_code)
    tok = r.json()
    tok["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 60
    return tok


def exchange_code(code: str, verifier: str, redirect_uri: str, client_id: str, client_secret: str | None) -> dict:
    tok = _token_request({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
                          "code_verifier": verifier}, client_id, client_secret)
    out = {"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token"),
           "expires_at": tok["expires_at"], "client_id": client_id}
    if tok.get("refresh_token_expires_in"):
        out["refresh_expires_at"] = time.time() + int(tok["refresh_token_expires_in"])
    return out


def client_token(client_id: str, client_secret: str) -> dict:
    """App-only token (no user): enough for catalog search and lookups."""
    tok = _token_request({"grant_type": "client_credentials"}, client_id, client_secret)
    return {"access_token": tok["access_token"], "expires_at": tok["expires_at"], "client_id": client_id,
            "app_only": True}


def iso_seconds(v: str | None) -> int | None:
    """ISO 8601 duration (PT3M5S) → seconds."""
    if not v:
        return None
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", v)
    if not m:
        return None
    d, h, mi, s = (float(x) if x else 0 for x in m.groups())
    return round(d * 86400 + h * 3600 + mi * 60 + s)


def track_url(track_id: str) -> str:
    return f"https://tidal.com/browse/track/{track_id}"


class TidalProvider(Provider):
    service = TIDAL

    def __init__(self, credentials, save_credentials=None, client_secret: str | None = None):
        super().__init__(credentials, save_credentials)
        self.client_secret = client_secret
        self.http = httpx.Client(timeout=30)
        self._me: dict | None = None

    # ------------------------------------------------------------------ transport
    def login_expires_at(self) -> float | None:
        return self.credentials.get("refresh_expires_at")

    def _token(self) -> str:
        c = self.credentials
        if time.time() >= c.get("expires_at", 0):
            if c.get("app_only"):
                if not self.client_secret:
                    raise AuthError("TIDAL search needs the app's client secret.")
                c.update(client_token(c["client_id"], self.client_secret))
            else:
                if not c.get("refresh_token"):
                    raise AuthError("TIDAL sign-in expired. Please reconnect.")
                tok = _token_request({"grant_type": "refresh_token", "refresh_token": c["refresh_token"]},
                                     c["client_id"], self.client_secret)
                c["access_token"], c["expires_at"] = tok["access_token"], tok["expires_at"]
                if tok.get("refresh_token"):
                    c["refresh_token"] = tok["refresh_token"]
                self.persist()
        return c["access_token"]

    def _req(self, method: str, path: str, params: dict | list | None = None, body: Any = None,
             _attempt: int = 0) -> Any:
        url = path if path.startswith("http") else API + path
        headers = {"Authorization": f"Bearer {self._token()}", "Accept": JSONAPI}
        if body is not None:
            headers["Content-Type"] = JSONAPI
        try:
            r = self.http.request(method, url, params=params, json=body, headers=headers)
        except httpx.HTTPError as e:
            if _attempt < 3:
                time.sleep(2 * (_attempt + 1))
                return self._req(method, path, params, body, _attempt + 1)
            raise ApiError(f"TIDAL is unreachable: {e}") from e
        if r.status_code == 401 and _attempt == 0:
            self.credentials["expires_at"] = 0
            return self._req(method, path, params, body, 1)
        if r.status_code == 401:
            raise AuthError("TIDAL sign-in is invalid. Please reconnect.")
        if r.status_code == 429:
            health.rate_limited(TIDAL)
            if _attempt < 5:
                time.sleep(min(int(r.headers.get("Retry-After", "3") or 3), 60) + 1)
                return self._req(method, path, params, body, _attempt + 1)
        if r.status_code >= 500 and _attempt < 3:
            time.sleep(3 * (_attempt + 1))
            return self._req(method, path, params, body, _attempt + 1)
        if r.status_code == 403:
            raise ApiError(f"TIDAL {method} {path}: access denied ({r.text[:300]})", 403)
        if r.status_code >= 400:
            raise ApiError(f"TIDAL {method} {path}: HTTP {r.status_code} {r.text[:300]}", r.status_code)
        if not r.content:
            return None
        try:
            return r.json()
        except ValueError:
            return None

    def _paged(self, path: str, params: dict | list) -> tuple[list[dict], list[dict]]:
        """All pages of a to-many list: (data, included)."""
        data: list[dict] = []
        included: list[dict] = []
        d = self._req("GET", path, params)
        while d:
            data.extend(d.get("data") or [])
            included.extend(d.get("included") or [])
            nxt = (d.get("links") or {}).get("next")
            d = self._req("GET", nxt if nxt.startswith("http") else API + nxt) if nxt else None
        return data, included

    # ------------------------------------------------------------------ account
    def me(self) -> dict:
        if self._me is None:
            d = self._req("GET", "/users/me") or {}
            self._me = d.get("data") or {}
        return self._me

    def country(self) -> str:
        if self.credentials.get("app_only"):
            return self.credentials.get("country") or "US"
        return ((self.me().get("attributes") or {}).get("country") or "US").upper()

    def whoami(self) -> str:
        a = self.me().get("attributes") or {}
        name = " ".join(x for x in (a.get("firstName"), a.get("lastName")) if x)
        return a.get("username") or name or a.get("email") or "TIDAL"

    # ------------------------------------------------------------------ parsing
    def _details(self, ids: list[str]) -> dict[str, Track]:
        """Full tracks (artists, album, cover) for these ids."""
        out: dict[str, Track] = {}
        for part in chunks(list(dict.fromkeys(ids)), 20):
            params = [("countryCode", self.country()), ("include", "artists,albums,albums.coverArt")]
            params += [("filter[id]", i) for i in part]
            d = self._req("GET", "/tracks", params) or {}
            out.update(self._parse(d))
        return out

    def _parse(self, d: dict) -> dict[str, Track]:
        inc = {(x.get("type"), x.get("id")): x for x in d.get("included") or []}
        out: dict[str, Track] = {}
        for t in d.get("data") or []:
            if t.get("type") != "tracks":
                continue
            tr = self._track(t, inc)
            if tr:
                out[tr.id] = tr
        return out

    @staticmethod
    def _track(t: dict, inc: dict) -> Track | None:
        a = t.get("attributes") or {}
        if not t.get("id") or not a.get("title"):
            return None
        rel = t.get("relationships") or {}
        artists = [((inc.get(("artists", x.get("id"))) or {}).get("attributes") or {}).get("name")
                   for x in ((rel.get("artists") or {}).get("data") or [])]
        album_ref = ((rel.get("albums") or {}).get("data") or [None])[0] or {}
        album = inc.get(("albums", album_ref.get("id"))) or {}
        aa = album.get("attributes") or {}
        art = None
        cover_ref = (((album.get("relationships") or {}).get("coverArt") or {}).get("data") or [None])[0] or {}
        files = ((inc.get(("artworks", cover_ref.get("id"))) or {}).get("attributes") or {}).get("files") or []
        fit = sorted((f for f in files if f.get("href")), key=lambda f: ((f.get("meta") or {}).get("width") or 0))
        for f in fit:
            if ((f.get("meta") or {}).get("width") or 0) >= 250:
                art = f["href"]
                break
        if art is None and fit:
            art = fit[-1]["href"]
        title = a["title"]
        if a.get("version") and a["version"].lower() not in title.lower():
            title = f"{title} ({a['version']})"
        kind = (aa.get("type") or "").lower()
        return Track(service=TIDAL, id=str(t["id"]), title=title, artist=", ".join(x for x in artists if x),
                     album=aa.get("title") or "", duration=iso_seconds(a.get("duration")), isrc=a.get("isrc"),
                     url=track_url(str(t["id"])), explicit=a.get("explicit"),
                     album_type={"album": "album", "single": "single", "ep": "single",
                                 "compilation": "compilation"}.get(kind),
                     artwork=art)

    # ------------------------------------------------------------------ collections
    def collections(self) -> list[Collection]:
        out = [Collection(LIKED, "", "My Tracks", None)]
        data, _ = self._paged("/playlists", [("filter[owners.id]", "me"), ("countryCode", self.country())])
        for p in data:
            a = p.get("attributes") or {}
            out.append(Collection(PLAYLIST, str(p["id"]), a.get("name") or "Playlist",
                                  a.get("numberOfTrackItems", a.get("numberOfItems"))))
        return out

    def read(self, kind: str, collection_id: str) -> list[Track]:
        if kind == LIKED:
            items, _ = self._paged("/userCollectionTracks/me/relationships/items",
                                   {"countryCode": self.country(), "locale": "en-US"})
            entries = [(str(i["id"]), {}) for i in items if i.get("type") == "tracks"]
        elif kind == PLAYLIST:
            items, _ = self._paged(f"/playlists/{quote(collection_id)}/relationships/items",
                                   {"countryCode": self.country()})
            entries = [(str(i["id"]), {"item_id": (i.get("meta") or {}).get("itemId")})
                       for i in items if i.get("type") == "tracks"]
        else:
            raise NotSupported("TIDAL has no separate library. Use “My Tracks”.")
        details = self._details([i for i, _ in entries])
        tracks = []
        for tid, handle in entries:
            t = details.get(tid)
            if t is None:
                continue        # no longer available in this country
            tracks.append(replace(t, handle=dict(handle)))
        return unique(tracks)

    def add(self, kind: str, collection_id: str, tracks: list[Track]) -> list[str]:
        failed: list[str] = []
        path = ("/userCollectionTracks/me/relationships/items" if kind == LIKED
                else f"/playlists/{quote(collection_id)}/relationships/items" if kind == PLAYLIST else None)
        if path is None:
            raise NotSupported("Not supported")
        for batch in chunks(tracks, 50):
            body: dict = {"data": [{"type": "tracks", "id": t.id} for t in batch]}
            try:
                self._req("POST", path, {"countryCode": self.country()} if kind == LIKED else None, body)
            except ApiError:
                failed += [t.id for t in batch]
        return failed

    def remove(self, kind: str, collection_id: str, tracks: list[Track]) -> list[str]:
        failed: list[str] = []
        if kind == LIKED:
            for batch in chunks(tracks, 50):
                try:
                    self._req("DELETE", "/userCollectionTracks/me/relationships/items", None,
                              {"data": [{"type": "tracks", "id": t.id} for t in batch]})
                except ApiError:
                    failed += [t.id for t in batch]
        elif kind == PLAYLIST:
            entries = []
            for t in tracks:
                handles = [t.handle, *t.handle.get("extra", [])]
                ids = [h.get("item_id") for h in handles if h.get("item_id")]
                if not ids:
                    failed.append(t.id)
                    continue
                entries += [(t.id, i) for i in ids]
            for batch in chunks(entries, 50):
                try:
                    self._req("DELETE", f"/playlists/{quote(collection_id)}/relationships/items", None,
                              {"data": [{"type": "tracks", "id": tid, "meta": {"itemId": iid}} for tid, iid in batch]})
                except ApiError:
                    failed += [tid for tid, _ in batch]
        else:
            raise NotSupported("Not supported")
        return list(dict.fromkeys(failed))

    def remove_copies(self, kind: str, collection_id: str, track: Track) -> None:
        """TIDAL removes single playlist entries: drop the extra copies, keep the first."""
        extra = [h.get("item_id") for h in track.handle.get("extra", []) if h.get("item_id")]
        if kind != PLAYLIST or not extra:
            return super().remove_copies(kind, collection_id, track)
        for batch in chunks(extra, 50):
            self._req("DELETE", f"/playlists/{quote(collection_id)}/relationships/items", None,
                      {"data": [{"type": "tracks", "id": track.id, "meta": {"itemId": i}} for i in batch]})

    def create_playlist(self, name: str, description: str = "") -> Collection:
        d = self._req("POST", "/playlists", {"countryCode": self.country()}, {"data": {
            "type": "playlists", "attributes": {"name": name[:250], "description": description or "Synced with Crossfade",
                                                "accessType": "UNLISTED"}}}) or {}
        p = d.get("data") or {}
        if not p.get("id"):
            raise ApiError("TIDAL didn't return a playlist ID.")
        return Collection(PLAYLIST, str(p["id"]), (p.get("attributes") or {}).get("name") or name, 0)

    def delete_playlist(self, collection_id: str) -> None:
        self._req("DELETE", f"/playlists/{quote(collection_id)}")

    # ------------------------------------------------------------------ catalog
    def search(self, query: str, limit: int = 10) -> list[Track]:
        q = query.strip()[:200]
        if not q:
            return []
        d = self._req("GET", f"/searchResults/{quote(q, safe='')}/relationships/tracks",
                      {"countryCode": self.country(), "explicitFilter": "INCLUDE"}) or {}
        ids = [str(x["id"]) for x in (d.get("data") or []) if x.get("type") == "tracks"][:limit]
        details = self._details(ids)
        return [details[i] for i in ids if i in details]

    def by_isrc(self, isrc: str) -> list[Track]:
        d = self._req("GET", "/tracks", [("filter[isrc]", isrc), ("countryCode", self.country()),
                                         ("include", "artists,albums,albums.coverArt")]) or {}
        return list(self._parse(d).values())

    def track(self, track_id: str) -> Track | None:
        try:
            return self._details([track_id]).get(str(track_id))
        except ApiError as e:
            if e.status in (400, 404):
                return None
            raise

    @staticmethod
    def parse_link(text: str) -> str | None:
        text = (text or "").strip()
        if re.fullmatch(r"\d{4,12}", text):
            return text
        u = urlparse(text)
        if "tidal.com" in u.netloc:
            m = re.search(r"/track/(\d+)", u.path)
            if m:
                return m.group(1)
        return None
