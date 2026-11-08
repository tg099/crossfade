"""Apple Music via the music.apple.com web player API (amp-api).

Developer token: scraped from the web player's JS bundle and shared by the whole instance.
User token: the `media-user-token` cookie of a logged-in music.apple.com session.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import threading
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from ..models import APPLE, LIBRARY, LIKED, PLAYLIST, ApiError, AuthError, Collection, NotSupported, Track
from .base import Provider, chunks, unique

BASE = "https://amp-api.music.apple.com"
log = logging.getLogger(__name__)
WEB = "https://music.apple.com"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/18.0 Safari/605.1.15")
_JWT = re.compile(r'"(eyJ[\w-]+\.[\w-]+\.[\w-]+)"')


def _b64json(part: str) -> dict:
    part += "=" * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(part))


def jwt_expiry(token: str) -> int | None:
    try:
        return int(_b64json(token.split(".")[1])["exp"])
    except Exception:  # noqa: BLE001
        return None


def fetch_developer_token() -> str:
    with httpx.Client(headers={"User-Agent": UA}, follow_redirects=True, timeout=30) as c:
        html = c.get(f"{WEB}/us/browse").text
        for src in re.findall(r'<script[^>]+src="(/assets/[^"]+\.js)"', html):
            js = c.get(WEB + src).text
            for m in _JWT.finditer(js):
                tok = m.group(1)
                try:
                    if _b64json(tok.split(".")[0]).get("kid") == "WebPlayKid":
                        return tok
                except Exception:  # noqa: BLE001
                    continue
    raise ApiError("Couldn't load the Apple Music web token from music.apple.com.")


class DevTokenStore:
    """Instance-wide developer token cache, persisted via callbacks."""

    def __init__(self, load: Callable[[], str | None], save: Callable[[str], None]):
        self._load, self._save = load, save
        self._lock = threading.Lock()

    def get(self, force: bool = False) -> str:
        with self._lock:
            tok = self._load()
            exp = jwt_expiry(tok) if tok else None
            if force or not tok or (exp and exp - time.time() < 86400):
                tok = fetch_developer_token()
                self._save(tok)
            return tok


def _artwork(attrs: dict) -> str | None:
    url = (attrs.get("artwork") or {}).get("url")
    return url.replace("{w}", "300").replace("{h}", "300").replace("{f}", "jpg") if url else None


def _explicit(attrs: dict) -> bool | None:
    rating = attrs.get("contentRating")
    return True if rating == "explicit" else False if rating == "clean" else None


def clean_user_token(raw: str) -> str:
    tok = (raw or "").strip().strip('"').strip("'")
    if tok.lower().startswith("media-user-token="):
        tok = tok.split("=", 1)[1]
    return tok.split(";")[0].strip()


class AppleProvider(Provider):
    service = APPLE

    def __init__(self, credentials, save_credentials=None, dev_tokens: DevTokenStore | None = None):
        super().__init__(credentials, save_credentials)
        if dev_tokens is None:
            cache: dict[str, str] = {}
            dev_tokens = DevTokenStore(lambda: cache.get("t"), lambda t: cache.__setitem__("t", t))
        self.dev_tokens = dev_tokens
        self.http = httpx.Client(timeout=30, headers={"User-Agent": UA, "Origin": WEB, "Referer": WEB + "/",
                                                      "Accept": "application/json"})

    def login_expires_at(self) -> float | None:
        tok = self.credentials.get("media_user_token") or ""
        return jwt_expiry(tok) if tok.count(".") == 2 else None

    # ------------------------------------------------------------------ transport
    def _req(self, method: str, path: str, params: dict | None = None, body: Any = None,
             user: bool = True, _attempt: int = 0, patient: bool = True) -> dict:
        headers = {"Authorization": f"Bearer {self.dev_tokens.get(force=_attempt == 1)}"}
        if user:
            headers["Media-User-Token"] = self.credentials["media_user_token"]
        try:
            r = self.http.request(method, BASE + path, params=params, json=body, headers=headers)
        except httpx.HTTPError as e:
            if _attempt < 3:
                time.sleep(2 * (_attempt + 1))
                return self._req(method, path, params, body, user, _attempt + 2, patient)
            raise ApiError(f"Apple Music is unreachable: {e}") from e
        if r.status_code in (401, 403):
            if _attempt == 0:  # developer token may have rotated
                return self._req(method, path, params, body, user, 1, patient)
            raise AuthError("Apple Music sign-in is invalid or expired. Please enter the media-user-token "
                            "again.")
        if (r.status_code >= 500 or (r.status_code == 429 and patient)) and _attempt < 5:
            time.sleep(3 * (_attempt + 1))
            return self._req(method, path, params, body, user, _attempt + 2, patient)
        if r.status_code >= 400:
            raise ApiError(f"Apple Music {method} {path}: HTTP {r.status_code} {r.text[:300]}", r.status_code)
        if not r.content:
            return {}
        try:
            return r.json()
        except ValueError:
            return {}

    def _paged(self, path: str, params: dict) -> list[dict]:
        out: list[dict] = []
        params = dict(params)
        while True:
            try:
                d = self._req("GET", path, params)
            except ApiError as e:
                if e.status == 404 and not out:  # Apple answers 404 for empty relationships
                    return []
                raise
            out.extend(d.get("data", []))
            nxt = d.get("next")
            if not nxt:
                return out
            q = parse_qs(urlparse(nxt).query)
            if "offset" not in q:
                return out
            params["offset"] = q["offset"][0]

    @property
    def _has_user(self) -> bool:
        # Apple rejects anonymous catalog searches with 429, so search as the user whenever possible.
        return bool(self.credentials.get("media_user_token"))

    # ------------------------------------------------------------------ account
    def storefront(self) -> str:
        sf = self.credentials.get("storefront")
        if not sf:
            d = self._req("GET", "/v1/me/storefront")
            sf = d["data"][0]["id"]
            self.credentials["storefront"] = sf
            self.persist()
        return sf

    def whoami(self) -> str:
        return f"Apple Music account ({self.storefront().upper()})"

    # ------------------------------------------------------------------ parsing
    @staticmethod
    def _catalog(d: dict) -> Track:
        a = d.get("attributes") or {}
        dur = a.get("durationInMillis")
        return Track(service=APPLE, id=str(d["id"]), title=a.get("name", ""), artist=a.get("artistName", ""),
                     album=a.get("albumName", ""), duration=round(dur / 1000) if dur else None,
                     isrc=a.get("isrc"), url=a.get("url"), explicit=_explicit(a), artwork=_artwork(a))

    @staticmethod
    def _library(it: dict) -> Track | None:
        if it.get("type") == "songs":
            return AppleProvider._catalog(it)
        if it.get("type") != "library-songs":
            return None
        a = it.get("attributes") or {}
        cat = ((it.get("relationships") or {}).get("catalog") or {}).get("data") or []
        ca = (cat[0].get("attributes") or {}) if cat else {}
        cid = (a.get("playParams") or {}).get("catalogId") or (cat[0]["id"] if cat else None)
        if not cid:
            return None  # uploaded file / not in the catalog
        dur = a.get("durationInMillis") or ca.get("durationInMillis")
        return Track(service=APPLE, id=str(cid), title=a.get("name") or ca.get("name") or "",
                     artist=a.get("artistName") or ca.get("artistName") or "",
                     album=a.get("albumName") or ca.get("albumName") or "",
                     duration=round(dur / 1000) if dur else None, isrc=ca.get("isrc"), url=ca.get("url"),
                     explicit=_explicit(ca) if ca else _explicit(a), artwork=_artwork(a) or _artwork(ca),
                     handle={"library_id": it.get("id")})

    # ------------------------------------------------------------------ collections
    def _playlists(self) -> list[dict]:
        params = {"platform": "web", "extend[library-playlists]": "tags", "limit": "100"}
        try:
            return [p for p in self._paged("/v1/me/library/playlists", params) if p.get("type") == "library-playlists"]
        except ApiError:
            return [p for p in self._paged("/v1/me/library/playlist-folders/p.playlistsroot/children", params)
                    if p.get("type") == "library-playlists"]

    def _favorites_id(self) -> str | None:
        for p in self._playlists():
            if "favorited" in ((p.get("attributes") or {}).get("tags") or []):
                return p["id"]
        return None

    def collections(self) -> list[Collection]:
        lib_total = None
        try:
            lib_total = (self._req("GET", "/v1/me/library/songs", {"limit": "1"}).get("meta") or {}).get("total")
        except ApiError:
            pass
        out = [Collection(LIKED, "", "Favourite songs"), Collection(LIBRARY, "", "Library", lib_total)]
        for p in self._playlists():
            a = p.get("attributes") or {}
            if "favorited" in (a.get("tags") or []) or not a.get("canEdit", True):
                continue
            out.append(Collection(PLAYLIST, p["id"], a.get("name") or "Playlist"))
        return out

    def read(self, kind: str, collection_id: str) -> list[Track]:
        params = {"limit": "100", "include[library-songs]": "catalog", "include": "catalog"}
        if kind == LIKED:
            pid = self._favorites_id()
            items = self._paged(f"/v1/me/library/playlists/{pid}/tracks", params) if pid else []
        elif kind == LIBRARY:
            items = self._paged("/v1/me/library/songs", params)
        elif kind == PLAYLIST:
            items = self._paged(f"/v1/me/library/playlists/{collection_id}/tracks", params)
        else:
            raise NotSupported(kind)
        return unique([self._library(it) for it in items])

    def _add_ids(self, kind: str, collection_id: str, ids: list[str]) -> None:
        if kind == LIKED:
            self._req("POST", "/v1/me/favorites", {"ids[songs]": ",".join(ids)})
        elif kind == LIBRARY:
            self._req("POST", "/v1/me/library", {"ids[songs]": ",".join(ids), "representation": "ids"})
        elif kind == PLAYLIST:
            self._req("POST", f"/v1/me/library/playlists/{collection_id}/tracks",
                      {"representation": "resources"},
                      body={"data": [{"id": i, "type": "songs"} for i in ids]})
        else:
            raise NotSupported(kind)

    def add(self, kind: str, collection_id: str, tracks: list[Track]) -> list[str]:
        """Add in batches; if Apple rejects a batch (e.g. favorites only take one id, or one song
        is unavailable in the storefront), retry its songs one by one so only the bad ones fail."""
        failed: list[str] = []
        for batch in chunks([t.id for t in tracks], 25):
            try:
                self._add_ids(kind, collection_id, batch)
            except ApiError as e:
                if len(batch) == 1 or (e.status is not None and e.status not in (400, 404, 422)):
                    log.warning("Apple Music: adding failed (%s): %s", kind, e)
                    failed += batch
                else:
                    log.info("Apple Music: batch rejected (%s), trying one by one: %s", kind, e)
                    for i in batch:
                        try:
                            self._add_ids(kind, collection_id, [i])
                        except ApiError as e1:
                            log.warning("Apple Music: song %s not added (%s): %s", i, kind, e1)
                            failed.append(i)
                        time.sleep(0.2)
            time.sleep(0.2)
        return failed

    def remove(self, kind: str, collection_id: str, tracks: list[Track]) -> list[str]:
        failed: list[str] = []
        for t in tracks:
            lib_id = t.handle.get("library_id")
            try:
                if kind == LIKED:
                    self._req("DELETE", "/v1/me/favorites", {"ids[songs]": t.id})
                elif kind == LIBRARY:
                    if not lib_id:
                        raise ApiError("library ID missing")
                    try:
                        self._req("DELETE", f"/v1/me/library/songs/{lib_id}")
                    except ApiError as e:
                        if e.status != 404:
                            raise
                        self._req("DELETE", f"/v1/me/library/library-songs/{lib_id}")
                elif kind == PLAYLIST:
                    if not lib_id:
                        raise ApiError("playlist entry ID missing")
                    self._req("DELETE", f"/v1/me/library/playlists/{collection_id}/tracks",
                              {"ids[library-songs]": lib_id, "mode": "all"})
                else:
                    raise NotSupported(kind)
            except ApiError:
                failed.append(t.id)
            time.sleep(0.15)
        return failed

    def delete_playlist(self, collection_id: str) -> None:
        self._req("DELETE", f"/v1/me/library/playlists/{collection_id}")

    def create_playlist(self, name: str, description: str = "") -> Collection:
        d = self._req("POST", "/v1/me/library/playlists", body={
            "attributes": {"name": name, "description": description or "Synced with Crossfade",
                           "isPublic": False}})
        p = (d.get("data") or [{}])[0]
        if not p.get("id"):
            raise ApiError("Apple Music didn't return a playlist ID.")
        return Collection(PLAYLIST, p["id"], name, 0)

    # ------------------------------------------------------------------ catalog
    # Apple blocks catalog *search* with a 429 "Request is forbidden" (code 42900) – first for calls with
    # the user token, then also anonymous ones – and the block lasts for hours, while song lookups by id
    # or ISRC keep working. So: try the mode that worked last, switch on a 429, and when both are
    # refused, search the public iTunes Search API (same catalog ids) until the block is over.
    _search_as_user: bool | None = None
    _amp_blocked_until: float = 0.0
    _itunes_lock = threading.Lock()
    _itunes_last: float = 0.0
    AMP_BLOCK_PAUSE = 20 * 60
    ITUNES_GAP = 3.2          # documented limit: about 20 calls per minute

    def search(self, query: str, limit: int = 10) -> list[Track]:
        query = query[:200]
        if time.time() >= AppleProvider._amp_blocked_until:
            d = self._amp_search(query, limit)
            if d is not None:
                songs = ((d.get("results") or {}).get("songs") or {}).get("data") or []
                return [self._catalog(s) for s in songs]
            AppleProvider._amp_blocked_until = time.time() + self.AMP_BLOCK_PAUSE
            log.warning("Apple Music blocks catalogue search (429), using iTunes Search for %d min",
                        self.AMP_BLOCK_PAUSE // 60)
        return self._itunes_search(query, limit)

    def _amp_search(self, query: str, limit: int) -> dict | None:
        path = f"/v1/catalog/{self.storefront()}/search"
        params = {"term": query, "types": "songs", "limit": str(min(limit, 25))}
        first = self._has_user if AppleProvider._search_as_user is None else AppleProvider._search_as_user
        for user in ([first, not first] if self._has_user else [False]):
            try:
                d = self._req("GET", path, params, user=user, patient=False)
            except ApiError as e:
                if e.status != 429:
                    raise
                continue
            if AppleProvider._search_as_user != user:
                log.info("Apple Music: searching %s the account now", "with" if user else "without")
            AppleProvider._search_as_user = user
            return d
        return None

    def _itunes_search(self, query: str, limit: int) -> list[Track]:
        res = self._itunes_get("https://itunes.apple.com/search",
                               {"term": query, "entity": "song", "media": "music", "country": self.storefront(),
                                "limit": str(min(limit, 25))})
        return self._hydrate([t for t in (self._itunes(x) for x in res) if t])

    def search_alt(self, query: str, limit: int = 10) -> list[Track]:
        """The public iTunes search – a different index than the catalog search, finds other songs."""
        return self._itunes_search(query[:200], limit)

    _artist_cache: dict[tuple[str, str], tuple[float, list[Track]]] = {}
    ARTIST_TTL = 6 * 3600

    def artist_tracks(self, artist: str) -> list[Track]:
        from rapidfuzz import fuzz

        from ..matcher import _base
        sf = self.storefront()
        key = (sf, _base(artist))
        hit = AppleProvider._artist_cache.get(key)
        if hit and time.time() - hit[0] < self.ARTIST_TTL:
            return hit[1]
        found = self._itunes_get("https://itunes.apple.com/search",
                                 {"term": artist[:100], "entity": "musicArtist", "country": sf, "limit": "5"})
        ids = [x["artistId"] for x in found if x.get("artistId")
               and fuzz.ratio(_base(x.get("artistName") or ""), key[1]) >= 88][:2]
        out: list[Track] = []
        for aid in ids:
            res = self._itunes_get("https://itunes.apple.com/lookup",
                                   {"id": str(aid), "entity": "song", "limit": "200", "country": sf})
            out += [t for t in (self._itunes(x) for x in res) if t]
        AppleProvider._artist_cache[key] = (time.time(), out)
        return out

    def _itunes_get(self, url: str, params: dict) -> list[dict]:
        for attempt in range(4):
            with AppleProvider._itunes_lock:
                wait = AppleProvider._itunes_last + self.ITUNES_GAP - time.time()
                if wait > 0:
                    time.sleep(wait)
                AppleProvider._itunes_last = time.time()
                try:
                    r = httpx.get(url, params=params, timeout=20, headers={"User-Agent": UA})
                except httpx.HTTPError as e:
                    if attempt == 3:
                        raise ApiError(f"iTunes Search is unreachable: {e}") from e
                    continue
            if r.status_code in (403, 429) or r.status_code >= 500:
                time.sleep(10 * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise ApiError(f"iTunes Search: HTTP {r.status_code}", r.status_code)
            try:
                return r.json().get("results") or []
            except ValueError:
                return []
        raise ApiError("Apple Music and iTunes Search are overloaded right now (429).", 429)

    def _hydrate(self, tracks: list[Track]) -> list[Track]:
        """iTunes results lack the ISRC – fetch the catalog entries (one lookup by id, not throttled)."""
        if not tracks:
            return tracks
        try:
            d = self._req("GET", f"/v1/catalog/{self.storefront()}/songs",
                          {"ids": ",".join(t.id for t in tracks)}, user=False, patient=False)
        except ApiError:
            return tracks
        full = {str(x.get("id")): self._catalog(x) for x in d.get("data") or []}
        return [full.get(t.id, t) for t in tracks]

    @staticmethod
    def _itunes(x: dict) -> Track | None:
        if x.get("kind") != "song" or not x.get("trackId") or x.get("isStreamable") is False:
            return None
        ms = x.get("trackTimeMillis")
        art = x.get("artworkUrl100") or ""
        return Track(service=APPLE, id=str(x["trackId"]), title=x.get("trackName") or "",
                     artist=x.get("artistName") or "", album=x.get("collectionName") or "",
                     duration=round(ms / 1000) if ms else None,
                     url=(x.get("trackViewUrl") or "").split("&uo=")[0] or None,
                     explicit={"explicit": True, "cleaned": False}.get(x.get("trackExplicitness") or ""),
                     artwork=art.replace("100x100bb", "300x300bb") or None)

    def by_isrc(self, isrc: str) -> list[Track]:
        d = self._req("GET", f"/v1/catalog/{self.storefront()}/songs", {"filter[isrc]": isrc}, user=self._has_user)
        return [self._catalog(s) for s in d.get("data", [])]

    def track(self, track_id: str) -> Track | None:
        try:
            d = self._req("GET", f"/v1/catalog/{self.storefront()}/songs/{track_id}", user=self._has_user)
        except ApiError as e:
            if e.status == 404:
                return None
            raise
        data = d.get("data") or []
        return self._catalog(data[0]) if data else None

    @staticmethod
    def parse_link(text: str) -> str | None:
        text = (text or "").strip()
        if text.isdigit():
            return text
        u = urlparse(text)
        if "apple.com" in u.netloc:
            q = parse_qs(u.query)
            if "i" in q:
                return q["i"][0]
            m = re.search(r"/song/(?:[^/]+/)?(\d+)", u.path)
            if m:
                return m.group(1)
        return None
