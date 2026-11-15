"""Breaks a service asks for: a used-up quota, or "too many requests" for longer than it is worth
waiting in place. Until the break is over nothing is sent to that service – every call stops at
once with RateLimited – so a fade waits instead of failing, and asking again and again doesn't make
the break longer.

Quotas belong to the instance (the admin's app, the server's address), so a break counts for every
account of the service. A break can be narrower than the whole service: "search" (catalogue search
and lookups) or "library" (reading and changing collections), when the service limits those apart.

Without a time from the service, each break in a row lasts longer (BACKOFF); one call that goes
through starts the row over. Kept in memory: after a restart the first call finds out again.
"""
from __future__ import annotations

import email.utils
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .models import RateLimited

ALL, SEARCH, LIBRARY = "all", "search", "library"
# what Crossfade asks of a service, by kind of limit (the rest – reading and changing – is LIBRARY)
CATALOG_OPS = frozenset({"search", "search_alt", "by_isrc", "artist_tracks", "track", "counterpart", "audio_version"})
BACKOFF = (10 * 60, 30 * 60, 60 * 60, 3 * 3600, 6 * 3600)
LONGEST = 24 * 3600

# why, in words the UI translates (web/src/lib/ui/said.ts)
QUOTA = "the app's quota is used up"
YT_QUOTA = "the daily quota of the YouTube API is used up"
TOO_MANY = "too many requests"

_lock = threading.Lock()
_breaks: dict[tuple[str, str], dict] = {}       # (service, scope) -> {"until", "reason"}
_strikes: dict[tuple[str, str], int] = {}       # breaks in a row without a time from the service


def scope_of(op: str) -> str:
    return SEARCH if op in CATALOG_OPS else LIBRARY


def pause(service: str, seconds: float | None = None, reason: str = TOO_MANY, scope: str = ALL) -> RateLimited:
    """Remember a break and return the error to raise. `seconds`: what the service said (Retry-After),
    otherwise the next step of BACKOFF."""
    key = (service, scope)
    now = time.time()
    with _lock:
        cur = _breaks.get(key)
        if seconds is None or seconds <= 0:
            if cur and cur["until"] > now:          # a call that was already under way: same break
                return RateLimited(service, cur["until"], cur["reason"], scope)
            n = _strikes.get(key, 0)
            _strikes[key] = n + 1
            seconds = BACKOFF[min(n, len(BACKOFF) - 1)]
        until = now + min(seconds, LONGEST)
        if not cur or cur["until"] < until:
            _breaks[key] = {"until": until, "reason": reason}
        else:
            until, reason = cur["until"], cur["reason"]
    return RateLimited(service, until, reason, scope)


def check(service: str, op_scope: str = ALL) -> None:
    """Raise RateLimited while the service (or this kind of call) is on a break."""
    if not _breaks:
        return
    now = time.time()
    for scope in (ALL,) if op_scope == ALL else (ALL, op_scope):
        b = _breaks.get((service, scope))
        if b and b["until"] > now:
            raise RateLimited(service, b["until"], b["reason"], scope)


def went_through(service: str, op_scope: str) -> None:
    """A call was answered: the next break without a time starts short again."""
    if _strikes:
        with _lock:
            for scope in (ALL, op_scope):
                _strikes.pop((service, scope), None)


def current(service: str) -> RateLimited | None:
    """The break of the service, of any kind, that ends last (None: there is none)."""
    now = time.time()
    with _lock:
        found = [(b["until"], scope, b["reason"]) for (svc, scope), b in _breaks.items()
                 if svc == service and b["until"] > now]
    if not found:
        return None
    end, scope, reason = max(found)
    return RateLimited(service, end, reason, scope)


def active() -> dict[str, dict]:
    """Breaks going on, per service: the one that ends last."""
    now = time.time()
    out: dict[str, dict] = {}
    with _lock:
        for (service, scope), b in _breaks.items():
            if b["until"] > now and (service not in out or out[service]["until"] < b["until"]):
                out[service] = {"until": b["until"], "reason": b["reason"], "scope": scope}
    return out


def clear() -> None:
    with _lock:
        _breaks.clear()
        _strikes.clear()


def retry_after(headers) -> float | None:
    """Seconds from a Retry-After header (seconds or an HTTP date), when the service sent one."""
    raw = (headers or {}).get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    return max(0.0, when.timestamp() - time.time())


def to_pacific_midnight() -> float:
    """Seconds until Google's daily quotas start over (midnight Pacific time), plus a few minutes."""
    now = datetime.now(ZoneInfo("America/Los_Angeles"))
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    return (nxt - now).total_seconds()
