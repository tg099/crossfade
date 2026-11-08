"""Is there a newer Crossfade? Asks GitHub's release list twice a day and tells the admins once per
release (in the app and on their notification channels).

Only official versions take part: a build called "dev" or anything that isn't a version number never
looks. A pre-release (1.4.0-RC.1) also hears about newer pre-releases, a release only about releases.
CROSSFADE_UPDATE_CHECK=false turns the check off (then nothing ever contacts GitHub).
"""
from __future__ import annotations

import logging
import re
import time

import httpx

from .config import DEFAULT_SOURCE, config
from .db import Database

log = logging.getLogger("crossfade.updates")

RELEASES_API = "https://api.github.com/repos/" + DEFAULT_SOURCE.removeprefix("https://github.com/") + "/releases"
EVERY = 12 * 3600
RETRY = 3600
VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?")


def parse(v: str | None) -> tuple | None:
    """A sortable key: releases after their own pre-releases, RC.2 after RC.1."""
    m = VERSION.fullmatch((v or "").strip())
    if not m:
        return None
    pre = m.group(4)
    parts = tuple((0, int(p), "") if p.isdigit() else (1, 0, p.lower()) for p in pre.split(".")) if pre else ()
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), 0 if pre else 1, parts)


def enabled() -> bool:
    return config.update_check and parse(config.version) is not None


def newest(releases: list[dict], current: str) -> dict | None:
    """The highest release this instance should hear about."""
    cur = parse(current)
    with_pre = bool(cur and cur[3] == 0)
    best, best_key = None, None
    for r in releases:
        if r.get("draft") or (r.get("prerelease") and not with_pre):
            continue
        key = parse(r.get("tag_name"))
        if key and (best_key is None or key > best_key):
            best, best_key = r, key
    return best


def state(db: Database) -> dict:
    s = db.setting("update_check") or {}
    latest = s.get("latest")
    cur, new = parse(config.version), parse(latest)
    return {"enabled": enabled(), "current": config.version, "latest": latest, "url": s.get("url"),
            "published_at": s.get("published_at"), "checked_at": s.get("checked_at"), "error": s.get("error"),
            "available": bool(enabled() and cur and new and new > cur)}


def check(db: Database, fetch=None) -> dict:
    """Ask GitHub now. `fetch` replaces the request in tests."""
    if not enabled():
        return state(db)
    old = db.setting("update_check") or {}
    try:
        releases = fetch() if fetch else _fetch()
        best = newest(releases, config.version)
        s = {"latest": best["tag_name"].lstrip("v") if best else None, "url": best.get("html_url") if best else None,
             "published_at": best.get("published_at") if best else None, "checked_at": time.time(), "error": None}
    except Exception as e:  # noqa: BLE001 – offline or rate limited: keep what we knew, ask again soon
        log.info("update check failed: %s", e)
        s = {**old, "checked_at": time.time() - EVERY + RETRY, "error": f"{type(e).__name__}: {e}"[:200]}
    db.set_setting("update_check", s)
    out = state(db)
    if out["available"]:
        from . import notify
        for uid in [r["id"] for r in db.all("SELECT id FROM users WHERE is_admin=1")]:
            notify.safely(notify.emit, db, uid, "update", "update", "update", value=out["latest"],
                          url=out["url"] or DEFAULT_SOURCE + "/releases", latest=out["latest"],
                          current=config.version)
    return out


def _fetch() -> list[dict]:
    r = httpx.get(RELEASES_API, params={"per_page": 30}, timeout=15, follow_redirects=True,
                  headers={"Accept": "application/vnd.github+json", "User-Agent": f"Crossfade/{config.version}"})
    r.raise_for_status()
    return r.json()


def due(db: Database) -> bool:
    if not enabled():
        return False
    checked = (db.setting("update_check") or {}).get("checked_at") or 0
    return checked < time.time() - EVERY
