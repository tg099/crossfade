"""How the services are doing: calls, errors, rate limits and duration per service and hour.

Every provider Crossfade builds is wrapped in `Measured`, so each service – also one added later –
is counted the same way, at the level of what Crossfade asked for (read a collection, add songs,
search). Counts collect in memory and are written once per scheduler tick; two weeks are kept.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

from .db import Database
from .models import AuthError

KEEP_DAYS = 14
STARTED_AT = time.time()

# the provider methods that talk to the service
MEASURED = frozenset({"whoami", "collections", "read", "add", "remove", "create_playlist", "delete_playlist",
                      "remove_copies", "search", "by_isrc", "artist_tracks", "track", "counterpart",
                      "audio_version"})
FIELDS = ("calls", "errors", "auth_errors", "rate_limited", "failed_items", "ms")

_lock = threading.Lock()
_pending: dict[tuple[int, str], dict[str, int]] = {}
_recent: deque[dict] = deque(maxlen=40)       # last errors, newest last


def _hour(ts: float | None = None) -> int:
    return int((ts or time.time()) // 3600)


def record(service: str, op: str, ms: float, error: BaseException | None = None, failed_items: int = 0,
           rate_limited: bool = False) -> None:
    key = (_hour(), service)
    with _lock:
        row = _pending.setdefault(key, dict.fromkeys(FIELDS, 0))
        row["calls"] += 1
        row["ms"] += int(ms)
        row["failed_items"] += failed_items
        if rate_limited:
            row["rate_limited"] += 1
        if isinstance(error, AuthError):
            row["auth_errors"] += 1
        elif error is not None:
            row["errors"] += 1
        if error is not None:
            _recent.append({"ts": time.time(), "service": service, "op": op,
                            "kind": "auth" if isinstance(error, AuthError) else "error",
                            "message": str(error)[:240] or type(error).__name__})


def rate_limited(service: str) -> None:
    """A provider waited because the service said "too many requests" (and then went on)."""
    key = (_hour(), service)
    with _lock:
        _pending.setdefault(key, dict.fromkeys(FIELDS, 0))["rate_limited"] += 1


def flush(db: Database) -> None:
    with _lock:
        rows = [(h, svc, *(v[f] for f in FIELDS)) for (h, svc), v in _pending.items()]
        _pending.clear()
    if not rows:
        return
    db.many(f"""INSERT INTO service_stats(hour,service,{','.join(FIELDS)}) VALUES(?,?,{','.join('?' * len(FIELDS))})
                ON CONFLICT(hour,service) DO UPDATE SET {', '.join(f'{f}={f}+excluded.{f}' for f in FIELDS)}""", rows)
    db.run("DELETE FROM service_stats WHERE hour < ?", (_hour() - KEEP_DAYS * 24,))


def recent_errors() -> list[dict]:
    with _lock:
        return list(reversed(_recent))


class Measured:
    """A provider whose service calls are counted. Everything else passes straight through."""

    __slots__ = ("_inner",)

    def __init__(self, inner: Any):
        object.__setattr__(self, "_inner", inner)

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._inner, name)
        if name not in MEASURED or not callable(value):
            return value
        service = self._inner.service

        def call(*args, **kwargs):
            start = time.monotonic()
            try:
                out = value(*args, **kwargs)
            except BaseException as e:
                if isinstance(e, Exception):
                    record(service, name, (time.monotonic() - start) * 1000, e)
                raise
            failed = len(out) if name in ("add", "remove") and isinstance(out, list) else 0
            record(service, name, (time.monotonic() - start) * 1000, failed_items=failed)
            return out
        return call

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._inner, name, value)

    def __repr__(self) -> str:
        return f"Measured({self._inner!r})"


def measured(provider: Any) -> Any:
    return provider if isinstance(provider, Measured) else Measured(provider)
