"""The services of a fade work side by side (reading, searching, adding), each one call at a time."""
from __future__ import annotations

import threading
import time

from test_engine import P, _instant_removals, _no_verify_wait, env, make_line, sync, track  # noqa: F401 - fixtures

from crossfade.engine import LineSync
from crossfade.models import APPLE, LIKED, SPOTIFY, YTM, AuthError, Track
from crossfade.providers.apple import AppleProvider


class Watch:
    """Counts how many service calls run at once: overall and per service."""

    def __init__(self):
        self.lock = threading.Lock()
        self.now: dict[str, int] = {}
        self.most: dict[str, int] = {}
        self.most_overall = 0
        self.inside = threading.local()     # a fake calling its own methods is still one call

    def wrap(self, provider, delay: float = 0.04):
        key = provider.service
        for name in ("read", "search", "by_isrc", "add", "remove"):
            fn = getattr(provider, name)

            def call(*a, _fn=fn, **kw):
                if getattr(self.inside, "busy", False):
                    return _fn(*a, **kw)
                self.inside.busy = True
                with self.lock:
                    self.now[key] = self.now.get(key, 0) + 1
                    self.most[key] = max(self.most.get(key, 0), self.now[key])
                    self.most_overall = max(self.most_overall, sum(self.now.values()))
                try:
                    time.sleep(delay)
                    return _fn(*a, **kw)
                finally:
                    with self.lock:
                        self.now[key] -= 1
                    self.inside.busy = False
            setattr(provider, name, call)


def test_services_work_side_by_side_but_each_takes_one_call_at_a_time(env):
    y, a, s = P(env, YTM), P(env, APPLE), P(env, SPOTIFY)
    y.put(LIKED, "", "lucky", "blind", "bohem", "levit")
    a.put(LIKED, "", "hello")
    s.put(LIKED, "", "haus", "duhast")
    watch = Watch()
    for p in (y, a, s):
        watch.wrap(p)
    lid = make_line(env, [(YTM, LIKED, "", "both"), (APPLE, LIKED, "", "both"), (SPOTIFY, LIKED, "", "both")])
    r = sync(env, lid)
    assert r.status == "ok", r.message
    everything = ["blind", "bohem", "duhast", "haus", "hello", "levit", "lucky"]
    assert y.keys() == a.keys() == s.keys() == everything
    assert watch.most_overall >= 2                      # the services did overlap ...
    assert set(watch.most.values()) == {1}              # ... but no service had two calls at once


def test_two_accounts_of_one_service_take_turns(env):
    db, _, provs = env
    other = db.run("INSERT INTO users(username,display_name,password_hash,created_at) VALUES('f','F','x',0)")
    aid2 = db.run("INSERT INTO accounts(user_id,service,created_at,updated_at) VALUES(?,?,0,0)", (other, SPOTIFY))
    from test_engine import FakeProvider
    s2 = FakeProvider(SPOTIFY)
    provs["spotify2"] = (aid2, s2)
    s, y = P(env, SPOTIFY), P(env, YTM)
    s.put(LIKED, "", "lucky", "blind")
    s2.put(LIKED, "", "hello", "haus")
    y.put(LIKED, "", "duhast")
    watch = Watch()
    watch.wrap(s)
    watch.wrap(s2)          # same service: counted together
    watch.wrap(y)
    lid = db.run("INSERT INTO lines(user_id,name,color,created_at,updated_at) VALUES(1,'L','#000',0,0)")
    for i, (aid, name) in enumerate(((provs[SPOTIFY][0], "s1"), (aid2, "s2"), (provs[YTM][0], "y"))):
        db.run("INSERT INTO line_members(line_id,account_id,kind,collection_id,collection_name,role,position) "
               "VALUES(?,?,?,?,?,?,?)", (lid, aid, LIKED, "", name, "both", i))
    providers = {aid: p for aid, p in provs.values()}
    r = LineSync(db, lid, providers).run(apply=True)
    assert r.status == "ok", r.message
    assert s.keys() == s2.keys() == y.keys() == ["blind", "duhast", "haus", "hello", "lucky"]
    assert watch.most[SPOTIFY] == 1


def test_a_slow_service_still_helps_a_fast_one(env):
    """Apple answers at once but finds nothing by text; Spotify takes its time and finds the song.
    Apple's miss waits for Spotify's answer and is searched again with Spotify's details."""
    y, a, s = P(env, YTM), P(env, APPLE), P(env, SPOTIFY)
    a.search = lambda q, limit=10: []
    slow = s.search

    def search(q, limit=10):
        time.sleep(0.15)
        return slow(q, limit)
    s.search = search
    y.cols[(LIKED, "")] = {"ytm_haus": Track(YTM, "ytm_haus", "Haus am See", "Peter Fox", duration=216)}
    lid = make_line(env, [(YTM, LIKED, "", "send"), (APPLE, LIKED, "", "receive"), (SPOTIFY, LIKED, "", "receive")])
    r = sync(env, lid)
    assert r.status == "ok", r.message
    assert a.keys() == s.keys() == ["haus"]
    assert r.summary["unmatched"] == 0


def test_a_login_failing_while_searching_names_its_account_and_changes_nothing(env):
    db, _, provs = env
    y, a, s = P(env, YTM), P(env, APPLE), P(env, SPOTIFY)
    y.put(LIKED, "", "lucky", "blind")

    def refuse(q, limit=10):
        raise AuthError("Apple Music sign-in is invalid")
    a.search = refuse
    a.by_isrc = lambda isrc: refuse(isrc)
    lid = make_line(env, [(YTM, LIKED, "", "send"), (APPLE, LIKED, "", "receive"), (SPOTIFY, LIKED, "", "receive")])
    sync_ = LineSync(db, lid, {aid: p for aid, p in provs.values()})
    r = sync_.run(apply=True)
    assert r.status == "auth_error" and "Apple" in r.message
    assert sync_.active_account == provs[APPLE][0]
    assert a.keys() == s.keys() == []          # searching stopped before anything was added


def test_a_music_video_asks_youtube_for_its_audio_version_once(env):
    """The audio track YouTube links to a video is remembered: a video that is searched on every
    sync (because the receiver doesn't have it) doesn't cost a YouTube call each time."""
    y = P(env, YTM)
    video = Track(YTM, "omv1", "Some Song (Official Video)", "Nobody", duration=230,
                  video_type="MUSIC_VIDEO_TYPE_OMV")
    audio = Track(YTM, "atv1", "Some Song", "Nobody", duration=228, video_type="MUSIC_VIDEO_TYPE_ATV")
    y.cols[(LIKED, "")] = {"omv1": video}
    asked: list[str] = []

    def counterpart(video_id):
        asked.append(video_id)
        return audio if video_id == "omv1" else None
    y.counterpart = counterpart
    lid = make_line(env, [(YTM, LIKED, "", "send"), (APPLE, LIKED, "", "receive")])
    for _ in range(3):
        r = sync(env, lid)
        assert r.summary["unmatched"] == 1        # Apple doesn't have it: searched again every time
    assert asked == ["omv1"]


def test_apple_looks_up_the_favourites_playlist_once_per_job(monkeypatch):
    am = AppleProvider.__new__(AppleProvider)
    am._fav_id = None
    listings: list[int] = []

    def playlists():
        listings.append(1)
        return [{"id": "p.other", "attributes": {"tags": []}}, {"id": "p.fav", "attributes": {"tags": ["favorited"]}}]
    am._playlists = playlists
    am._paged = lambda path, params: []
    am.read(LIKED, "")
    am.read(LIKED, "")
    assert am._favorites_id() == "p.fav" and len(listings) == 1
