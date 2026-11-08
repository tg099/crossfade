"""YouTube Music.

Two ways in:
- browser cookie (pasted cURL command): everything via ytmusicapi (YouTube Music's internal API).
- Google OAuth device flow with the instance's own Google Cloud client: YouTube Music's internal
  web API rejects such tokens (HTTP 400 "invalid argument", verified 2026-11-07), but the official
  YouTube Data API v3 accepts them. So likes and playlists go through the Data API, while catalog
  search and song details use ytmusicapi without login. The Data API cannot see the library.
"""
from __future__ import annotations

import json
import logging
import re
import shlex
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from ytmusicapi import LikeStatus, OAuthCredentials, YTMusic
from ytmusicapi.helpers import initialize_headers

from ..matcher import tidy_video
from ..models import LIBRARY, LIKED, PLAYLIST, YTM, ApiError, AuthError, Collection, NotSupported, Track
from .base import Provider, chunks, unique

SPECIAL_PLAYLISTS = {"LM", "SE"}
log = logging.getLogger(__name__)
DATA_API = "https://www.googleapis.com/youtube/v3"


# ---------------------------------------------------------------------- auth helpers
def cookie_auth(cookie: str, authuser: int = 0) -> dict:
    if "__Secure-3PAPISID" not in cookie:
        raise AuthError("The cookie has no __Secure-3PAPISID. Are you signed in on music.youtube.com?")
    h = dict(initialize_headers())
    h.update({"cookie": cookie, "x-goog-authuser": str(authuser), "authorization": "SAPISIDHASH 0_0",
              "origin": "https://music.youtube.com", "x-origin": "https://music.youtube.com"})
    return {"type": "browser", "headers": h}


def parse_pasted(raw: str) -> dict:
    """Accept 'Copy as cURL' (Chrome/Brave/Edge/Firefox/Safari), raw request headers or a cookie string."""
    raw = (raw or "").strip()
    cookie, authuser = None, 0
    if raw.lower().startswith("curl"):
        text = raw.replace("\\\r\n", " ").replace("\\\n", " ").replace("^\r\n", " ").replace("^\n", " ")
        text = text.replace("^\"", "\"")
        try:
            parts = shlex.split(text)
        except ValueError as e:
            raise AuthError(f"The cURL command is incomplete: {e}") from e
        for flag, val in zip(parts, parts[1:], strict=False):
            if flag in ("-b", "--cookie"):
                cookie = val
            elif flag in ("-H", "--header") and ":" in val:
                k, v = val.split(":", 1)
                if k.strip().lower() == "cookie":
                    cookie = v.strip()
                elif k.strip().lower() == "x-goog-authuser" and v.strip().isdigit():
                    authuser = int(v.strip())
    else:
        for line in raw.splitlines():
            if ":" in line and line.split(":", 1)[0].strip().lower() == "cookie":
                cookie = line.split(":", 1)[1].strip()
            elif ":" in line and line.split(":", 1)[0].strip().lower() == "x-goog-authuser":
                v = line.split(":", 1)[1].strip()
                authuser = int(v) if v.isdigit() else 0
        if cookie is None and "__Secure-3PAPISID=" in raw:
            cookie = raw
    if not cookie:
        raise AuthError("It contains no cookie. Copy a request from music.youtube.com "
                        "while you're signed in.")
    return cookie_auth(cookie, authuser)


class DeviceFlow:
    """Google OAuth device flow (the user enters a code on google.com/device)."""

    def __init__(self, client_id: str, client_secret: str):
        self.creds = OAuthCredentials(client_id=client_id, client_secret=client_secret)

    def start(self) -> dict:
        try:
            c = self.creds.get_code()
        except Exception as e:  # noqa: BLE001
            raise AuthError(f"Google rejected the code sign-in: {e}") from e
        return {"device_code": c["device_code"], "user_code": c["user_code"],
                "verification_url": c.get("verification_url") or "https://www.google.com/device",
                "interval": int(c.get("interval", 5)), "expires_at": time.time() + int(c.get("expires_in", 1800))}

    def poll(self, device_code: str) -> dict | None:
        """Returns the token once the user confirmed, None while pending."""
        try:
            tok = self.creds.token_from_code(device_code)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "authorization_pending" in msg or "slow_down" in msg:
                return None
            raise AuthError(f"Google sign-in failed: {msg}") from e
        if tok.get("error") in ("authorization_pending", "slow_down"):
            return None
        if tok.get("error"):
            raise AuthError(f"Google sign-in failed: {tok.get('error_description') or tok['error']}")
        tok["expires_at"] = int(time.time()) + int(tok.get("expires_in", 3600))
        if tok.get("refresh_token_expires_in"):
            # Google limits the login of apps still "in testing" (7 days) and of time-limited grants
            tok["refresh_expires_at"] = int(time.time()) + int(tok["refresh_token_expires_in"])
        return {"type": "oauth", "token": tok}


# ---------------------------------------------------------------------- official Data API (OAuth)
def _iso_duration(v: str | None) -> int | None:
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", v or "")
    if not m:
        return None
    d, h, mi, se = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


def _best_thumb(thumbs: dict | None) -> str | None:
    thumbs = thumbs or {}
    for k in ("high", "medium", "standard", "maxres", "default"):
        if (thumbs.get(k) or {}).get("url"):
            return thumbs[k]["url"]
    return None


class DataApi:
    """Minimal YouTube Data API v3 client with automatic token refresh.

    Quota (default 10,000 units/day per Google project): reads cost 1 unit per 50 items,
    every write (like, playlist insert/delete) costs 50 units."""

    def __init__(self, token: dict, oauth: OAuthCredentials, on_refresh):
        self.token, self.oauth, self.on_refresh = token, oauth, on_refresh
        self.http = httpx.Client(timeout=30)

    def _refresh(self) -> None:
        rt = self.token.get("refresh_token")
        if not rt:
            raise AuthError("The Google sign-in expired. Please reconnect YouTube Music.")
        try:
            fresh = dict(self.oauth.refresh_token(rt))
        except Exception as e:  # noqa: BLE001
            raise AuthError("The Google sign-in expired. Please reconnect YouTube Music.") from e
        if not fresh.get("access_token"):
            raise AuthError("The Google sign-in expired. Please reconnect YouTube Music.")
        fresh["expires_at"] = int(time.time()) + int(fresh.get("expires_in", 3600))
        self.token.update({k: v for k, v in fresh.items() if v})
        self.on_refresh()

    def req(self, method: str, path: str, params: dict | None = None, body: Any = None) -> dict:
        if int(self.token.get("expires_at") or 0) - time.time() < 60:
            self._refresh()
        for attempt in range(4):
            r = self.http.request(method, DATA_API + path, params=params, json=body,
                                  headers={"Authorization": f"Bearer {self.token['access_token']}"})
            if r.status_code == 401 and attempt == 0:
                self._refresh()
                continue
            if r.status_code in (429, 500, 502, 503) and attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            break
        if r.status_code == 401:
            raise AuthError("YouTube Music sign-in is invalid or expired. Please reconnect.")
        if r.status_code >= 400:
            try:
                err = r.json().get("error") or {}
                reason = ((err.get("errors") or [{}])[0]).get("reason") or ""
                msg = err.get("message") or r.text[:200]
            except ValueError:
                reason, msg = "", r.text[:200]
            if reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
                raise ApiError("The daily quota of the YouTube API is used up. The rest follows "
                               "automatically with the next sync (the quota resets at midnight Pacific time).", 403)
            if r.status_code == 403 and reason in ("insufficientPermissions", "forbidden", "accessNotConfigured"):
                raise AuthError(f"Google denied access ({reason}). Is the YouTube Data API v3 "
                                "enabled and the sign-in still valid?")
            raise ApiError(f"YouTube Data API {method} {path}: HTTP {r.status_code} {reason} {msg}".strip(),
                           r.status_code)
        return r.json() if r.content else {}

    def paged(self, path: str, params: dict) -> list[dict]:
        out: list[dict] = []
        params = {**params, "maxResults": 50}
        while True:
            d = self.req("GET", path, params)
            out += d.get("items") or []
            tok = d.get("nextPageToken")
            if not tok:
                return out
            params["pageToken"] = tok

    def videos(self, ids: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for batch in chunks(ids, 50):
            d = self.req("GET", "/videos", {"part": "snippet,contentDetails", "id": ",".join(batch),
                                             "maxResults": 50})
            for v in d.get("items") or []:
                out[v["id"]] = v
        return out

    def tracks(self, items: list[dict]) -> list[Track]:
        """playlistItems -> Tracks (title/artist/duration from videos.list, 1 unit per 50)."""
        vids = [((i.get("snippet") or {}).get("resourceId") or {}).get("videoId") for i in items]
        details = self.videos([v for v in vids if v])
        out: list[Track] = []
        for item, vid in zip(items, vids, strict=True):
            v = details.get(vid or "")
            if not v:          # deleted or private video
                continue
            sn = v.get("snippet") or {}
            channel = sn.get("channelTitle") or ""
            title = sn.get("title") or ""
            artist = re.sub(r"\s-\sTopic$", "", channel)
            is_topic = channel.endswith(" - Topic")
            if not is_topic and " - " in title:     # "Artist - Title (Official Video)"
                artist, title = (x.strip() for x in title.split(" - ", 1))
            artist = re.sub(r"(VEVO|Official)$", "", artist).strip()
            album = ""
            if is_topic:
                title, artist, album = _topic_meta(sn.get("description") or "", title, artist)
            out.append(Track(service=YTM, id=vid, title=title, artist=artist, album=album,
                             duration=_iso_duration((v.get("contentDetails") or {}).get("duration")),
                             url=f"https://music.youtube.com/watch?v={vid}", artwork=_best_thumb(sn.get("thumbnails")),
                             video_type="MUSIC_VIDEO_TYPE_ATV" if is_topic else None,
                             handle={"item_id": item.get("id")}))
        return out


def _topic_meta(desc: str, title: str, artist: str) -> tuple[str, str, str]:
    """Auto-generated "Artist - Topic" uploads describe the release:
    'Provided to YouTube by X\n\nTitle · Artist · Artist\n\nAlbum\n\n℗ …'."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", desc or "") if b.strip()]
    if len(blocks) < 2 or not blocks[0].startswith("Provided to YouTube"):
        return title, artist, ""
    parts = [p.strip() for p in blocks[1].split("·") if p.strip()]
    if len(parts) >= 2:
        title, artist = parts[0], ", ".join(parts[1:])
    album = blocks[2] if len(blocks) > 2 and not blocks[2].startswith(("℗", "©", "Released on")) else ""
    return title, artist, album


class MetaStore:
    def get_many(self, ids: list[str]) -> dict[str, dict]:
        raise NotImplementedError

    def put(self, video_id: str, meta: dict) -> None:
        raise NotImplementedError


class MemoryMetaStore(MetaStore):
    def __init__(self):
        self.d: dict[str, dict] = {}

    def get_many(self, ids):
        return {i: self.d[i] for i in ids if i in self.d}

    def put(self, video_id, meta):
        self.d[video_id] = meta


# ---------------------------------------------------------------------- provider
def _artists(item: dict) -> str:
    return ", ".join(a.get("name", "") for a in (item.get("artists") or []) if a.get("name"))


def _thumb(item: dict) -> str | None:
    thumbs = item.get("thumbnails") or []
    if not thumbs:
        return None
    url = thumbs[-1].get("url") or ""
    # googleusercontent images can be requested in any size
    return re.sub(r"=w\d+-h\d+", "=w300-h300", url) if "=w" in url else url


def _track(item: dict, handle: dict | None = None) -> Track | None:
    vid = item.get("videoId")
    if not vid:
        return None
    return Track(service=YTM, id=vid, title=item.get("title") or "", artist=_artists(item),
                 album=((item.get("album") or {}).get("name") or ""), duration=item.get("duration_seconds"),
                 url=f"https://music.youtube.com/watch?v={vid}", explicit=item.get("isExplicit"),
                 video_type=item.get("videoType"), artwork=_thumb(item), handle=handle or {})


class YTMProvider(Provider):
    service = YTM
    _lock = threading.Lock()

    def __init__(self, credentials, save_credentials=None, oauth_client: tuple[str, str] | None = None,
                 meta_store: MetaStore | None = None):
        super().__init__(credentials, save_credentials)
        self.meta_store = meta_store or MemoryMetaStore()
        try:
            self.api: DataApi | None = None
            if credentials.get("type") == "oauth":
                if not oauth_client:
                    raise AuthError("Google sign-in needs a client ID and secret in the instance settings.")
                # internal API without login (search, song details); library via the Data API
                self.yt = YTMusic()
                self.api = DataApi(credentials["token"], OAuthCredentials(*oauth_client), self.persist)
            elif credentials.get("type") == "browser":
                self.yt = YTMusic(credentials["headers"])
            else:
                self.yt = YTMusic()
        except AuthError:
            raise
        except Exception as e:  # noqa: BLE001
            raise AuthError(f"YouTube Music sign-in is invalid: {e}") from e

    def _persist_token(self) -> None:
        tok = getattr(self.yt, "_token", None)
        if self.credentials.get("type") == "oauth" and tok is not None and self.api is None:
            try:
                d = tok.as_dict()
                if d.get("access_token") and d["access_token"] != self.credentials["token"].get("access_token"):
                    self.credentials["token"].update(d)
                    self.persist()
            except Exception:  # noqa: BLE001
                pass

    def _call(self, fn, *args, **kwargs) -> Any:
        last: Exception | None = None
        for attempt in range(4):
            try:
                res = fn(*args, **kwargs)
                self._persist_token()
                return res
            except AuthError:
                raise
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                if re.search(r"\b(401|403)\b", msg) or any(s in msg for s in ("Sign in", "invalid_grant")) \
                        or "not logged in" in msg.lower():
                    raise AuthError("YouTube Music sign-in is invalid or expired. Please reconnect.") from e
                
                last = e
                time.sleep(2 * (attempt + 1))
        raise ApiError(f"YouTube Music: {last}")

    # ------------------------------------------------------------------ account
    def login_expires_at(self) -> float | None:
        if self.credentials.get("type") == "oauth":
            return (self.credentials.get("token") or {}).get("refresh_expires_at")
        return None

    def whoami(self) -> str:
        if self.api:
            items = self.api.req("GET", "/channels", {"part": "snippet", "mine": "true"}).get("items") or []
            return ((items[0].get("snippet") or {}).get("title") if items else None) or "YouTube Music"
        info = self._call(self.yt.get_account_info)
        return info.get("accountName") or info.get("channelHandle") or "YouTube Music"

    # ------------------------------------------------------------------ collections
    def collections(self) -> list[Collection]:
        if self.api:   # the Data API has no access to the YouTube Music library
            out = [Collection(LIKED, "", "Liked videos")]
            for p in self.api.paged("/playlists", {"part": "snippet,contentDetails", "mine": "true"}):
                if p.get("id") in SPECIAL_PLAYLISTS:
                    continue
                out.append(Collection(PLAYLIST, p["id"], (p.get("snippet") or {}).get("title") or "Playlist",
                                      (p.get("contentDetails") or {}).get("itemCount")))
            return out
        out = [Collection(LIKED, "", "Liked music"), Collection(LIBRARY, "", "Library – Songs")]
        for p in self._call(self.yt.get_library_playlists, limit=None) or []:
            pid = p.get("playlistId")
            if not pid or pid in SPECIAL_PLAYLISTS:
                continue
            count = p.get("count")
            try:
                count = int(str(count).split()[0].replace(".", "").replace(",", "")) if count else None
            except ValueError:
                count = None
            out.append(Collection(PLAYLIST, pid, p.get("title") or "Playlist", count))
        return out

    # ------------------------------------------------------------------ video metadata
    META_RETRY = 7 * 86400
    META_WORKERS = 4

    def _enrich(self, tracks: list[Track]) -> None:
        """The Data API only knows *videos* ("Cro - Noch da (Official Version)" on "Chimperator
        Channel"). YouTube Music knows the song behind it (title "Noch da", artist "CRO", whether
        it is an audio track or a music video) – ask it once per video, without login, and keep it."""
        todo = [t for t in tracks if t.video_type != "MUSIC_VIDEO_TYPE_ATV"]
        known = self.meta_store.get_many([t.id for t in todo])
        now = time.time()
        missing = [t.id for t in todo if t.id not in known
                   or (not known[t.id].get("title") and now - known[t.id].get("at", 0) > self.META_RETRY)]
        if missing:
            local = threading.local()

            def fetch(vid: str) -> tuple[str, dict]:
                yt = getattr(local, "yt", None) or YTMusic()
                local.yt = yt
                try:
                    wp = yt.get_watch_playlist(vid, limit=1)
                    it = next((x for x in wp.get("tracks") or [] if x.get("videoId") == vid), None)
                except Exception as e:  # noqa: BLE001
                    log.info("YouTube Music: no song details for %s (%s)", vid, e)
                    it = None
                if not it:
                    return vid, {"at": now}
                return vid, {"at": now, "title": it.get("title") or "", "artist": _artists(it),
                             "album": (it.get("album") or {}).get("name") or "",
                             "video_type": it.get("videoType"), "explicit": it.get("isExplicit")}

            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(self.META_WORKERS) as pool:
                for vid, meta in pool.map(fetch, missing):
                    known[vid] = meta
                    self.meta_store.put(vid, meta)
        for t in todo:
            meta = known.get(t.id) or {}
            if meta.get("title"):
                t.title, t.artist = meta["title"], meta.get("artist") or t.artist
                t.album = meta.get("album") or t.album
                t.video_type = meta.get("video_type") or t.video_type
                if meta.get("explicit") is not None:
                    t.explicit = meta["explicit"]
            if t.video_type != "MUSIC_VIDEO_TYPE_ATV":
                t.title, t.artist = tidy_video(t.title, t.artist, t.video_type)

    def read(self, kind: str, collection_id: str) -> list[Track]:
        tracks: list[Track | None] = []
        if self.api:
            if kind == LIBRARY:
                raise NotSupported("The library isn't available with the Google code sign-in. "
                                   "Connect YouTube Music via the browser to use it.")
            pid = "LM" if kind == LIKED else collection_id
            tracks = list(self.api.tracks(self.api.paged("/playlistItems", {"part": "snippet", "playlistId": pid})))
            self._enrich([t for t in tracks if t])
        elif kind == LIKED:
            pl = self._call(self.yt.get_playlist, "LM", limit=None)
            tracks = [_track(i) for i in pl.get("tracks") or []]
        elif kind == LIBRARY:
            items = self._call(self.yt.get_library_songs, limit=None) or []
            tracks = [_track(i, {"remove_token": (i.get("feedbackTokens") or {}).get("remove")}) for i in items]
        elif kind == PLAYLIST:
            pl = self._call(self.yt.get_playlist, collection_id, limit=None)
            tracks = [_track(i, {"setVideoId": i.get("setVideoId")}) for i in pl.get("tracks") or []]
        else:
            raise NotSupported(kind)
        for t in tracks:
            if t and not self.api and t.video_type in ("MUSIC_VIDEO_TYPE_OMV", "MUSIC_VIDEO_TYPE_UGC"):
                t.title, t.artist = tidy_video(t.title, t.artist, t.video_type)
        return unique(tracks)

    def _add_token(self, video_id: str) -> str | None:
        """Feedback token to add a song to the library: taken from the song's album page."""
        wp = self._call(self.yt.get_watch_playlist, video_id, limit=1)
        first = (wp.get("tracks") or [{}])[0]
        album_id = ((first.get("album") or {}).get("id"))
        if not album_id:
            return None
        album = self._call(self.yt.get_album, album_id)
        for t in album.get("tracks") or []:
            if t.get("videoId") == video_id:
                return (t.get("feedbackTokens") or {}).get("add")
        return None

    def _api_each(self, tracks: list[Track], fn) -> list[str]:
        failed: list[str] = []
        for i, t in enumerate(tracks):
            try:
                fn(t)
            except ApiError as e:
                log.warning("YouTube Data API: %s failed: %s", t.id, e)
                failed.append(t.id)
                if e.status == 403 and "daily quota" in str(e):
                    return failed + [x.id for x in tracks[i + 1:]]   # no point trying the rest today
            time.sleep(0.1)
        return failed

    def add(self, kind: str, collection_id: str, tracks: list[Track]) -> list[str]:
        if self.api:
            if kind == LIKED:
                return self._api_each(tracks, lambda t: self.api.req("POST", "/videos/rate", {"id": t.id, "rating": "like"}))
            if kind == PLAYLIST:
                return self._api_each(tracks, lambda t: self.api.req(
                    "POST", "/playlistItems", {"part": "snippet"},
                    {"snippet": {"playlistId": collection_id, "resourceId": {"kind": "youtube#video", "videoId": t.id}}}))
            raise NotSupported(kind)
        failed: list[str] = []
        if kind == LIKED:
            for t in tracks:
                try:
                    self._call(self.yt.rate_song, t.id, LikeStatus.LIKE)
                except ApiError:
                    failed.append(t.id)
                time.sleep(0.2)
        elif kind == LIBRARY:
            for t in tracks:
                try:
                    tok = self._add_token(t.id)
                    if not tok:
                        raise ApiError("no album entry found")
                    self._call(self.yt.edit_song_library_status, [tok])
                except ApiError:
                    failed.append(t.id)
        elif kind == PLAYLIST:
            for batch in chunks([t.id for t in tracks], 50):
                try:
                    res = self._call(self.yt.add_playlist_items, collection_id, batch, duplicates=False)
                    if isinstance(res, dict) and res.get("status") not in (None, "STATUS_SUCCEEDED"):
                        raise ApiError(str(res.get("status")))
                except ApiError:
                    failed += batch
        else:
            raise NotSupported(kind)
        return failed

    def remove(self, kind: str, collection_id: str, tracks: list[Track]) -> list[str]:
        if self.api:
            if kind == LIKED:
                return self._api_each(tracks, lambda t: self.api.req("POST", "/videos/rate", {"id": t.id, "rating": "none"}))
            if kind == PLAYLIST:
                def rm(t: Track) -> None:
                    if not t.handle.get("item_id"):
                        raise ApiError("playlist entry ID missing")
                    self.api.req("DELETE", "/playlistItems", {"id": t.handle["item_id"]})
                return self._api_each(tracks, rm)
            raise NotSupported(kind)
        failed: list[str] = []
        if kind == LIKED:
            for t in tracks:
                try:
                    self._call(self.yt.rate_song, t.id, LikeStatus.INDIFFERENT)
                except ApiError:
                    failed.append(t.id)
                time.sleep(0.2)
        elif kind == LIBRARY:
            for batch in chunks(tracks, 25):
                tokens = [t.handle.get("remove_token") for t in batch]
                if not all(tokens):
                    failed += [t.id for t in batch if not t.handle.get("remove_token")]
                    batch = [t for t in batch if t.handle.get("remove_token")]
                    tokens = [t.handle["remove_token"] for t in batch]
                if not batch:
                    continue
                try:
                    self._call(self.yt.edit_song_library_status, tokens)
                except ApiError:
                    failed += [t.id for t in batch]
        elif kind == PLAYLIST:
            for batch in chunks(tracks, 50):
                videos = [{"videoId": t.id, "setVideoId": t.handle.get("setVideoId")} for t in batch
                          if t.handle.get("setVideoId")]
                failed += [t.id for t in batch if not t.handle.get("setVideoId")]
                if not videos:
                    continue
                try:
                    self._call(self.yt.remove_playlist_items, collection_id, videos)
                except ApiError:
                    failed += [v["videoId"] for v in videos]
        else:
            raise NotSupported(kind)
        return failed

    def create_playlist(self, name: str, description: str = "") -> Collection:
        if self.api:
            d = self.api.req("POST", "/playlists", {"part": "snippet,status"}, {
                "snippet": {"title": name, "description": description or "Synced with Crossfade"},
                "status": {"privacyStatus": "private"}})
            if not d.get("id"):
                raise ApiError("YouTube didn't return a playlist ID.")
            return Collection(PLAYLIST, d["id"], name, 0)
        pid = self._call(self.yt.create_playlist, name, description or "Synced with Crossfade",
                         privacy_status="PRIVATE")
        if not isinstance(pid, str):
            raise ApiError(f"Couldn't create the playlist: {json.dumps(pid)[:200]}")
        return Collection(PLAYLIST, pid, name, 0)

    def remove_copies(self, kind: str, collection_id: str, track: Track) -> None:
        """YouTube knows every playlist entry by itself: only the further copies are removed."""
        extra = track.handle.get("extra") or []
        if kind != PLAYLIST or not extra:
            return
        if self.api:
            ids = [h.get("item_id") for h in extra]
            if not all(ids):
                raise ApiError("playlist entry ID missing")
            for i in ids:
                self.api.req("DELETE", "/playlistItems", {"id": i})
            return
        sets = [h.get("setVideoId") for h in extra]
        if not all(sets):
            raise ApiError("playlist entry ID missing")
        self._call(self.yt.remove_playlist_items, collection_id, [{"videoId": track.id, "setVideoId": x} for x in sets])

    def delete_playlist(self, collection_id: str) -> None:
        if self.api:
            self.api.req("DELETE", "/playlists", {"id": collection_id})
        else:
            self._call(self.yt.delete_playlist, collection_id)

    def counterpart(self, video_id: str) -> Track | None:
        """The other form of a song YouTube Music links: the music video of an audio track ("song"),
        or the audio track of a music video. None if there is none."""
        wp = self._call(self.yt.get_watch_playlist, video_id, limit=1)
        first = next((x for x in wp.get("tracks") or [] if x.get("videoId") == video_id), None) \
            or (wp.get("tracks") or [{}])[0]
        cp = first.get("counterpart")
        if not cp or not cp.get("videoId"):
            return None
        length = cp.get("length") or ""
        secs = None
        if re.fullmatch(r"\d+:\d{2}", length):
            mm, ss = length.split(":")
            secs = int(mm) * 60 + int(ss)
        is_song = first.get("videoType") in ("MUSIC_VIDEO_TYPE_OMV", "MUSIC_VIDEO_TYPE_UGC")
        return Track(service=YTM, id=cp["videoId"], title=cp.get("title") or "", artist=_artists(cp),
                     album=((cp.get("album") or {}).get("name") or ""), duration=secs,
                     url=f"https://music.youtube.com/watch?v={cp['videoId']}", explicit=cp.get("isExplicit"),
                     artwork=_thumb(cp),
                     video_type=cp.get("videoType") or ("MUSIC_VIDEO_TYPE_ATV" if is_song else "MUSIC_VIDEO_TYPE_OMV"))

    def audio_version(self, video_id: str) -> Track | None:
        """For a music video: the audio track ("song") YouTube Music links to it, if any."""
        cp = self.counterpart(video_id)
        return cp if cp and cp.video_type == "MUSIC_VIDEO_TYPE_ATV" else None

    # ------------------------------------------------------------------ catalog
    def search(self, query: str, limit: int = 10) -> list[Track]:
        res = self._call(self.yt.search, query[:200], filter="songs", limit=limit)
        return [t for t in (_track(r) for r in res) if t]

    def by_isrc(self, isrc: str) -> list[Track]:
        return self.search(isrc, limit=3)

    def track(self, track_id: str) -> Track | None:
        d = self._call(self.yt.get_song, track_id)
        vd = d.get("videoDetails") or {}
        if not vd.get("videoId"):
            return None
        return Track(service=YTM, id=vd["videoId"], title=vd.get("title", ""),
                     artist=re.sub(r"\s-\sTopic$", "", vd.get("author", "")),
                     duration=int(vd["lengthSeconds"]) if vd.get("lengthSeconds") else None,
                     url=f"https://music.youtube.com/watch?v={vd['videoId']}")

    @staticmethod
    def parse_link(text: str) -> str | None:
        text = (text or "").strip()
        if re.fullmatch(r"[\w-]{11}", text):
            return text
        u = urlparse(text)
        if "youtube" in u.netloc and "v" in parse_qs(u.query):
            return parse_qs(u.query)["v"][0]
        if "youtu.be" in u.netloc:
            return u.path.strip("/")[:11] or None
        return None
