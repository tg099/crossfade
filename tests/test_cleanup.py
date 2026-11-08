"""Tidying the library: duplicates, copies, unusual versions, playlists, video or song on YouTube."""
from __future__ import annotations

from pathlib import Path

import pytest
from test_engine import FakeProvider, track

from crossfade.cleanup import Cleanup, findings
from crossfade.db import Database
from crossfade.engine import LineSync
from crossfade.models import APPLE, LIKED, PLAYLIST, SPOTIFY, YTM, Collection, Track
from crossfade.providers.base import Provider, unique


@pytest.fixture(autouse=True)
def _instant(monkeypatch):
    """A removal counts at once – otherwise the fade tests here would pass by waiting."""
    monkeypatch.setattr("crossfade.engine.GONE_CONFIRM", 0)
    monkeypatch.setattr("crossfade.engine.VERIFY_DELAY", 0)


class Fake(FakeProvider):
    """Fake service that can list a track twice, hold any track and knows YouTube counterparts."""

    remove_copies = Provider.remove_copies

    def __init__(self, service):
        super().__init__(service)
        self.copies: dict[tuple[str, str], dict[str, int]] = {}
        self.counterparts: dict[str, Track] = {}
        self.deleted: list[str] = []

    def collections(self):
        out = [Collection(LIKED, "", "Likes")]
        out += [Collection(PLAYLIST, cid, f"PL {cid}") for (kind, cid) in self.cols if kind == PLAYLIST]
        return out

    def put_track(self, kind, cid, t: Track, copies: int = 1):
        self.cols.setdefault((kind, cid), {})[t.id] = t
        if copies > 1:
            self.copies.setdefault((kind, cid), {})[t.id] = copies

    def read(self, kind, cid):
        col = self.cols.setdefault((kind, cid), {})
        out = []
        for t in col.values():
            for _ in range(self.copies.get((kind, cid), {}).get(t.id, 1)):
                out.append(Track(**{**t.__dict__, "handle": {"h": t.id}}))
        return unique(out)

    def add(self, kind, cid, tracks):
        self.calls.append(("add", kind, cid, sorted(t.id for t in tracks)))
        col = self.cols.setdefault((kind, cid), {})
        for t in tracks:
            col[t.id] = Track(**{**t.__dict__, "handle": {}})
        return []

    def remove(self, kind, cid, tracks):
        self.calls.append(("remove", kind, cid, sorted(t.id for t in tracks)))
        failed = []
        for t in tracks:
            assert t.handle.get("h") == t.id, "remove must receive tracks from read() with handles"
            if t.id in self.fail_remove:
                failed.append(t.id)
                continue
            self.cols[(kind, cid)].pop(t.id, None)
            self.copies.get((kind, cid), {}).pop(t.id, None)
        return failed

    def counterpart(self, vid):
        return self.counterparts.get(vid)

    def delete_playlist(self, cid):
        self.deleted.append(cid)
        self.cols.pop((PLAYLIST, cid), None)

    def ids(self, kind=LIKED, cid=""):
        return sorted(self.cols.get((kind, cid), {}))


@pytest.fixture
def env(tmp_path: Path):
    db = Database(tmp_path / "c.db")
    uid = db.run("INSERT INTO users(username,display_name,password_hash,created_at) VALUES('t','T','x',0)")
    provs = {}
    for svc in (YTM, APPLE, SPOTIFY):
        aid = db.run("INSERT INTO accounts(user_id,service,credentials,created_at,updated_at) VALUES(?,?,'x',0,0)",
                     (uid, svc))
        provs[svc] = (aid, Fake(svc))
    return db, uid, provs


def C(env) -> Cleanup:
    db, uid, provs = env
    return Cleanup(db, uid, {aid: p for aid, p in provs.values()})


def P(env, svc) -> Fake:
    return env[2][svc][1]


def T(service, id_, title, artist="Queen", duration=355, **kw) -> Track:
    return Track(service=service, id=id_, title=title, artist=artist, duration=duration, **kw)


def source(db, svc, kind=LIKED, cid=""):
    return db.val("SELECT s.id FROM catalog_sources s JOIN accounts a ON a.id=s.account_id "
                  "WHERE a.service=? AND s.kind=? AND s.collection_id=?", (svc, kind, cid))


# ------------------------------------------------------------------------------ duplicates
def test_remaster_and_original_are_offered_as_one_song(env):
    db, uid, _ = env
    P(env, SPOTIFY).put_track(LIKED, "", T(SPOTIFY, "s1", "Bohemian Rhapsody", isrc="GBUM71029604"))
    P(env, SPOTIFY).put_track(LIKED, "", T(SPOTIFY, "s2", "Bohemian Rhapsody - Remastered 2011", duration=354,
                                           isrc="GBUM71029605"))
    C(env).scan()
    f = findings(db, uid)
    (g,) = f["duplicates"]
    assert g["confidence"] == "likely" and len(g["songs"]) == 2
    other = next(v["id"] for v in g["variants"] if v["id"] != g["keep"])
    assert g["plans"][g["keep"]] == [[source(db, SPOTIFY), other.split(":")[1]]]


def test_live_version_is_not_a_duplicate(env):
    db, uid, _ = env
    P(env, SPOTIFY).put_track(LIKED, "", T(SPOTIFY, "s1", "Bohemian Rhapsody"))
    P(env, SPOTIFY).put_track(LIKED, "", T(SPOTIFY, "s2", "Bohemian Rhapsody - Live Aid", duration=360))
    C(env).scan()
    assert findings(db, uid)["duplicates"] == []


def test_episodes_named_after_a_bar_are_not_duplicates(env):
    """Uploads like "Letztes JBB | Finale #1" share the part before the bar but are different videos."""
    db, uid, _ = env
    yt = P(env, YTM)
    for i, (ep, d) in enumerate((("8tel-Finale #3 | RAPIDO vs. NAIRO", 407), ("Halbfinale #2 | DEAL vs. Gary", 350))):
        yt.put_track(LIKED, "", T(YTM, f"v{i}", f"Letztes JBB | {ep}", "JuliensBlogBattle", d,
                                  video_type="MUSIC_VIDEO_TYPE_UGC"))
    C(env).scan()
    assert findings(db, uid)["duplicates"] == []


def test_dedupe_keeps_the_song_in_every_collection_so_fades_delete_nothing(env):
    """The key promise: removing a duplicate never reaches other services through a fade."""
    db, uid, provs = env
    sp, ap = P(env, SPOTIFY), P(env, APPLE)
    orig = T(SPOTIFY, "s1", "Bohemian Rhapsody", isrc="GBUM71029604")
    rem = T(SPOTIFY, "s2", "Bohemian Rhapsody - Remastered 2011", duration=354, isrc="GBUM71029605")
    sp.put_track(LIKED, "", orig)
    sp.put_track(LIKED, "", rem)
    ap.put_track(LIKED, "", T(APPLE, "a2", "Bohemian Rhapsody (Remastered 2011)", duration=354, isrc="GBUM71029605"))
    lid = db.run("INSERT INTO lines(user_id,name,color,sync_deletions,created_at,updated_at) VALUES(?,?,?,1,0,0)",
                 (uid, "L", "#000"))
    for i, svc in enumerate((SPOTIFY, APPLE)):
        db.run("INSERT INTO line_members(line_id,account_id,kind,collection_id,collection_name,role,position) "
               "VALUES(?,?,?,?,?,?,?)", (lid, provs[svc][0], LIKED, "", svc, "both", i))
    providers = {aid: p for aid, p in provs.values()}
    LineSync(db, lid, providers).run(apply=True)
    LineSync(db, lid, providers).run(apply=True)
    c = C(env)
    c.scan()
    (g,) = findings(db, uid)["duplicates"]
    res = c.dedupe([{"songs": g["songs"], "keep": ["spotify", "s1"]}], [])
    assert res["removed"] >= 1 and not res["errors"]
    assert sp.ids() == ["s1"]
    apple_before = ap.ids()
    assert apple_before                      # Apple keeps its entry (now the same song)
    r = LineSync(db, lid, providers).run(apply=True)
    assert r.status == "ok"
    assert ap.ids() == apple_before and sp.ids() == ["s1"]
    assert findings(db, uid)["duplicates"] == []


def test_dedupe_skips_when_the_kept_entry_is_gone_meanwhile(env):
    db, uid, _ = env
    sp = P(env, SPOTIFY)
    sp.put_track(LIKED, "", T(SPOTIFY, "s1", "Bohemian Rhapsody"))
    sp.put_track(LIKED, "", T(SPOTIFY, "s2", "Bohemian Rhapsody - Remastered 2011", duration=354))
    c = C(env)
    c.scan()
    (g,) = findings(db, uid)["duplicates"]
    sp.cols[(LIKED, "")].pop("s1")            # removed in the service's own app meanwhile
    res = c.dedupe([{"songs": g["songs"], "keep": ["spotify", "s1"]}], [])
    assert res["removed"] == 0 and res["errors"]
    assert sp.ids() == ["s2"]                 # the last entry of the song is never removed


def test_undo_adds_removed_entries_back(env):
    db, uid, _ = env
    sp = P(env, SPOTIFY)
    sp.put_track(LIKED, "", T(SPOTIFY, "s1", "Bohemian Rhapsody"))
    sp.put_track(LIKED, "", T(SPOTIFY, "s2", "Bohemian Rhapsody - Remastered 2011", duration=354))
    c = C(env)
    c.scan()
    (g,) = findings(db, uid)["duplicates"]
    c.dedupe([{"songs": g["songs"], "keep": ["spotify", "s1"]}], [])
    assert findings(db, uid)["undo"]["n"] == 1
    res = C(env).undo()
    assert res["restored"] == 1 and sp.ids() == ["s1", "s2"]
    assert findings(db, uid)["undo"] is None


def test_dismissed_group_stays_hidden(env):
    db, uid, _ = env
    sp = P(env, SPOTIFY)
    sp.put_track(LIKED, "", T(SPOTIFY, "s1", "Bohemian Rhapsody"))
    sp.put_track(LIKED, "", T(SPOTIFY, "s2", "Bohemian Rhapsody - Remastered 2011", duration=354))
    C(env).scan()
    (g,) = findings(db, uid)["duplicates"]
    db.run("INSERT INTO cleanup_dismissed(user_id,key,created_at) VALUES(?,?,0)", (uid, g["key"]))
    assert findings(db, uid)["duplicates"] == []


def _castle(env):
    """One recording on the single, the album and a compilation, all three liked on Spotify."""
    sp = P(env, SPOTIFY)
    for id_, album, kind in (("single", "CASTLE OF GLASS", "single"), ("album", "LIVING THINGS", "album"),
                             ("comp", "Pool Party 2025 Best Hits", "compilation")):
        sp.put_track(LIKED, "", T(SPOTIFY, id_, "CASTLE OF GLASS", "Linkin Park", 205, album=album,
                                  isrc="USWB11200795", album_type=kind))
    C(env).scan()


@pytest.mark.parametrize("pref", ["album", "single"])
def test_duplicate_keeps_the_preferred_release(env, pref):
    db, uid, _ = env
    db.run("UPDATE users SET release_pref=? WHERE id=?", (pref, uid))
    _castle(env)
    f = findings(db, uid)
    (g,) = f["duplicates"]
    assert f["release"] == pref
    assert g["keep"] == f"spotify:{pref}"
    assert {v["release"] for v in g["variants"]} == {"album", "single", None}
    assert sorted(r for _, r in g["plans"][g["keep"]]) == sorted({"album", "single", "comp"} - {pref})


def test_dedupe_removes_all_but_the_preferred_release(env):
    db, uid, _ = env
    db.run("UPDATE users SET release_pref='single' WHERE id=?", (uid,))
    _castle(env)
    (g,) = findings(db, uid)["duplicates"]
    v = next(x for x in g["variants"] if x["id"] == g["keep"])
    res = C(env).dedupe([{"songs": g["songs"], "keep": [v["service"], v["ref_id"]]}], [])
    assert not res["errors"] and P(env, SPOTIFY).ids() == ["single"]


def test_release_is_read_from_album_names_without_album_type():
    from crossfade.cleanup import _release
    base = {"title": "Castle of Glass", "album_type": None, "video_type": None}
    assert _release({**base, "album": "Castle of Glass - Single"}) == "single"
    assert _release({**base, "album": "Castle of Glass"}) == "single"
    assert _release({**base, "album": "Living Things"}) is None          # album or compilation?
    assert _release({**base, "album": "Living Things", "album_type": "album"}) == "album"
    assert _release({**base, "album": ""}) is None
    assert _release({**base, "album": "Living Things", "video_type": "MUSIC_VIDEO_TYPE_OMV"}) is None


# ------------------------------------------------------------------------------ copies
def test_track_listed_twice_in_a_playlist_is_found_and_kept_once(env):
    db, uid, _ = env
    sp = P(env, SPOTIFY)
    sp.put_track(PLAYLIST, "gym", track(SPOTIFY, "lucky"), copies=3)
    sp.put_track(PLAYLIST, "gym", track(SPOTIFY, "blind"))
    c = C(env)
    c.scan()
    (cp,) = findings(db, uid)["copies"]
    assert cp["copies"] == 3 and cp["ref_id"] == "spotify_lucky"
    res = c.dedupe([], [[cp["source_id"], cp["ref_id"]]])
    assert res["removed"] == 2 and not res["errors"]
    assert sp.read(PLAYLIST, "gym")[0].handle.get("copies") is None
    assert sorted(sp.ids(PLAYLIST, "gym")) == ["spotify_blind", "spotify_lucky"]
    assert findings(db, uid)["copies"] == []


# ------------------------------------------------------------------------------ unusual versions
def test_karaoke_and_sped_up_are_flagged(env):
    db, uid, _ = env
    sp = P(env, SPOTIFY)
    sp.put_track(LIKED, "", T(SPOTIFY, "k", "Get Lucky (Karaoke Version)", "Party Band", 300))
    sp.put_track(LIKED, "", T(SPOTIFY, "f", "Levitating (Sped Up)", "Dua Lipa", 160))
    sp.put_track(LIKED, "", T(SPOTIFY, "o", "Levitating", "Dua Lipa", 203))
    C(env).scan()
    tags = {u["title"]: u["tags"] for u in findings(db, uid)["unusual"]}
    assert tags == {"Get Lucky (Karaoke Version)": ["Karaoke"], "Levitating (Sped Up)": ["Sped up / Slowed"]}


# ------------------------------------------------------------------------------ playlists
def test_playlist_findings_and_merge(env):
    db, uid, _ = env
    sp = P(env, SPOTIFY)
    for k in ("lucky", "blind", "bohem", "levit", "hello"):
        sp.put(PLAYLIST, "big", k)
    for k in ("lucky", "blind", "bohem", "levit"):
        sp.put(PLAYLIST, "small", k)
    sp.cols[(PLAYLIST, "empty")] = {}
    P(env, APPLE).put(PLAYLIST, "big", "lucky", "blind", "bohem", "levit", "hello")   # other service: no finding
    c = C(env)
    c.scan()
    kinds = {(p["kind"], tuple(p["sources"])) for p in findings(db, uid)["playlists"]}
    big, small, empty = (source(db, SPOTIFY, PLAYLIST, x) for x in ("big", "small", "empty"))
    assert ("subset", (small, big)) in kinds and ("empty", (empty,)) in kinds
    assert not any(k == "same_name" for k, _ in kinds)
    res = c.playlists([empty], [[small, big]])
    assert res["deleted"] == 2 and not res["errors"]
    assert sorted(sp.deleted) == ["empty", "small"]


# ------------------------------------------------------------------------------ YouTube: video or song
ATV, OMV = "MUSIC_VIDEO_TYPE_ATV", "MUSIC_VIDEO_TYPE_OMV"


def test_swap_songs_for_music_videos(env):
    db, uid, provs = env
    yt = P(env, YTM)
    song = T(YTM, "song1", "Bohemian Rhapsody", video_type=ATV)
    yt.put_track(LIKED, "", song)
    yt.counterparts["song1"] = T(YTM, "vid1", "Bohemian Rhapsody", duration=359, video_type=OMV)
    db.run("UPDATE accounts SET yt_format='video' WHERE id=?", (provs[YTM][0],))
    c = C(env)
    c.scan()
    sid = db.val("SELECT song_id FROM song_refs WHERE ref_id='song1'")
    f = findings(db, uid)["youtube"]
    assert f["format"] == "video" and [x["ref_id"] for x in f["candidates"]] == ["song1"]
    res = c.swap([[f["candidates"][0]["source_id"], "song1"]])
    assert res["swapped"] == 1 and not res["errors"]
    assert yt.ids() == ["vid1"]
    assert db.val("SELECT song_id FROM song_refs WHERE ref_id='vid1'") == sid      # still the same song
    assert findings(db, uid)["youtube"]["candidates"] == []


def test_song_without_video_is_remembered(env):
    db, uid, provs = env
    yt = P(env, YTM)
    yt.put_track(LIKED, "", T(YTM, "song1", "Bohemian Rhapsody", video_type=ATV))
    db.run("UPDATE accounts SET yt_format='video' WHERE id=?", (provs[YTM][0],))
    c = C(env)
    c.scan()
    res = c.swap([[source(db, YTM), "song1"]])
    assert res == {"swapped": 0, "without": 1, "failed": 0, "errors": []}
    f = findings(db, uid)["youtube"]
    assert f["candidates"] == [] and f["without"] == 1


def test_fade_adds_the_music_video_when_preferred(env):
    db, uid, provs = env
    yt, sp = P(env, YTM), P(env, SPOTIFY)
    sp.put(LIKED, "", "lucky")
    yt.search = lambda q, limit=10: [T(YTM, "yt_song", "Get Lucky", "Daft Punk", 370, video_type=ATV)] \
        if "lucky" in q.lower() else []
    yt.counterparts["yt_song"] = T(YTM, "yt_video", "Get Lucky", "Daft Punk", 372, video_type=OMV)
    db.run("UPDATE accounts SET yt_format='video' WHERE id=?", (provs[YTM][0],))
    lid = db.run("INSERT INTO lines(user_id,name,color,created_at,updated_at) VALUES(?,?,?,0,0)", (uid, "L", "#000"))
    for i, (svc, role) in enumerate(((SPOTIFY, "send"), (YTM, "receive"))):
        db.run("INSERT INTO line_members(line_id,account_id,kind,collection_id,collection_name,role,position) "
               "VALUES(?,?,?,?,?,?,?)", (lid, provs[svc][0], LIKED, "", svc, role, i))
    providers = {aid: p for aid, p in provs.values()}
    LineSync(db, lid, providers).run(apply=True)
    assert yt.ids() == ["yt_video"]
    r = LineSync(db, lid, providers).run(apply=True)     # the video counts as the song: nothing more to do
    assert r.status == "ok" and yt.ids() == ["yt_video"] and sp.keys() == ["lucky"]
