"""Time travel: snapshots only on change, thinned over time, private, and restoring brings exactly
the old entries back."""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("CROSSFADE_DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("CROSSFADE_BASE_URL", "https://music.example.test")

import time  # noqa: E402

from crossfade import history  # noqa: E402
from crossfade.db import get_db  # noqa: E402
from crossfade.models import Track  # noqa: E402
from tests.test_sharing import H, person  # noqa: E402


def _sfx() -> str:
    return str(int(time.time() * 1e6) % 10**7)


def _account(uid: int, service: str = "ytm") -> int:
    return get_db().val("SELECT id FROM accounts WHERE user_id=? AND service=?", (uid, service))


def _refs(db, service, *ids):
    for i in ids:
        sid = db.run("INSERT INTO songs(title,artist,created_at) VALUES(?,?,?)", (f"Song {i}", "Band", time.time()))
        db.run("INSERT OR IGNORE INTO song_refs(service,ref_id,song_id,title,artist) VALUES(?,?,?,?,?)",
               (service, i, sid, f"Song {i}", "Band"))


def test_only_changes_are_kept():
    db = get_db()
    _, uid = person("h1" + _sfx(), "H")
    acc = _account(uid)
    assert history.capture(db, acc, "playlist", "PL", ["a", "b", "b"], "sync") is not None
    first = db.one("SELECT * FROM snapshots WHERE account_id=?", (acc,))
    assert first["count"] == 2 and history.unpack(first["items"]) == ["a", "b"]
    # same content in another order: no new snapshot, only "still like this"
    assert history.capture(db, acc, "playlist", "PL", ["b", "a"], "sync", now=time.time() + 60) is None
    assert db.val("SELECT COUNT(*) FROM snapshots WHERE account_id=?", (acc,)) == 1
    assert db.val("SELECT last_seen FROM snapshots WHERE account_id=?", (acc,)) > first["last_seen"]
    assert history.capture(db, acc, "playlist", "PL", ["a"], "sync") is not None
    tl = history.timeline(db, acc, "playlist", "PL")
    assert [(s["count"], s["added"], s["removed"]) for s in tl] == [(1, 0, 1), (2, None, None)]


def test_old_snapshots_are_thinned():
    db = get_db()
    _, uid = person("h2" + _sfx(), "H")
    acc = _account(uid)
    now = time.time()
    day = 86400
    # three per day, 100 days back
    for d in range(100, 0, -1):
        for h in range(3):
            history.capture(db, acc, "liked", "", [f"x{d}-{h}"], "sync", now=now - d * day + h * 3600)
    rows = db.all("SELECT taken_at FROM snapshots WHERE account_id=? ORDER BY taken_at", (acc,))
    ages = [(now - r["taken_at"]) / day for r in rows]
    assert max(ages) <= history.KEEP_DAYS + 1
    old = [a for a in ages if a > history.KEEP_ALL_DAYS + 1]
    assert len(old) <= history.KEEP_DAYS - history.KEEP_ALL_DAYS + 1     # one per day
    assert len([a for a in ages if a < history.KEEP_ALL_DAYS - 1]) >= 3 * (history.KEEP_ALL_DAYS - 2)


class FakeProvider:
    service = "ytm"

    def __init__(self, items):
        self.items = list(items)
        self.added, self.removed = [], []

    def read(self, kind, cid):
        return [Track(service="ytm", id=i, title=i) for i in self.items]

    def add(self, kind, cid, tracks):
        bad = [t.id for t in tracks if t.id == "broken"]
        for t in tracks:
            if t.id not in bad:
                self.items.append(t.id)
                self.added.append(t.id)
        return bad

    def remove(self, kind, cid, tracks):
        for t in tracks:
            self.items.remove(t.id)
            self.removed.append(t.id)
        return []


def _noop(*a):
    pass


def test_restore_missing_and_exact():
    db = get_db()
    _, uid = person("h3" + _sfx(), "H")
    acc = _account(uid)
    _refs(db, "ytm", "r1", "r2", "r3", "r4")
    history.capture(db, acc, "playlist", "PLx", ["r1", "r2", "r3"], "sync", now=time.time() - 3600)
    old = db.val("SELECT id FROM snapshots WHERE account_id=?", (acc,))
    # meanwhile r2 and r3 were deleted and r4 came in
    prov = FakeProvider(["r1", "r4"])
    res = history.restore(db, uid, {acc: prov}, old, "missing", None, _noop, lambda: False)
    assert prov.added == ["r2", "r3"] and prov.removed == [] and res["added"] == 2
    # exact: r4 goes too
    prov = FakeProvider(["r1", "r4"])
    res = history.restore(db, uid, {acc: prov}, old, "exact", None, _noop, lambda: False)
    assert prov.removed == ["r4"] and sorted(prov.items) == ["r1", "r2", "r3"] and res["removed"] == 1
    # just some of them
    prov = FakeProvider(["r1"])
    history.restore(db, uid, {acc: prov}, old, "missing", ["r3"], _noop, lambda: False)
    assert prov.added == ["r3"]
    # the state before and after a restore are snapshots of their own
    assert db.val("SELECT COUNT(*) FROM snapshots WHERE account_id=? AND origin='restore'", (acc,)) >= 1


def test_restore_reports_failures():
    db = get_db()
    _, uid = person("h4" + _sfx(), "H")
    acc = _account(uid)
    history.capture(db, acc, "liked", "", ["ok1", "broken"], "sync", now=time.time() - 60)
    snap = db.val("SELECT id FROM snapshots WHERE account_id=?", (acc,))
    prov = FakeProvider([])
    res = history.restore(db, uid, {acc: prov}, snap, "missing", None, _noop, lambda: False)
    assert res["added"] == 1 and res["failed"] == 1


def test_api_is_private_and_compares():
    db = get_db()
    sfx = _sfx()
    a, ua = person("ha" + sfx, "A")
    b, _ = person("hb" + sfx, "B")
    acc = _account(ua, "apple")
    _refs(db, "apple", "s1", "s2", "s3")
    history.capture(db, acc, "playlist", "p.1", ["s1", "s2"], "scan", now=time.time() - 7200)
    history.capture(db, acc, "playlist", "p.1", ["s1", "s3"], "sync")
    cols = a.get("/api/history").json()
    mine = [c for c in cols if c["account_id"] == acc]
    assert mine and mine[0]["snapshots"] == 2 and mine[0]["count"] == 2
    assert b.get("/api/history").json() == []
    tl = a.get("/api/history/timeline", params={"account_id": acc, "kind": "playlist", "collection_id": "p.1"}).json()
    old = tl["snapshots"][-1]["id"]
    assert b.get("/api/history/timeline", params={"account_id": acc, "kind": "playlist",
                                                 "collection_id": "p.1"}).status_code == 404
    d = a.get(f"/api/history/snapshots/{old}").json()
    assert [s["ref"] for s in d["missing"]] == ["s2"] and [s["ref"] for s in d["extra"]] == ["s3"]
    assert d["missing"][0]["title"] == "Song s2"
    assert b.get(f"/api/history/snapshots/{old}").status_code == 404
    assert b.post(f"/api/history/snapshots/{old}/restore", json={"mode": "missing"}, headers=H).status_code == 404
