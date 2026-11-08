"""Deezer: the library through the web player's gateway (gw-light) with the `arl` cookie, the catalog
through the public API (api.deezer.com, no login needed).

Deezer stopped accepting new developer apps, so there is no official sign-in for a self-hosted app.
The `arl` cookie of a logged-in deezer.com session works like Apple's media-user-token: the user
copies it once. Every write needs a short-lived CSRF token (`checkForm` from deezer.getUserData),
fetched on demand and renewed when the gateway asks for it.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from .. import health
from ..models import DEEZER, LIKED, PLAYLIST, ApiError, AuthError, Collection, NotSupported, Track
from .base import Provider, chunks, unique

GW = "https://www.deezer.com/ajax/gw-light.php"
PUBLIC = "https://api.deezer.com"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/18.0 Safari/605.1.15")
PAGE = 2000
_AUTH_CODES = ("NEED_USER_AUTH_REQUIRED", "USER_AUTH_REQUIRED", "GATEWAY_ERROR_AUTH")


def clean_arl(raw: str) -> str:
    tok = (raw or "").strip().strip('"').strip("'")
    if tok.lower().startswith("arl="):
        tok = tok.split("=", 1)[1]
    return tok.split(";")[0].strip()


def cover(md5: str | None, size: int = 264) -> str | None:
    return f"https://e-cdns-images.dzcdn.net/images/cover/{md5}/{size}x{size}-000000-80-0-0.jpg" if md5 else None


def track_url(track_id: str) -> str:
    return f"https://www.deezer.com/track/{track_id}"


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


class DeezerProvider(Provider):
    service = DEEZER
    # the public API allows about 50 calls per 5 seconds per IP: keep a small gap
    _public_lock = threading.Lock()
    _public_last = 0.0
    PUBLIC_GAP = 0.12

    def __init__(self, credentials, save_credentials=None):
        super().__init__(credentials, save_credentials)
        self.http = httpx.Client(timeout=30, headers={"User-Agent": UA, "Accept": "application/json"})
        if credentials.get("arl"):
            self.http.cookies.set("arl", credentials["arl"], domain=".deezer.com")
        self._token: str | None = None
        self._user: dict | None = None

    # ------------------------------------------------------------------ gateway
    def _gw(self, method: str, payload: dict | None = None, token: bool = True, _attempt: int = 0) -> Any:
        if not self.credentials.get("arl"):
            raise AuthError("Deezer is not connected.")
        params = {"method": method, "input": "3", "api_version": "1.0",
                  "api_token": self._csrf() if token else ""}
        try:
            r = self.http.post(GW, params=params, json=payload or {})
        except httpx.HTTPError as e:
            if _attempt < 3:
                time.sleep(2 * (_attempt + 1))
                return self._gw(method, payload, token, _attempt + 1)
            raise ApiError(f"Deezer is unreachable: {e}") from e
        if r.status_code == 429 or r.status_code >= 500:
            if r.status_code == 429:
                health.rate_limited(DEEZER)
            if _attempt < 4:
                time.sleep(3 * (_attempt + 1))
                return self._gw(method, payload, token, _attempt + 1)
        if r.status_code >= 400:
            raise ApiError(f"Deezer {method}: HTTP {r.status_code}", r.status_code)
        try:
            d = r.json()
        except ValueError as e:
            raise ApiError(f"Deezer {method}: unexpected answer") from e
        err = d.get("error")
        if err:
            codes = list(err) if isinstance(err, dict) else [str(err)]
            if any(c == "VALID_TOKEN_REQUIRED" for c in codes) and token and _attempt < 2:
                self._token = None
                return self._gw(method, payload, token, _attempt + 1)
            if any(c in _AUTH_CODES for c in codes):
                raise AuthError("Deezer sign-in is invalid or expired. Please copy the arl cookie again.")
            if any("QUOTA" in c for c in codes) and _attempt < 4:
                health.rate_limited(DEEZER)
                time.sleep(4 * (_attempt + 1))
                return self._gw(method, payload, token, _attempt + 1)
            raise ApiError(f"Deezer {method}: {err}")
        return d.get("results")

    def _csrf(self) -> str:
        if self._token is None:
            self._load_user()
        return self._token or ""

    def _load_user(self) -> dict:
        res = self._gw("deezer.getUserData", token=False) or {}
        user = res.get("USER") or {}
        if not _int(user.get("USER_ID")):
            raise AuthError("Deezer doesn't accept this arl cookie. Copy it again while you're signed in on deezer.com.")
        self._token = res.get("checkForm") or ""
        self._user = user
        return user

    def user(self) -> dict:
        return self._user or self._load_user()

    def whoami(self) -> str:
        u = self.user()
        return u.get("BLOG_NAME") or u.get("FIRSTNAME") or "Deezer"

    # ------------------------------------------------------------------ public API
    def _public(self, path: str, params: dict | None = None, _attempt: int = 0) -> dict:
        with DeezerProvider._public_lock:
            wait = DeezerProvider._public_last + self.PUBLIC_GAP - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            DeezerProvider._public_last = time.monotonic()
        try:
            r = self.http.get(PUBLIC + path, params=params)
        except httpx.HTTPError as e:
            if _attempt < 3:
                time.sleep(2 * (_attempt + 1))
                return self._public(path, params, _attempt + 1)
            raise ApiError(f"Deezer is unreachable: {e}") from e
        if r.status_code >= 500 and _attempt < 3:
            time.sleep(2 * (_attempt + 1))
            return self._public(path, params, _attempt + 1)
        if r.status_code >= 400:
            raise ApiError(f"Deezer {path}: HTTP {r.status_code}", r.status_code)
        d = r.json()
        err = d.get("error") if isinstance(d, dict) else None
        if err:
            if err.get("code") == 4 and _attempt < 4:          # "Quota limit exceeded"
                health.rate_limited(DEEZER)
                time.sleep(5)
                return self._public(path, params, _attempt + 1)
            if err.get("code") == 800:                         # no data (unknown id / ISRC)
                return {}
            raise ApiError(f"Deezer {path}: {err.get('message') or err}", 404 if err.get("code") == 800 else None)
        return d

    # ------------------------------------------------------------------ parsing
    @staticmethod
    def _gw_track(s: dict, handle: dict | None = None) -> Track | None:
        sid = s.get("SNG_ID")
        if not sid or _int(sid) is None or _int(sid) <= 0:       # negative ids are uploaded MP3s
            return None
        title = s.get("SNG_TITLE") or ""
        version = (s.get("VERSION") or "").strip()
        if version and version.lower() not in title.lower():
            title = f"{title} {version}"
        artists = [a.get("ART_NAME") for a in s.get("ARTISTS") or [] if a.get("ART_NAME")] or [s.get("ART_NAME") or ""]
        explicit = s.get("EXPLICIT_LYRICS")
        return Track(service=DEEZER, id=str(sid), title=title, artist=", ".join(dict.fromkeys(artists)),
                     album=s.get("ALB_TITLE") or "", duration=_int(s.get("DURATION")), isrc=s.get("ISRC") or None,
                     url=track_url(str(sid)), explicit=None if explicit is None else str(explicit) == "1",
                     artwork=cover(s.get("ALB_PICTURE")), handle=handle or {})

    @staticmethod
    def _public_track(t: dict) -> Track | None:
        if not t or t.get("type", "track") != "track" or not t.get("id") or _int(t["id"]) is None or _int(t["id"]) <= 0:
            return None
        title = t.get("title") or ""
        version = (t.get("title_version") or "").strip()
        if version and version.lower() not in title.lower():
            title = f"{title} {version}"
        contributors = [c.get("name") for c in t.get("contributors") or [] if c.get("role") == "Main" and c.get("name")]
        artist = ", ".join(dict.fromkeys(contributors)) or (t.get("artist") or {}).get("name") or ""
        album = t.get("album") or {}
        rtype = (album.get("record_type") or "").lower()
        return Track(service=DEEZER, id=str(t["id"]), title=title, artist=artist, album=album.get("title") or "",
                     duration=_int(t.get("duration")), isrc=t.get("isrc") or None,
                     url=t.get("link") or track_url(str(t["id"])), explicit=t.get("explicit_lyrics"),
                     album_type={"album": "album", "single": "single", "ep": "single",
                                 "compile": "compilation"}.get(rtype),
                     artwork=album.get("cover_medium") or cover(album.get("md5_image")))

    # ------------------------------------------------------------------ collections
    def _loved_id(self) -> str | None:
        return str(self.user().get("LOVEDTRACKS_ID") or "") or None

    def collections(self) -> list[Collection]:
        uid = self.user()["USER_ID"]
        liked = self._gw("favorite_song.getList", {"user_id": uid, "start": 0, "nb": 1}) or {}
        out = [Collection(LIKED, "", "Favourite tracks", _int(liked.get("total")))]
        prof = self._gw("deezer.pageProfile", {"user_id": uid, "tab": "playlists", "nb": 1000}) or {}
        loved = self._loved_id()
        for p in ((prof.get("TAB") or {}).get("playlists") or {}).get("data") or []:
            pid = str(p.get("PLAYLIST_ID") or "")
            if not pid or pid == loved:
                continue
            own = str(p.get("PARENT_USER_ID")) == str(uid)
            if not (own or str(p.get("COLLABORATIVE") or "") in ("1", "true")):
                continue
            out.append(Collection(PLAYLIST, pid, p.get("TITLE") or "Playlist", _int(p.get("NB_SONG"))))
        return out

    def _pages(self, method: str, payload: dict) -> list[dict]:
        out: list[dict] = []
        start = 0
        while True:
            res = self._gw(method, {**payload, "start": start, "nb": PAGE}) or {}
            batch = res.get("data") or []
            out.extend(batch)
            start += len(batch)
            total = _int(res.get("total"))
            if not batch or (total is not None and start >= total) or len(batch) < PAGE and total is None:
                break
        return out

    def read(self, kind: str, collection_id: str) -> list[Track]:
        if kind == LIKED:
            rows = self._pages("favorite_song.getList", {"user_id": self.user()["USER_ID"]})
        elif kind == PLAYLIST:
            rows = self._pages("playlist.getSongs", {"playlist_id": collection_id})
        else:
            raise NotSupported("Deezer has no separate library. Use “Favourite tracks”.")
        return unique([self._gw_track(s) for s in rows])

    def add(self, kind: str, collection_id: str, tracks: list[Track]) -> list[str]:
        failed: list[str] = []
        for batch in chunks(tracks, 100):
            ids = [t.id for t in batch]
            try:
                if kind == LIKED:
                    self._gw("song.addFavorites", {"IDS": ids})
                elif kind == PLAYLIST:
                    self._gw("playlist.addSongs", {"playlist_id": collection_id, "songs": [[i, 0] for i in ids],
                                                   "offset": -1})
                else:
                    raise NotSupported("Not supported")
            except NotSupported:
                raise
            except ApiError:
                failed += ids
        return failed

    def remove(self, kind: str, collection_id: str, tracks: list[Track]) -> list[str]:
        failed: list[str] = []
        for batch in chunks(tracks, 100):
            ids = [t.id for t in batch]
            try:
                if kind == LIKED:
                    self._gw("song.removeFavorites", {"IDS": ids})
                elif kind == PLAYLIST:
                    self._gw("playlist.deleteSongs", {"playlist_id": collection_id, "songs": [[i, 0] for i in ids]})
                else:
                    raise NotSupported("Not supported")
            except NotSupported:
                raise
            except ApiError:
                failed += ids
        return failed

    def create_playlist(self, name: str, description: str = "") -> Collection:
        # status 1 = private (0 public, 2 collaborative)
        res = self._gw("playlist.create", {"title": name[:100], "description": description or "Synced with Crossfade",
                                           "songs": [], "status": 1})
        pid = res if isinstance(res, (str, int)) else (res or {}).get("PLAYLIST_ID")
        if not pid:
            raise ApiError("Deezer didn't return a playlist ID.")
        return Collection(PLAYLIST, str(pid), name, 0)

    def delete_playlist(self, collection_id: str) -> None:
        self._gw("playlist.delete", {"playlist_id": collection_id})

    # ------------------------------------------------------------------ catalog
    def search(self, query: str, limit: int = 10) -> list[Track]:
        q = query.strip()[:200]
        if not q:
            return []
        d = self._public("/search/track", {"q": q, "limit": max(1, min(limit, 50))})
        return [t for t in (self._public_track(x) for x in d.get("data") or []) if t]

    def by_isrc(self, isrc: str) -> list[Track]:
        t = self._public_track(self._public(f"/track/isrc:{isrc}"))
        return [t] if t else []

    def artist_tracks(self, artist: str) -> list[Track]:
        d = self._public("/search/artist", {"q": artist[:100], "limit": 1})
        hit = (d.get("data") or [None])[0]
        if not hit:
            return []
        top = self._public(f"/artist/{hit['id']}/top", {"limit": 100})
        return [t for t in (self._public_track(x) for x in top.get("data") or []) if t]

    def track(self, track_id: str) -> Track | None:
        if not re.fullmatch(r"\d{1,14}", str(track_id)):
            return None
        return self._public_track(self._public(f"/track/{track_id}"))

    @staticmethod
    def parse_link(text: str) -> str | None:
        text = (text or "").strip()
        if re.fullmatch(r"\d{3,14}", text):
            return text
        u = urlparse(text)
        if "deezer.com" in u.netloc:
            m = re.search(r"/track/(\d+)", u.path)
            if m:
                return m.group(1)
        return None
