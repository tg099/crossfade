"""Listening samples for the in-app player.

- Apple Music: the 30 s preview from the catalog (amp-api `previews`, iTunes lookup as fallback).
- Spotify: the Web API no longer hands out `preview_url` for new apps, but the public embed page
  still carries the 30 s clip (`audioPreview`).
- YouTube Music: the whole video, played by the YouTube embed in the browser.

Results are cached in memory; preview URLs are stable for a long time.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable

import httpx

from .models import APPLE, DEEZER, SPOTIFY, TIDAL, YTM

log = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/18.0 Safari/605.1.15")

ID_PATTERNS = {
    APPLE: re.compile(r"^\d{1,15}$"),
    SPOTIFY: re.compile(r"^[A-Za-z0-9]{22}$"),
    YTM: re.compile(r"^[A-Za-z0-9_-]{11}$"),
    DEEZER: re.compile(r"^\d{1,14}$"),
    TIDAL: re.compile(r"^\d{1,14}$"),
}
# only ever hand out audio from these hosts (the page's CSP allows exactly them)
AUDIO_HOSTS = re.compile(r"^https://([\w-]+\.)*(itunes\.apple\.com|mzstatic\.com|scdn\.co|dzcdn\.net)/")
_SPOTIFY_PREVIEW = re.compile(r'"audioPreview"\s*:\s*\{\s*"url"\s*:\s*"(https://[^"]+)"')

TTL = 6 * 3600
MISS_TTL = 15 * 60
_cache: dict[tuple[str, str, str], tuple[float, dict | None]] = {}
_lock = threading.Lock()


def valid_id(service: str, track_id: str) -> bool:
    p = ID_PATTERNS.get(service)
    return bool(p and p.match(track_id))


def _audio(url: str | None) -> dict | None:
    if url and AUDIO_HOSTS.match(url):
        return {"kind": "audio", "url": url}
    return None


def apple_preview(track_id: str, storefront: str, amp: Callable[[str], dict] | None = None) -> dict | None:
    if amp:
        try:
            d = amp(f"/v1/catalog/{storefront}/songs/{track_id}")
            for s in d.get("data") or []:
                for p in (s.get("attributes") or {}).get("previews") or []:
                    if found := _audio(p.get("url")):
                        return found
        except Exception as e:  # noqa: BLE001 - fall back to the public lookup
            log.debug("amp preview %s: %s", track_id, e)
    r = httpx.get("https://itunes.apple.com/lookup", params={"id": track_id, "country": storefront},
                  headers={"User-Agent": UA}, timeout=15)
    if r.status_code != 200:
        return None
    for x in r.json().get("results") or []:
        if found := _audio(x.get("previewUrl")):
            return found
    return None


def spotify_preview(track_id: str) -> dict | None:
    r = httpx.get(f"https://open.spotify.com/embed/track/{track_id}", headers={"User-Agent": UA},
                  timeout=15, follow_redirects=True)
    if r.status_code != 200:
        return None
    m = _SPOTIFY_PREVIEW.search(r.text)
    return _audio(m.group(1)) if m else None


def deezer_preview(track_id: str) -> dict | None:
    r = httpx.get(f"https://api.deezer.com/track/{track_id}", headers={"User-Agent": UA}, timeout=15)
    if r.status_code != 200:
        return None
    return _audio((r.json() or {}).get("preview"))


def preview(service: str, track_id: str, storefront: str = "us",
            amp: Callable[[str], dict] | None = None) -> dict | None:
    """{"kind": "audio", "url": …} | {"kind": "youtube", "id": …} | None (no sample available)."""
    if service == YTM:
        return {"kind": "youtube", "id": track_id}
    if service == TIDAL:
        return None          # TIDAL offers no samples to third-party apps
    key = (service, track_id, storefront if service == APPLE else "")
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    try:
        found = (apple_preview(track_id, storefront, amp) if service == APPLE
                 else deezer_preview(track_id) if service == DEEZER else spotify_preview(track_id))
    except (httpx.HTTPError, ValueError) as e:
        log.info("preview %s/%s failed: %s", service, track_id, e)
        return None  # network trouble: don't cache, try again next time
    with _lock:
        if len(_cache) > 5000:
            _cache.clear()
        _cache[key] = (now + (TTL if found else MISS_TTL), found)
    return found
