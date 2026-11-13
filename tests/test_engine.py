"""Line engine tests with in-memory fake services. Run: .venv/bin/python -m pytest -q"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from crossfade.db import Database
from crossfade.engine import LineSync, fill_times
from crossfade.identity import Identity
from crossfade.models import APPLE, LIKED, PLAYLIST, SPOTIFY, YTM, Collection, Track

# (key, title, artist, seconds, isrc)
SONGS = [
    ("lucky", "Get Lucky", "Daft Punk", 369, "USQX91300108"),
    ("blind", "Blinding Lights", "The Weeknd", 200, "USUG11904206"),
    ("bohem", "Bohemian Rhapsody", "Queen", 355, "GBUM71029604"),
    ("levit", "Levitating", "Dua Lipa", 203, "GBAHT2000942"),
    ("hello", "Hello", "Adele", 295, "GBBKS1500214"),
    ("smell", "Smells Like Teen Spirit", "Nirvana", 301, "USGF19942501"),
    ("billi", "Billie Jean", "Michael Jackson", 294, "USSM19902991"),
    ("rolli", "Rolling in the Deep", "Adele", 228, "GBBKS1000335"),
    ("haus", "Haus am See", "Peter Fox", 215, "DEA620800624"),
    ("duhast", "Du hast", "Rammstein", 235, "DED579704010"),
]
TITLES = {
    YTM: lambda s: s[1] + (" (Remastered 2011)" if s[0] == "bohem" else ""),
    APPLE: lambda s: s[1],
    SPOTIFY: lambda s: s[1] + (" - Remastered 2011" if s[0] == "bohem" else ""),
}


@pytest.fixture(autouse=True)
def _instant_removals(monkeypatch):
    """Most tests check what a removal does, not when it counts: no confirmation window."""
    monkeypatch.setattr("crossfade.engine.GONE_CONFIRM", 0)


def track(service: str, key: str) -> Track:
    s = next(x for x in SONGS if x[0] == key)
    return Track(service=service, id=f"{service}_{key}", title=TITLES[service](s), artist=s[2],
                 duration=s[3] + (1 if service == YTM else 0), isrc=None if service == YTM else s[4])


def key_of(t: Track) -> str:
    return t.id.split("_", 1)[1]


class FakeProvider:
    def __init__(self, service: str):
        self.service = service
        self.cols: dict[tuple[str, str], dict[str, Track]] = {}
        self.calls: list[tuple] = []
        self.fail_remove: set[str] = set()
        self.lag: set[str] = set()      # ids added but hidden from the next `lag_reads` reads
        self.lag_reads = 1
        self.dates: dict[str, float] = {}   # id -> when it was added (liked), as the service says

    def put(self, kind, cid, *keys):
        col = self.cols.setdefault((kind, cid), {})
        for k in keys:
            t = track(self.service, k)
            col[t.id] = t

    def drop(self, kind, cid, *keys):
        for k in keys:
            self.cols[(kind, cid)].pop(f"{self.service}_{k}", None)

    def keys(self, kind=LIKED, cid=""):
        return sorted(key_of(t) for t in self.cols.get((kind, cid), {}).values())

    # provider API
    def read(self, kind, cid):
        col = self.cols.setdefault((kind, cid), {})
        visible = [t for t in col.values() if t.id not in self.lag]
        if self.lag:
            self.lag_reads -= 1
            if self.lag_reads <= 0:
                self.lag.clear()
                self.lag_reads = 1
        return [Track(**{**t.__dict__, "handle": {"h": t.id, **({"added_at": self.dates[t.id]} if t.id in self.dates else {})}})
                for t in visible]

    def add(self, kind, cid, tracks):
        self.calls.append(("add", kind, cid, sorted(key_of(t) for t in tracks)))
        col = self.cols.setdefault((kind, cid), {})
        for t in tracks:
            col[t.id] = track(self.service, key_of(t))
        return []

    def remove(self, kind, cid, tracks):
        self.calls.append(("remove", kind, cid, sorted(key_of(t) for t in tracks)))
        failed = []
        for t in tracks:
            assert t.handle.get("h") == t.id, "remove must receive tracks from read() with handles"
            if t.id in self.fail_remove:
                failed.append(t.id)
                continue
            self.cols[(kind, cid)].pop(t.id, None)
        return failed

    def search(self, q, limit=10):
        ql = q.lower()
        return [track(self.service, s[0]) for s in SONGS if s[1].lower() in ql]

    def by_isrc(self, isrc):
        if self.service == YTM:
            return self.search(isrc)
        return [track(self.service, s[0]) for s in SONGS if s[4] == isrc]

    def create_playlist(self, name, description=""):
        return Collection(PLAYLIST, "new", name, 0)


@pytest.fixture(autouse=True)
def _no_verify_wait(monkeypatch):
    monkeypatch.setattr("crossfade.engine.VERIFY_DELAY", 0)
    monkeypatch.setattr("crossfade.engine.RECHECK_DELAY", 0)


@pytest.fixture
def env(tmp_path: Path):
    db = Database(tmp_path / "t.db")
    uid = db.run("INSERT INTO users(username,display_name,password_hash,created_at) VALUES('t','T','x',0)")
    provs = {}
    for svc in (YTM, APPLE, SPOTIFY):
        aid = db.run("INSERT INTO accounts(user_id,service,created_at,updated_at) VALUES(?,?,0,0)", (uid, svc))
        provs[svc] = (aid, FakeProvider(svc))
    return db, uid, provs


def make_line(env, members, sync_deletions=True, guard_min=5):
    """members: (service, kind, collection_id, role[, follow_deletions])"""
    db, uid, provs = env
    lid = db.run("INSERT INTO lines(user_id,name,color,sync_deletions,delete_guard_min,created_at,updated_at) "
                 "VALUES(?,?,?,?,?,0,0)", (uid, "L", "#000", int(sync_deletions), guard_min))
    for i, (svc, kind, cid, role, *follow) in enumerate(members):
        db.run("INSERT INTO line_members(line_id,account_id,kind,collection_id,collection_name,role,follow_deletions,"
               "position) VALUES(?,?,?,?,?,?,?,?)",
               (lid, provs[svc][0], kind, cid, f"{svc}-{kind}", role, int(bool(follow and follow[0])), i))
    return lid


def sync(env, lid, apply=True, allow=False):
    db, _, provs = env
    providers = {aid: p for aid, p in provs.values()}
    return LineSync(db, lid, providers).run(apply=apply, allow_deletions=allow)


def P(env, svc) -> FakeProvider:
    return env[2][svc][1]


# ------------------------------------------------------------------------------------------
def test_two_way_first_run_merges_without_deleting(env):
    y, a = P(env, YTM), P(env, APPLE)
    y.put(LIKED, "", "lucky", "blind", "bohem")
    a.put(LIKED, "", "blind", "levit")
    lid = make_line(env, [(YTM, LIKED, "", "both"), (APPLE, LIKED, "", "both")])
    r = sync(env, lid)
    assert r.status == "ok", r.message
    assert y.keys() == a.keys() == ["blind", "bohem", "levit", "lucky"]
    assert not any(c[0] == "remove" for c in y.calls + a.calls)
    r = sync(env, lid)
    assert r.summary["add"] == r.summary["remove"] == 0 and r.summary["pending"] == 0


def test_group_of_three_adds_and_deletes_everywhere(env):
    y, a, s = P(env, YTM), P(env, APPLE), P(env, SPOTIFY)
    for p in (y, a, s):
        p.put(LIKED, "", "lucky", "blind", "hello")
    lid = make_line(env, [(YTM, LIKED, "", "both"), (APPLE, LIKED, "", "both"), (SPOTIFY, LIKED, "", "both")])
    sync(env, lid)
    s.put(LIKED, "", "haus")          # new on Spotify
    a.drop(LIKED, "", "blind")        # removed on Apple
    r = sync(env, lid)
    assert r.status == "ok", r.message
    for p in (y, a, s):
        assert p.keys() == ["haus", "hello", "lucky"], p.service
    r = sync(env, lid)
    assert r.summary["add"] == r.summary["remove"] == r.summary["pending"] == 0


def test_one_way_copies_only_from_sender_and_respects_receiver_removals(env):
    s, y = P(env, SPOTIFY), P(env, YTM)
    s.put(PLAYLIST, "gym", "lucky", "levit")
    y.put(PLAYLIST, "ygym", "duhast")           # receiver's own song stays untouched
    lid = make_line(env, [(SPOTIFY, PLAYLIST, "gym", "send"), (YTM, PLAYLIST, "ygym", "receive")])
    sync(env, lid)
    assert y.keys(PLAYLIST, "ygym") == ["duhast", "levit", "lucky"]
    assert s.keys(PLAYLIST, "gym") == ["levit", "lucky"]           # nothing flows back
    y.drop(PLAYLIST, "ygym", "levit")                               # user removes on the receiver
    s.put(PLAYLIST, "gym", "hello")
    sync(env, lid)
    assert y.keys(PLAYLIST, "ygym") == ["duhast", "hello", "lucky"]  # levit not re-added
    s.drop(PLAYLIST, "gym", "lucky")                                 # removed at the sender
    sync(env, lid)
    assert y.keys(PLAYLIST, "ygym") == ["duhast", "hello"]
    assert s.keys(PLAYLIST, "gym") == ["hello", "levit"]


def test_without_deletion_sync_nothing_is_removed_or_readded(env):
    y, a = P(env, YTM), P(env, APPLE)
    y.put(LIKED, "", "lucky", "blind")
    a.put(LIKED, "", "lucky", "blind")
    lid = make_line(env, [(YTM, LIKED, "", "both"), (APPLE, LIKED, "", "both")], sync_deletions=False)
    sync(env, lid)
    y.drop(LIKED, "", "blind")
    for _ in range(2):
        sync(env, lid)
    assert a.keys() == ["blind", "lucky"] and y.keys() == ["lucky"]


def test_mass_removal_is_held_until_confirmed(env):
    y, a = P(env, YTM), P(env, APPLE)
    keys = [s[0] for s in SONGS]
    y.put(LIKED, "", *keys)
    a.put(LIKED, "", *keys)
    lid = make_line(env, [(YTM, LIKED, "", "both"), (APPLE, LIKED, "", "both")], guard_min=2)
    sync(env, lid)
    y.drop(LIKED, "", *keys[:5])
    r = sync(env, lid)
    assert r.status == "needs_confirm" and r.summary["held"] == 5
    assert len(a.keys()) == 10 and len(y.keys()) == 5          # nothing removed, nothing re-added
    r = sync(env, lid)
    assert r.status == "needs_confirm" and len(y.keys()) == 5
    r = sync(env, lid, allow=True)
    assert r.status == "ok" and a.keys() == y.keys() and len(a.keys()) == 5



def test_mass_removal_passes_without_asking_when_switched_off(env):
    y, a = P(env, YTM), P(env, APPLE)
    keys = [s[0] for s in SONGS]
    y.put(LIKED, "", *keys)
    a.put(LIKED, "", *keys)
    lid = make_line(env, [(YTM, LIKED, "", "both"), (APPLE, LIKED, "", "both")], guard_min=2)
    env[0].run("UPDATE lines SET delete_confirm=0 WHERE id=?", (lid,))
    sync(env, lid)
    y.drop(LIKED, "", *keys[:5])
    r = sync(env, lid)
    assert r.status == "ok" and a.keys() == y.keys() and len(a.keys()) == 5


def test_empty_list_blocks(env):
    y, a = P(env, YTM), P(env, APPLE)
    y.put(LIKED, "", "lucky", "blind", "hello", "haus")
    a.put(LIKED, "", "lucky", "blind", "hello", "haus")
    lid = make_line(env, [(YTM, LIKED, "", "both"), (APPLE, LIKED, "", "both")])
    sync(env, lid)
    y.cols[(LIKED, "")].clear()
    r = sync(env, lid)
    assert r.status == "blocked" and len(a.keys()) == 4
    # the user confirms: the list really is empty
    r = sync(env, lid, allow=True)
    assert r.status == "ok" and a.keys() == []


def test_song_not_yet_listed_is_pending_not_deleted(env):
    y, a = P(env, YTM), P(env, APPLE)
    y.put(LIKED, "", "hello")
    lid = make_line(env, [(YTM, LIKED, "", "both"), (APPLE, LIKED, "", "both")])
    orig_add = a.add

    def lagging_add(kind, cid, tracks):
        res = orig_add(kind, cid, tracks)
        a.lag |= {f"{APPLE}_{key_of(t)}" for t in tracks}
        a.lag_reads = 2                    # hidden from the check right after adding and the next run
        return res
    a.add = lagging_add
    sync(env, lid)                         # added, but Apple will hide it for a while
    r = sync(env, lid)
    assert r.status == "ok" and r.summary["pending"] == 1 and y.keys() == ["hello"]
    r = sync(env, lid)                     # now visible -> confirmed
    assert r.summary["pending"] == 0 and r.summary["add"] == 0 and r.summary["remove"] == 0


def test_failed_removal_is_retried_not_readded(env):
    y, a = P(env, YTM), P(env, APPLE)
    y.put(LIKED, "", "hello", "lucky")
    a.put(LIKED, "", "hello", "lucky")
    lid = make_line(env, [(YTM, LIKED, "", "both"), (APPLE, LIKED, "", "both")])
    sync(env, lid)
    y.drop(LIKED, "", "hello")
    a.fail_remove.add(f"{APPLE}_hello")
    r = sync(env, lid)
    assert r.status == "partial" and a.keys() == ["hello", "lucky"]
    assert y.keys() == ["lucky"]                       # not re-added on YTM
    a.fail_remove.clear()
    r = sync(env, lid)
    assert r.status == "ok" and a.keys() == y.keys() == ["lucky"]


def test_preview_changes_nothing(env):
    y, a = P(env, YTM), P(env, APPLE)
    y.put(LIKED, "", "lucky", "haus")
    lid = make_line(env, [(YTM, LIKED, "", "both"), (APPLE, LIKED, "", "both")])
    r = sync(env, lid, apply=False)
    assert r.status == "preview" and r.summary["add"] == 2
    assert a.keys() == [] and not a.calls
    assert env[0].val("SELECT COUNT(*) FROM line_items") == 0


def test_interchange_two_lines_share_a_collection(env):
    y, a, s = P(env, YTM), P(env, APPLE), P(env, SPOTIFY)
    s.put(PLAYLIST, "gym", "duhast")
    l1 = make_line(env, [(SPOTIFY, PLAYLIST, "gym", "send"), (APPLE, LIKED, "", "receive")])
    l2 = make_line(env, [(APPLE, LIKED, "", "both"), (YTM, LIKED, "", "both")])
    sync(env, l1)
    sync(env, l2)
    assert y.keys() == ["duhast"] and a.keys() == ["duhast"]


def test_unmatched_song_becomes_issue(env):
    y, a = P(env, YTM), P(env, APPLE)
    y.cols[(LIKED, "")] = {"ytm_x": Track(YTM, "ytm_x", "Unknown track", "Nobody", duration=100)}
    lid = make_line(env, [(YTM, LIKED, "", "both"), (APPLE, LIKED, "", "both")])
    r = sync(env, lid)
    assert r.summary["unmatched"] == 1 and a.keys() == []
    assert env[0].val("SELECT COUNT(*) FROM issues WHERE kind='unmatched'") == 1


def _policy_line(env, policy):
    lid = make_line(env, [(YTM, LIKED, "", "send"), (APPLE, LIKED, "", "receive")])
    env[0].run("UPDATE lines SET version_policy=? WHERE id=?", (policy, lid))
    return lid


def test_exact_policy_keeps_other_versions_for_review(env):
    y, a = P(env, YTM), P(env, APPLE)
    y.put(LIKED, "", "bohem")                 # YTM: "Bohemian Rhapsody (Remastered 2011)"; Apple only has the original
    lid = _policy_line(env, "exact")
    r = sync(env, lid)
    assert a.keys() == [] and r.summary["unmatched"] == 1
    issue = env[0].one("SELECT data FROM issues WHERE kind='unmatched'")
    assert "Remaster" in issue["data"]


def test_closest_policy_adds_and_marks_the_difference(env):
    y, a = P(env, YTM), P(env, APPLE)
    y.put(LIKED, "", "bohem")
    lid = _policy_line(env, "closest")
    r = sync(env, lid)
    assert a.keys() == ["bohem"] and r.summary["approximate"] == 1
    note = env[0].val("SELECT note FROM changes WHERE action='add'")
    assert note and "Remaster" in note



def test_send_only_source_keeps_a_song_removed_elsewhere(env):
    s_, y, a = P(env, SPOTIFY), P(env, YTM), P(env, APPLE)
    s_.put(PLAYLIST, "src", "hello", "lucky")
    lid = make_line(env, [(SPOTIFY, PLAYLIST, "src", "send"), (YTM, LIKED, "", "both"), (APPLE, LIKED, "", "both")])
    sync(env, lid)
    sync(env, lid)                             # second run: the copies are now confirmed
    assert y.keys() == a.keys() == ["hello", "lucky"]
    y.drop(LIKED, "", "hello")                 # removed at a two-way stop, the source still has it
    for _ in range(3):
        r = sync(env, lid)
    assert y.keys() == ["lucky"]               # not pushed back to YTM again and again
    assert a.keys() == ["hello", "lucky"]      # nor removed elsewhere: the source still lists it
    assert r.summary["add"] == 0 and r.summary["remove"] == 0


def test_send_only_member_can_follow_deletions(env):
    """YTM only sends (nothing is added there) but songs removed elsewhere are removed there too."""
    y, a = P(env, YTM), P(env, APPLE)
    y.put(LIKED, "", "hello", "lucky")
    a.put(LIKED, "", "haus")
    lid = make_line(env, [(YTM, LIKED, "", "send", True), (APPLE, LIKED, "", "both")])
    sync(env, lid)
    sync(env, lid)
    assert y.keys() == ["hello", "lucky"]               # Apple's song does not go to YTM
    assert a.keys() == ["haus", "hello", "lucky"]
    a.drop(LIKED, "", "hello")                          # removed on Apple ...
    r = sync(env, lid)
    assert r.status == "ok", r.message
    assert y.keys() == ["lucky"]                        # ... and therefore on YTM
    y.drop(LIKED, "", "lucky")                          # its own removals still go out
    sync(env, lid)
    assert a.keys() == ["haus"]
    y.put(LIKED, "", "levit")                           # and it still sends new songs
    a.put(LIKED, "", "billi")
    r = sync(env, lid)
    assert a.keys() == ["billi", "haus", "levit"] and y.keys() == ["levit"]
    r = sync(env, lid)
    assert r.summary["add"] == r.summary["remove"] == 0


def test_following_sender_counts_towards_the_deletion_guard(env):
    y, a = P(env, YTM), P(env, APPLE)
    keys = ["lucky", "blind", "bohem", "levit", "hello", "smell"]
    y.put(LIKED, "", *keys)
    lid = make_line(env, [(YTM, LIKED, "", "send", True), (APPLE, LIKED, "", "both")], guard_min=2)
    sync(env, lid)
    sync(env, lid)
    a.drop(LIKED, "", "lucky", "blind", "bohem")
    r = sync(env, lid)
    assert r.status == "needs_confirm" and y.keys() == sorted(keys)
    r = sync(env, lid, allow=True)
    assert y.keys() == ["hello", "levit", "smell"]


def test_follow_deletions_is_ignored_without_deletion_sync(env):
    y, a = P(env, YTM), P(env, APPLE)
    y.put(LIKED, "", "hello", "lucky")
    lid = make_line(env, [(YTM, LIKED, "", "send", True), (APPLE, LIKED, "", "both")], sync_deletions=False)
    sync(env, lid)
    sync(env, lid)
    a.drop(LIKED, "", "hello")
    for _ in range(2):
        sync(env, lid)
    assert y.keys() == ["hello", "lucky"] and a.keys() == ["lucky"]


def test_song_that_never_shows_up_becomes_an_issue(env):
    y, a = P(env, YTM), P(env, APPLE)
    y.put(LIKED, "", "hello")
    lid = make_line(env, [(YTM, LIKED, "", "send"), (APPLE, LIKED, "", "receive")])
    orig_add = a.add
    a.add = lambda kind, cid, tracks: (orig_add(kind, cid, tracks), a.cols[(kind, cid)].clear(), [])[2]
    adds = 0
    for _ in range(14):
        r = sync(env, lid)
        adds += r.summary["add"]
    assert 2 <= adds <= 5                     # retried a few times, then stopped
    assert env[0].val("SELECT COUNT(*) FROM issues WHERE kind='unmatched'") == 1
    assert "several times but never shows up" in env[0].val("SELECT data FROM issues")


def test_removing_a_freshly_synced_song_is_passed_back(env):
    """YTM -> Apple, then the user removes it on Apple right away (before any further run):
    the removal must reach YouTube Music instead of the song being re-added to Apple."""
    y, a = P(env, YTM), P(env, APPLE)
    y.put(LIKED, "", "hello", "lucky")
    lid = make_line(env, [(YTM, LIKED, "", "both"), (APPLE, LIKED, "", "both")])
    r = sync(env, lid)
    assert r.status == "ok" and a.keys() == ["hello", "lucky"]
    a.drop(LIKED, "", "hello")                 # removed on Apple immediately after the sync
    r = sync(env, lid)
    assert r.status == "ok", r.message
    assert y.keys() == a.keys() == ["lucky"]
    assert not any(c[0] == "add" for c in a.calls[1:])


def test_removal_flows_both_ways(env):
    y, a = P(env, YTM), P(env, APPLE)
    y.put(LIKED, "", "hello", "lucky", "haus")
    a.put(LIKED, "", "blind")
    lid = make_line(env, [(YTM, LIKED, "", "both"), (APPLE, LIKED, "", "both")])
    sync(env, lid)
    a.drop(LIKED, "", "lucky")                 # came from YTM, removed on Apple
    y.drop(LIKED, "", "blind")                 # came from Apple, removed on YTM
    r = sync(env, lid)
    assert r.status == "ok", r.message
    assert y.keys() == a.keys() == ["haus", "hello"]


def test_sync_brings_the_library_catalog_up_to_date(env):
    """The library was scanned while Apple's favourites were empty; after the fade filled them the
    library still showed every song as missing on Apple."""
    db, _, provs = env
    src = db.run("INSERT INTO catalog_sources(account_id,kind,collection_id,name) VALUES(?,?,?,?)",
                 (provs[APPLE][0], LIKED, "", "Favourites"))
    y, a = P(env, YTM), P(env, APPLE)
    y.put(LIKED, "", "hello", "lucky")
    a.put(LIKED, "", "blind")
    lid = make_line(env, [(YTM, LIKED, "", "both"), (APPLE, LIKED, "", "both")])
    sync(env, lid)
    refs = sorted(r["ref_id"] for r in db.all("SELECT ref_id FROM catalog_items WHERE source_id=?", (src,)))
    assert refs == ["apple_blind", "apple_hello", "apple_lucky"]
    assert db.val("SELECT item_count FROM catalog_sources WHERE id=?", (src,)) == 3
    a.drop(LIKED, "", "blind")
    sync(env, lid)
    refs = sorted(r["ref_id"] for r in db.all("SELECT ref_id FROM catalog_items WHERE source_id=?", (src,)))
    assert refs == ["apple_hello", "apple_lucky"]


def test_song_missing_once_is_not_removed(env, monkeypatch):
    """Apple's favourites dropped a song we had just added for one read; the next run deleted
    the like on YouTube. A song must stay missing for GONE_CONFIRM before it counts as removed,
    and it is neither removed elsewhere nor re-added while that is unclear."""
    clock = [1000.0]
    monkeypatch.setattr("crossfade.engine.GONE_CONFIRM", 600)
    monkeypatch.setattr("crossfade.engine._now", lambda: clock[0])
    y, a = P(env, YTM), P(env, APPLE)
    y.put(LIKED, "", "hello", "lucky")
    lid = make_line(env, [(YTM, LIKED, "", "both"), (APPLE, LIKED, "", "both")])
    sync(env, lid)
    assert a.keys() == ["hello", "lucky"]
    hidden = a.cols[(LIKED, "")].pop("apple_lucky")         # a flaky read: gone for a moment
    a.calls.clear()
    clock[0] += 30
    r = sync(env, lid)
    assert r.status == "ok", r.message
    assert y.keys() == ["hello", "lucky"]                    # not removed on YouTube
    assert not [c for c in a.calls if c[0] == "add"]         # and not pushed back to Apple
    a.cols[(LIKED, "")]["apple_lucky"] = hidden              # it shows up again
    clock[0] += 900
    sync(env, lid)
    assert y.keys() == a.keys() == ["hello", "lucky"]
    assert env[0].val("SELECT COUNT(*) FROM member_items WHERE missing_since IS NOT NULL") == 0


def test_song_missing_long_enough_is_removed(env, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("crossfade.engine.GONE_CONFIRM", 600)
    monkeypatch.setattr("crossfade.engine._now", lambda: clock[0])
    y, a = P(env, YTM), P(env, APPLE)
    y.put(LIKED, "", "hello", "lucky")
    lid = make_line(env, [(YTM, LIKED, "", "both"), (APPLE, LIKED, "", "both")])
    sync(env, lid)
    a.drop(LIKED, "", "lucky")                               # the user really removed it
    clock[0] += 60
    sync(env, lid)
    assert y.keys() == ["hello", "lucky"]                    # first sighting: wait
    clock[0] += 300
    sync(env, lid)
    assert y.keys() == ["hello", "lucky"]                    # still inside the window
    clock[0] += 400
    r = sync(env, lid)
    assert r.status == "ok", r.message
    assert y.keys() == a.keys() == ["hello"]


def test_refused_searches_are_not_remembered_as_not_found(env):
    """Apple answered every search with 429 for hours; those songs must be searched again next run."""
    from crossfade.models import ApiError
    y, a = P(env, YTM), P(env, APPLE)
    y.put(LIKED, "", "bohem")
    lid = make_line(env, [(YTM, LIKED, "", "send"), (APPLE, LIKED, "", "receive")])
    real_search, real_isrc = a.search, a.by_isrc

    def blocked(*_a, **_k):
        raise ApiError("HTTP 429", 429)

    a.search = a.by_isrc = blocked
    sync(env, lid)
    assert a.keys() == []
    assert env[0].val("SELECT COUNT(*) FROM search_cache WHERE service=?", (APPLE,)) == 0
    a.search, a.by_isrc = real_search, real_isrc
    sync(env, lid)
    assert len(a.keys()) == 1


def test_results_are_reported_live_while_running(env, monkeypatch):
    monkeypatch.setattr("crossfade.engine.APPLY_CHUNK", 2)
    db, _, provs = env
    y, a = P(env, YTM), P(env, APPLE)
    y.put(LIKED, "", "lucky", "blind", "bohem")
    y.cols[(LIKED, "")]["ytm_x"] = Track(YTM, "ytm_x", "Unknown track", "Nobody", duration=100)
    lid = make_line(env, [(YTM, LIKED, "", "both"), (APPLE, LIKED, "", "both")])
    seen: list[tuple[str, dict]] = []
    providers = {aid: p for aid, p in provs.values()}

    # preview: planned additions and the unmatched song arrive one by one, nothing is applied
    LineSync(db, lid, providers, live=lambda k, d: seen.append((k, d))).run(apply=False)
    assert seen[0][0] == "start" and len(seen[0][1]["members"]) == 2
    assert sorted(key_of_info(d["track"]) for k, d in seen if k == "plan" and d["group"] == "add") == ["blind", "bohem", "lucky"]
    assert [d["track"]["title"] for k, d in seen if k == "issue"] == ["Unknown track"]
    assert not any(k == "change" for k, _ in seen)

    # apply: every change is reported as soon as its (small) batch went through
    seen.clear()
    r = LineSync(db, lid, providers, live=lambda k, d: seen.append((k, d))).run(apply=True)
    assert r.status == "ok", r.message
    changes = [d for k, d in seen if k == "change"]
    assert len(changes) == 3 and all(c["ok"] and c["action"] == "add" and c["service"] == APPLE for c in changes)
    assert [len(c[3]) for c in a.calls if c[0] == "add"] == [2, 1]
    assert db.val("SELECT COUNT(*) FROM changes WHERE line_id=?", (lid,)) == 3


def key_of_info(d: dict) -> str:
    return d["id"].split("_", 1)[1]


def test_songs_are_added_in_the_order_of_the_source(env):
    y, a = P(env, YTM), P(env, APPLE)
    ident = Identity(env[0])     # song ids in another order than the playlist's
    for k in ("hello", "smell", "blind", "bohem", "lucky", "levit"):
        ident.song_for(track(YTM, k))
    y.put(PLAYLIST, "src", "levit", "lucky", "bohem", "blind")
    lid = make_line(env, [(YTM, PLAYLIST, "src", "send"), (APPLE, PLAYLIST, "dst", "receive")])
    r = sync(env, lid)
    assert r.status == "ok", r.message
    assert [key_of(t) for t in a.cols[(PLAYLIST, "dst")].values()] == ["levit", "lucky", "bohem", "blind"]
    # songs added later at the source land behind the others, in their order
    y.put(PLAYLIST, "src", "smell", "hello")
    assert sync(env, lid).status == "ok"
    assert [key_of(t) for t in a.cols[(PLAYLIST, "dst")].values()] == [
        "levit", "lucky", "bohem", "blind", "smell", "hello"]


def test_liked_songs_go_in_oldest_first(env):
    # services list liked songs newest first: the newest like must be added last, so it shows on
    # top of liked songs elsewhere and at the end of a playlist, like songs liked later
    y, a = P(env, YTM), P(env, APPLE)
    ident = Identity(env[0])
    for k in ("hello", "smell", "blind", "bohem", "lucky", "levit"):
        ident.song_for(track(YTM, k))
    y.put(LIKED, "", "levit", "lucky", "bohem")          # levit is the newest like
    lid = make_line(env, [(YTM, LIKED, "", "send"), (APPLE, LIKED, "", "receive"), (APPLE, PLAYLIST, "p", "receive")])
    assert sync(env, lid).status == "ok"
    assert [key_of(t) for t in a.cols[(LIKED, "")].values()] == ["bohem", "lucky", "levit"]
    assert [key_of(t) for t in a.cols[(PLAYLIST, "p")].values()] == ["bohem", "lucky", "levit"]


def test_liked_songs_of_several_services_go_in_by_like_date(env):
    """Apple got every song only Spotify had as one block above the YouTube ones (it shows the
    newest favourite on top). Liked songs of all services go on one timeline, oldest first."""
    s, y, a = P(env, SPOTIFY), P(env, YTM), P(env, APPLE)
    s.put(LIKED, "", "lucky", "blind")                    # newest first
    y.put(LIKED, "", "hello", "bohem")
    s.dates = {"spotify_lucky": 50, "spotify_blind": 30}
    y.dates = {"ytm_hello": 40, "ytm_bohem": 20}
    lid = make_line(env, [(YTM, LIKED, "", "both"), (SPOTIFY, LIKED, "", "both"), (APPLE, LIKED, "", "receive")])
    assert sync(env, lid).status == "ok"
    assert [key_of(t) for t in a.cols[(LIKED, "")].values()] == ["bohem", "blind", "hello", "lucky"]


def test_a_song_without_a_date_goes_between_its_neighbours(env):
    """Apple has no dates: a favourite only Apple had sits between the songs around it there. The
    date Spotify gives the entry Crossfade made for it later is not when it was liked."""
    s, y, a = P(env, SPOTIFY), P(env, YTM), P(env, APPLE)
    a.put(LIKED, "", "blind", "hello", "bohem")           # liked on Apple, newest first
    s.put(LIKED, "", "blind", "bohem")                     # also liked on Spotify, with dates
    s.dates = {"spotify_blind": 60, "spotify_bohem": 20}
    lid = make_line(env, [(APPLE, LIKED, "", "send"), (SPOTIFY, LIKED, "", "both"), (YTM, LIKED, "", "receive")])
    assert sync(env, lid).status == "ok"
    assert [key_of(t) for t in y.cols[(LIKED, "")].values()] == ["bohem", "hello", "blind"]
    s.dates["spotify_hello"] = 100                         # Crossfade liked it on Spotify just now
    db, _, provs = env
    ls = LineSync(db, lid, {aid: p for aid, p in provs.values()})
    ls._read()
    hello = Identity(db).song_for(track(APPLE, "hello"))
    assert ls.like_times[hello] == 40


def test_fill_times_between_neighbours():
    times = {2: 40.0, 4: 20.0}
    fill_times([1, 2, 3, 4, 5], times)
    assert times == {1: 41.0, 2: 40.0, 3: 30.0, 4: 20.0, 5: 19.0}
    times = {2: 10.0, 4: 20.0}                             # list against the dates: keep its order
    fill_times([1, 2, 3, 4], times)
    assert 20 < times[3] < 21 and times[1] == 11.0


def test_removal_at_a_sender_waits_and_is_reported(env, monkeypatch):
    """Unfavouriting at a send-only Apple source shows as waiting until it is confirmed, then goes
    everywhere."""
    clock = [1000.0]
    monkeypatch.setattr("crossfade.engine.GONE_CONFIRM", 600)
    monkeypatch.setattr("crossfade.engine._now", lambda: clock[0])
    y, a = P(env, YTM), P(env, APPLE)
    a.put(LIKED, "", "hello", "lucky")
    lid = make_line(env, [(APPLE, LIKED, "", "send"), (YTM, LIKED, "", "receive")])
    sync(env, lid)
    a.drop(LIKED, "", "lucky")
    clock[0] += 60
    r = sync(env, lid)
    assert y.keys() == ["hello", "lucky"]
    assert r.summary["vanishing"] == 1 and r.summary["recheck_at"] == 1060 + 600 + 5
    clock[0] = r.summary["recheck_at"]
    r = sync(env, lid)
    assert y.keys() == ["hello"]
    assert r.summary["vanishing"] == 0 and "recheck_at" not in r.summary


def test_removal_at_spotify_goes_everywhere_in_the_same_sync(env, monkeypatch):
    """Removed on Spotify, synced right away: still on YouTube. Only Apple's lists drop songs for a
    while; elsewhere a second read a few seconds later confirms the removal within the sync."""
    monkeypatch.setattr("crossfade.engine.GONE_CONFIRM", 600)
    s, y = P(env, SPOTIFY), P(env, YTM)
    s.put(LIKED, "", "hello", "lucky")
    lid = make_line(env, [(SPOTIFY, LIKED, "", "both"), (YTM, LIKED, "", "both")])
    sync(env, lid)
    assert y.keys() == ["hello", "lucky"]
    s.drop(LIKED, "", "lucky")
    r = sync(env, lid)
    assert r.status == "ok", r.message
    assert s.keys() == y.keys() == ["hello"]
    assert r.summary["vanishing"] == 0 and "recheck_at" not in r.summary


def test_song_back_on_the_second_read_is_left_alone(env, monkeypatch):
    """A read that misses a song once must not remove it anywhere, nor add it again."""
    monkeypatch.setattr("crossfade.engine.GONE_CONFIRM", 600)
    s, y = P(env, SPOTIFY), P(env, YTM)
    s.put(LIKED, "", "hello", "lucky")
    lid = make_line(env, [(SPOTIFY, LIKED, "", "both"), (YTM, LIKED, "", "both")])
    sync(env, lid)
    s.calls.clear()
    s.lag, s.lag_reads = {"spotify_lucky"}, 1         # hidden from the first read only
    r = sync(env, lid)
    assert r.status == "ok", r.message
    assert s.keys() == y.keys() == ["hello", "lucky"]
    assert s.calls == [] and r.summary["remove"] == 0
    assert env[0].val("SELECT COUNT(*) FROM member_items WHERE missing_since IS NOT NULL") == 0


def test_unreadable_second_read_falls_back_to_waiting(env, monkeypatch):
    from crossfade.models import ApiError
    monkeypatch.setattr("crossfade.engine.GONE_CONFIRM", 600)
    s, y = P(env, SPOTIFY), P(env, YTM)
    s.put(LIKED, "", "hello", "lucky")
    lid = make_line(env, [(SPOTIFY, LIKED, "", "both"), (YTM, LIKED, "", "both")])
    sync(env, lid)
    s.drop(LIKED, "", "lucky")
    real, reads = s.read, [0]

    def flaky(kind, cid):
        reads[0] += 1
        if reads[0] == 2:
            raise ApiError("HTTP 500", 500)
        return real(kind, cid)

    s.read = flaky
    r = sync(env, lid)
    assert r.status == "ok", r.message
    assert y.keys() == ["hello", "lucky"] and r.summary["vanishing"] == 1


def test_scheduler_rechecks_a_waiting_removal_whatever_the_interval(env, monkeypatch):
    from crossfade import worker as workers
    db = env[0]
    db.run("UPDATE accounts SET credentials='x'")
    lid = make_line(env, [(YTM, LIKED, "", "send"), (APPLE, LIKED, "", "receive")])
    db.run("UPDATE lines SET interval_minutes=0, last_run_at=?, last_summary=? WHERE id=?",
           (time.time(), json.dumps({"vanishing": 1, "recheck_at": time.time() + 300}), lid))
    w = workers.Worker(db)
    started = []
    monkeypatch.setattr(w, "start", lambda line_id, trigger="manual", **kw: started.append((line_id, trigger)))
    for name in ("_check_accounts", "_housekeeping"):
        monkeypatch.setattr(w, name, lambda: None)
    monkeypatch.setattr(workers.health, "flush", lambda db: None)
    w._tick()
    assert started == []                                  # not yet
    db.run("UPDATE lines SET last_summary=? WHERE id=?", (json.dumps({"vanishing": 1, "recheck_at": time.time() - 1}), lid))
    w._tick()
    assert started == [(lid, "schedule")]
    w.pool.shutdown(wait=False)


# ------------------------------------------------------------------------------------------ reorder
def liked_on_top(p: FakeProvider) -> None:
    """Like a real service: liking a song puts it on top of liked songs."""
    real = p.add

    def add(kind, cid, tracks):
        failed = real(kind, cid, tracks)
        if kind == LIKED:
            col = p.cols[(kind, cid)]
            fresh = [track(p.service, key_of(t)).id for t in tracks]
            p.cols[(kind, cid)] = {**{i: col.pop(i) for i in reversed(fresh) if i in col}, **col}
        return failed

    p.add = add


def reorder_env(env, monkeypatch):
    monkeypatch.setattr("crossfade.engine.time.sleep", lambda s: None)
    s, y, a = P(env, SPOTIFY), P(env, YTM), P(env, APPLE)
    s.put(LIKED, "", "lucky", "blind")
    s.dates = {"spotify_lucky": 50, "spotify_blind": 30}
    y.put(LIKED, "", "hello", "bohem")
    y.dates = {"ytm_hello": 40, "ytm_bohem": 20}
    a.put(LIKED, "", "lucky", "blind", "hello", "bohem")     # Spotify's block above YouTube's
    liked_on_top(a)
    lid = make_line(env, [(YTM, LIKED, "", "both"), (SPOTIFY, LIKED, "", "both"), (APPLE, LIKED, "", "both")])
    assert sync(env, lid).status == "ok"
    apple = env[0].val("SELECT m.id FROM line_members m JOIN accounts a ON a.id=m.account_id "
                       "WHERE m.line_id=? AND a.service=?", (lid, APPLE))
    return lid, apple, a


def reorder(env, lid, apply, ids):
    db, _, provs = env
    return LineSync(db, lid, {aid: p for aid, p in provs.values()}).reorder(apply, ids)


def test_reorder_likes_by_like_date_moving_as_few_as_possible(env, monkeypatch):
    lid, apple, a = reorder_env(env, monkeypatch)
    r = reorder(env, lid, False, {apple})
    assert r.status == "preview" and r.summary["reorder"] == [
        {"member_id": apple, "service": APPLE, "name": "apple-liked", "count": 4, "moves": 2}]
    assert a.keys() == ["blind", "bohem", "hello", "lucky"] and not [c for c in a.calls if c[0] == "remove"]
    a.calls.clear()
    r = reorder(env, lid, True, {apple})
    assert r.status == "ok", r.message
    assert [key_of(t) for t in a.cols[(LIKED, "")].values()] == ["lucky", "hello", "blind", "bohem"]
    assert [c for c in a.calls if c[0] == "remove"] == [("remove", LIKED, "", ["hello"]), ("remove", LIKED, "", ["lucky"])]
    assert r.summary["moved"] == 2
    # in order now: nothing left to do, and the next sync sees nothing removed
    assert reorder(env, lid, False, {apple}).summary["reorder"][0]["moves"] == 0
    r = sync(env, lid)
    assert r.status == "ok" and r.summary["remove"] == 0 and r.summary["add"] == 0


def test_reorder_never_loses_a_song(env, monkeypatch):
    """Unliked but not liked again (the service refused): the run stops, nothing is removed
    elsewhere, and the next sync adds it again."""
    lid, apple, a = reorder_env(env, monkeypatch)
    working = a.add
    a.add = lambda kind, cid, tracks: [t.id for t in tracks]
    r = reorder(env, lid, True, {apple})
    assert r.status == "error" and r.summary["lost"] == 1 and r.summary["moved"] == 0
    assert "hello" not in a.keys()
    a.add = working
    r = sync(env, lid)
    assert r.status == "ok", r.message
    assert "hello" in a.keys() and P(env, YTM).keys() == ["blind", "bohem", "hello", "lucky"]


def test_like_dates_are_kept_when_a_service_dates_a_like_anew(env):
    """Liking a song again (putting likes in order does that) dates it anew at the service. The
    order must not flip because of that: the first date stays. A song that left the fade and is
    liked again later is a new like."""
    s = P(env, SPOTIFY)
    s.put(LIKED, "", "lucky", "blind")
    s.dates = {"spotify_lucky": 50, "spotify_blind": 30}
    lid = make_line(env, [(SPOTIFY, LIKED, "", "both"), (YTM, LIKED, "", "receive")])
    assert sync(env, lid).status == "ok"
    s.dates["spotify_blind"] = 1000
    db, _, provs = env
    ls = LineSync(db, lid, {aid: p for aid, p in provs.values()})
    ls._read()
    blind = Identity(db).song_for(track(SPOTIFY, "blind"))
    assert ls.like_times[blind] == 30
    s.drop(LIKED, "", "blind")
    assert sync(env, lid).status == "ok"
    assert db.val("SELECT COUNT(*) FROM like_times WHERE song_id=?", (blind,)) == 0
