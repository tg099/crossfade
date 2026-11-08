"""Health page: service calls are counted through the provider wrapper; admins only; no private data."""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("CROSSFADE_DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("CROSSFADE_BASE_URL", "https://music.example.test")

import time  # noqa: E402

import pytest  # noqa: E402

from crossfade import health  # noqa: E402
from crossfade.db import get_db  # noqa: E402
from crossfade.models import ApiError, AuthError  # noqa: E402
from tests.test_sharing import person  # noqa: E402


class Fake:
    service = "spotify"

    def __init__(self):
        self.credentials = {"a": 1}

    def read(self, kind, cid):
        if cid == "boom":
            raise ApiError("HTTP 500 boom", 500)
        if cid == "auth":
            raise AuthError("expired")
        return ["t1", "t2"]

    def add(self, kind, cid, tracks):
        return ["failed-1"]

    def audio_version(self, vid):
        return None

    def helper(self):
        return "not measured"


def test_wrapper_counts_calls_errors_and_failed_items():
    health.flush(get_db())
    p = health.measured(Fake())
    assert health.measured(p) is p
    assert p.read("liked", "") == ["t1", "t2"]
    assert p.add("liked", "", ["x"]) == ["failed-1"]
    with pytest.raises(ApiError):
        p.read("playlist", "boom")
    with pytest.raises(AuthError):
        p.read("playlist", "auth")
    health.rate_limited("spotify")
    # everything else passes through untouched
    assert p.helper() == "not measured" and p.credentials == {"a": 1} and hasattr(p, "audio_version")
    p.credentials = {"b": 2}
    assert p._inner.credentials == {"b": 2}
    db = get_db()
    health.flush(db)
    row = db.one("SELECT * FROM service_stats WHERE service='spotify' AND hour=?", (health._hour(),))
    assert row["calls"] >= 4 and row["errors"] >= 1 and row["auth_errors"] >= 1
    assert row["failed_items"] >= 1 and row["rate_limited"] >= 1
    errs = health.recent_errors()
    assert errs[0]["kind"] == "auth" and errs[1]["message"] == "HTTP 500 boom"


def test_health_page_is_admin_only_and_aggregated():
    db = get_db()
    c, uid = person("hl" + str(int(time.time() * 1e6) % 10**7), "H")
    assert c.get("/api/admin/health").status_code == 403
    db.run("UPDATE users SET is_admin=1 WHERE id=?", (uid,))
    health.record("apple", "search", 120)
    r = c.get("/api/admin/health")
    assert r.status_code == 200, r.text
    d = r.json()
    apple = next(s for s in d["services"] if s["service"] == "apple")
    assert apple["day"]["calls"] >= 1 and len(apple["hours"]) == 24 and apple["hours"][-1]["calls"] >= 1
    assert apple["accounts"]["connected"] >= 1
    assert d["instance"]["schema"] >= 16 and d["instance"]["database_bytes"] > 0
    assert set(d["syncs"]["fades"]) == {"total", "paused", "failing", "waiting"}
    db.run("UPDATE users SET is_admin=0 WHERE id=?", (uid,))
