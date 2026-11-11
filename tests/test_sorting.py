"""Sorting inside a fade: rule collections, waiting for labels, and letting sorted songs go."""
from __future__ import annotations

import json
import time

import pytest
from test_engine import FakeProvider, P, sync

from crossfade.db import Database
from crossfade.models import APPLE, LIKED, PLAYLIST, SPOTIFY, YTM
from crossfade.sorting import Rule, Sorter, fits


@pytest.fixture
def env(tmp_path):
    db = Database(tmp_path / "t.db")
    uid = db.run("INSERT INTO users(username,display_name,password_hash,created_at) VALUES('t','T','x',0)")
    provs = {}
    for svc in (YTM, APPLE, SPOTIFY):
        aid = db.run("INSERT INTO accounts(user_id,service,created_at,updated_at) VALUES(?,?,0,0)", (uid, svc))
        provs[svc] = (aid, FakeProvider(svc))
    return db, uid, provs


def make_fade(env, members, guard_min=50, sync_deletions=True):
    """members: (service, kind, collection_id, role, rule | None, move_sorted)"""
    db, uid, provs = env
    lid = db.run("INSERT INTO lines(user_id,name,color,sync_deletions,delete_guard_min,created_at,updated_at) "
                 "VALUES(?,?,?,?,?,0,0)", (uid, "Sort", "#000", int(sync_deletions), guard_min))
    ids = []
    for i, (svc, kind, cid, role, rule, move) in enumerate(members):
        ids.append(db.run(
            "INSERT INTO line_members(line_id,account_id,kind,collection_id,collection_name,role,position,rule,"
            "move_sorted) VALUES(?,?,?,?,?,?,?,?,?)",
            (lid, provs[svc][0], kind, cid, cid or f"{svc} likes", role, i,
             rule if isinstance(rule, str) or rule is None else json.dumps(rule), int(move))))
    return lid, ids


def label(env, title, **data):
    db, uid, _ = env
    sid = db.val("SELECT id FROM songs WHERE title=?", (title,))
    db.run("INSERT INTO music_annotations(user_id,song_id,data,origin,updated_at) VALUES(?,?,?,'assistant',?) "
           "ON CONFLICT(user_id,song_id) DO UPDATE SET data=excluded.data",
           (uid, sid, json.dumps({"moods": [], "genres": [], "tags": [], **data, "confidence": 0.9,
                                  "evidence": "test"}), time.time()))


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr("crossfade.engine.GONE_CONFIRM", 0)
    monkeypatch.setattr("crossfade.engine.VERIFY_DELAY", 0)


# ------------------------------------------------------------------------------------------ rules
def test_rule_conditions_combine_and_unknown_labels_stay_open():
    rule = Rule(moods=["Relaxed", "calm "], artists=["Adele"])
    assert rule.moods == ["relaxed", "calm"] and rule.needs_labels
    assert fits(rule, "Adele", None) is None                       # the artist fits, the mood is unknown
    assert fits(rule, "Queen", None) is False                      # decided without labels
    assert fits(rule, "Adele, Someone", {"moods": ["Calm"]}) is True
    assert fits(rule, "Adele", {"moods": ["happy"]}) is False
    assert fits(Rule(artists=["Simon & Garfunkel"]), "Simon & Garfunkel", None) is True
    assert fits(Rule(energy=["high"], languages=["German"]), "X", {"energy": "high", "language": "german"}) is True
    for bad in ({}, {"rest": True, "moods": ["x"]}, {"moods": [""]}, {"unknown": ["x"]}):
        with pytest.raises(ValueError):
            Rule.model_validate(bad)


def test_unreadable_rule_sorts_nothing(env):
    s, y = P(env, SPOTIFY), P(env, YTM)
    s.put(LIKED, "", "hello", "lucky")
    lid, _ = make_fade(env, [(SPOTIFY, LIKED, "", "send", None, False),
                             (YTM, PLAYLIST, "broken", "receive", '{"moods": 5}', False)])
    assert sync(env, lid).status == "ok"
    assert y.keys(PLAYLIST, "broken") == []


# ------------------------------------------------------------------------------------------ sorting
def test_rule_collections_get_only_fitting_songs_and_unlabelled_songs_wait(env):
    s, y = P(env, SPOTIFY), P(env, YTM)
    s.put(LIKED, "", "lucky", "hello", "rolli", "haus")
    lid, _ = make_fade(env, [(SPOTIFY, LIKED, "", "send", None, False),
                             (YTM, PLAYLIST, "chill", "receive", {"moods": ["relaxed"]}, False),
                             (YTM, PLAYLIST, "adele", "receive", {"artists": ["Adele"]}, False),
                             (YTM, PLAYLIST, "rest", "receive", {"rest": True}, False)])
    r = sync(env, lid)
    assert r.status == "ok", r.message
    assert y.keys(PLAYLIST, "adele") == ["hello", "rolli"]          # artists need no labels
    assert y.keys(PLAYLIST, "chill") == y.keys(PLAYLIST, "rest") == []
    assert r.summary["unsorted"] == 4                                # nobody knows their mood yet
    label(env, "Haus am See", moods=["relaxed"])
    label(env, "Hello", moods=["relaxed", "melancholic"])
    label(env, "Get Lucky", moods=["energetic"])
    r = sync(env, lid)
    assert y.keys(PLAYLIST, "chill") == ["haus", "hello"]
    assert y.keys(PLAYLIST, "rest") == ["lucky"]                     # labelled, fits nothing else
    assert r.summary["unsorted"] == 1                                # Rolling in the Deep: no mood yet
    assert s.keys() == ["haus", "hello", "lucky", "rolli"]           # kept in likes
    r = sync(env, lid)
    assert r.summary["add"] == r.summary["remove"] == 0


def test_sorted_songs_leave_the_inbox_only_once_they_arrived(env):
    y, a = P(env, YTM), P(env, APPLE)
    y.put(LIKED, "", "hello", "rolli", "lucky")
    lid, _ = make_fade(env, [(YTM, LIKED, "", "send", None, True),
                             (APPLE, PLAYLIST, "adele", "receive", {"artists": ["Adele"]}, False)])
    r = sync(env, lid)
    assert a.keys(PLAYLIST, "adele") == ["hello", "rolli"]
    assert y.keys() == ["hello", "lucky", "rolli"]                   # not before they are listed there
    r = sync(env, lid)
    assert r.status == "ok" and r.summary["sorted_out"] == 2
    assert y.keys() == ["lucky"]                                     # unsorted stays in the inbox
    note = env[0].val("SELECT note FROM changes WHERE action='remove' LIMIT 1")
    assert note == "sorted into adele"
    for _ in range(2):                                               # leaving likes is no deletion
        r = sync(env, lid)
        assert r.summary["remove"] == r.summary["add"] == 0
    assert a.keys(PLAYLIST, "adele") == ["hello", "rolli"]
    y.put(LIKED, "", "hello")                                        # liked again: sorted again
    sync(env, lid)
    assert y.keys() == ["lucky"] and a.keys(PLAYLIST, "adele") == ["hello", "rolli"]


def test_many_hand_offs_wait_for_confirmation(env):
    y, a = P(env, YTM), P(env, APPLE)
    y.put(LIKED, "", "hello", "rolli", "lucky")
    lid, _ = make_fade(env, [(YTM, LIKED, "", "send", None, True),
                             (APPLE, PLAYLIST, "all", "receive", {"rest": True}, False)], guard_min=1)
    sync(env, lid)
    r = sync(env, lid)
    assert r.status == "needs_confirm" and r.summary["held"] == 3 and len(y.keys()) == 3
    r = sync(env, lid, allow=True)
    assert r.status == "ok" and y.keys() == [] and a.keys(PLAYLIST, "all") == ["hello", "lucky", "rolli"]


def test_hand_off_waits_for_every_collection_the_song_fits(env, monkeypatch):
    y, a, s = P(env, YTM), P(env, APPLE), P(env, SPOTIFY)
    y.put(LIKED, "", "hello")
    monkeypatch.setattr(s, "search", lambda q, limit=10: [])        # Spotify can't find it
    monkeypatch.setattr(s, "by_isrc", lambda isrc: [])
    lid, ids = make_fade(env, [(YTM, LIKED, "", "send", None, True),
                               (APPLE, PLAYLIST, "adele", "receive", {"artists": ["Adele"]}, False),
                               (SPOTIFY, PLAYLIST, "sad", "receive", {"artists": ["Adele"]}, False)])
    sync(env, lid)
    sync(env, lid)
    assert y.keys() == ["hello"]                                     # Spotify still lacks it
    issue = env[0].one("SELECT * FROM issues WHERE line_id=? AND kind='unmatched'", (lid,))
    assert issue and issue["member_id"] == ids[2]
    env[0].run("UPDATE issues SET kind='ignored' WHERE id=?", (issue["id"],))
    r = sync(env, lid)
    assert r.summary["sorted_out"] == 1 and y.keys() == [] and a.keys(PLAYLIST, "adele") == ["hello"]


def test_two_way_inbox_does_not_get_its_sorted_songs_back(env):
    y, a = P(env, YTM), P(env, APPLE)
    y.put(LIKED, "", "hello", "lucky")
    lid, _ = make_fade(env, [(YTM, LIKED, "", "both", None, True),
                             (APPLE, PLAYLIST, "adele", "receive", {"artists": ["Adele"]}, False)])
    sync(env, lid)
    sync(env, lid)
    assert y.keys() == ["lucky"]
    for _ in range(2):
        r = sync(env, lid)
        assert r.summary["add"] == 0 and y.keys() == ["lucky"]
    assert a.keys(PLAYLIST, "adele") == ["hello"]


def test_preview_shows_what_would_be_sorted_without_changing_anything(env):
    y, a = P(env, YTM), P(env, APPLE)
    y.put(LIKED, "", "hello", "lucky")
    lid, ids = make_fade(env, [(YTM, LIKED, "", "send", None, True),
                               (APPLE, PLAYLIST, "adele", "receive", {"artists": ["Adele"]}, False)])
    r = sync(env, lid, apply=False)
    assert r.status == "preview" and a.keys(PLAYLIST, "adele") == []
    plan = {m["member_id"]: m for m in r.plan["members"]}
    assert [x["title"] for x in plan[ids[1]]["add"]] == ["Hello"]


def test_sorter_counts_songs_waiting_for_labels(env):
    db, uid, provs = env
    s = P(env, SPOTIFY)
    s.put(LIKED, "", "hello", "lucky")
    lid, ids = make_fade(env, [(SPOTIFY, LIKED, "", "send", None, False),
                               (YTM, PLAYLIST, "chill", "receive", {"moods": ["relaxed"]}, False)])
    sync(env, lid)
    rows = db.all("SELECT m.*, a.user_id FROM line_members m JOIN accounts a ON a.id=m.account_id WHERE line_id=?",
                  (lid,))
    songs = [r["song_id"] for r in db.all("SELECT song_id FROM line_items WHERE line_id=?", (lid,))]
    assert len(Sorter(db, rows).waiting(songs)) == 2
    label(env, "Hello", moods=["sad"])
    assert len(Sorter(db, rows).waiting(songs)) == 1
