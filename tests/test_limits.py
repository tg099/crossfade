"""Breaks a service asks for (crossfade/limits.py): nothing is sent until they are over, fades wait
instead of failing, and the scheduler starts them again once the break is over."""
from __future__ import annotations

import json
import time

import httpx
import pytest
from ytmusicapi.exceptions import YTMusicServerError

from crossfade import health, limits
from crossfade.engine import LineSync
from crossfade.models import APPLE, LIKED, SPOTIFY, YTM, RateLimited
from crossfade.providers.apple import AppleProvider
from crossfade.providers.deezer import DeezerProvider
from crossfade.providers.spotify import SpotifyProvider
from crossfade.providers.tidal import TidalProvider
from crossfade.providers.ytm import DataApi, YTMProvider
from tests.test_engine import P, _instant_removals, _no_verify_wait, env, make_line  # noqa: F401


# ------------------------------------------------------------------ the register
def test_a_break_without_a_time_gets_longer_each_time_and_starts_over_after_a_good_call():
    first = limits.pause(SPOTIFY)
    assert 590 < first.until - time.time() <= 600 and first.reason == limits.TOO_MANY
    # a call that was under way while the break started: the same break, not a longer one
    assert limits.pause(SPOTIFY).until == first.until
    limits._breaks[(SPOTIFY, limits.ALL)]["until"] = time.time() - 1
    assert 1790 < limits.pause(SPOTIFY).until - time.time() <= 1800
    limits._breaks[(SPOTIFY, limits.ALL)]["until"] = time.time() - 1
    limits.went_through(SPOTIFY, limits.LIBRARY)
    assert 590 < limits.pause(SPOTIFY).until - time.time() <= 600


def test_the_service_says_how_long_and_a_break_is_never_shortened():
    long = limits.pause(SPOTIFY, 3 * 3600, limits.QUOTA)
    assert limits.pause(SPOTIFY, 120).until == long.until
    assert limits.pause(SPOTIFY, 10 ** 9).until - time.time() <= limits.LONGEST
    with pytest.raises(RateLimited) as e:
        limits.check(SPOTIFY, limits.SEARCH)
    assert e.value.service == SPOTIFY and "Spotify needs a break" in str(e.value)


def test_a_narrow_break_leaves_the_rest_alone():
    limits.pause(APPLE, 600, scope=limits.SEARCH)
    limits.check(APPLE, limits.LIBRARY)
    limits.check(APPLE)
    with pytest.raises(RateLimited):
        limits.check(APPLE, limits.SEARCH)
    assert limits.current(APPLE).scope == limits.SEARCH
    assert limits.current(SPOTIFY) is None
    assert limits.active()[APPLE]["scope"] == limits.SEARCH


def test_retry_after_reads_seconds_and_dates():
    assert limits.retry_after({"Retry-After": "30"}) == 30
    assert limits.retry_after({}) is None
    later = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(time.time() + 600))
    assert 590 < limits.retry_after({"Retry-After": later}) <= 600
    assert 0 < limits.to_pacific_midnight() <= 24 * 3600 + 300


# ------------------------------------------------------------------ nothing is sent during a break
class Counting:
    service = SPOTIFY

    def __init__(self):
        self.calls = 0

    def read(self, kind, cid):
        self.calls += 1
        return []

    def search(self, q, limit=10):
        self.calls += 1
        return []


def test_a_provider_on_a_break_is_not_asked():
    inner = Counting()
    p = health.measured(inner)
    limits.pause(SPOTIFY, 600, scope=limits.SEARCH)
    assert p.read(LIKED, "") == [] and inner.calls == 1
    with pytest.raises(RateLimited):
        p.search("x")
    assert inner.calls == 1


# ------------------------------------------------------------------ the services
def _http(provider, handler):
    calls = []

    def h(request):
        calls.append(request.url.path)
        return handler(len(calls))
    provider.http = httpx.Client(transport=httpx.MockTransport(h))
    return calls


@pytest.fixture
def slept(monkeypatch):
    out: list[float] = []
    for mod in ("spotify", "tidal", "deezer", "apple", "ytm"):
        monkeypatch.setattr(f"crossfade.providers.{mod}.time.sleep", out.append)
    return out


def _spotify():
    return SpotifyProvider({"access_token": "t", "expires_at": time.time() + 3600, "client_id": "c"})


def test_spotify_quota_used_up_is_a_break_for_every_account(slept):
    p = _spotify()
    calls = _http(p, lambda n: httpx.Response(429, headers={"Retry-After": "1800"},
                                              json={"error": {"status": 429, "reason": "QUOTA_EXCEEDED"}}))
    with pytest.raises(RateLimited) as e:
        p._req("GET", "/me")
    assert e.value.reason == limits.QUOTA and 1790 < e.value.until - time.time() <= 1800
    other = _spotify()                        # another account of the same instance app
    other_calls = _http(other, lambda n: httpx.Response(200, json={}))
    with pytest.raises(RateLimited):
        other._req("GET", "/me")
    assert len(calls) == 1 and other_calls == [] and slept == []


def test_spotify_waits_a_short_while_in_place_and_takes_a_long_wait_as_a_break(slept):
    p = _spotify()
    _http(p, lambda n: httpx.Response(429, headers={"Retry-After": "2"}) if n == 1 else httpx.Response(200, json={"ok": 1}))
    assert p._req("GET", "/me") == {"ok": 1} and slept == [3]
    q = _spotify()
    calls = _http(q, lambda n: httpx.Response(429, headers={"Retry-After": "7200"}))
    with pytest.raises(RateLimited) as e:
        q._req("GET", "/me")
    assert len(calls) == 1 and 7190 < e.value.until - time.time() <= 7200


def test_spotify_still_refusing_after_waiting_takes_a_break(slept):
    p = _spotify()
    calls = _http(p, lambda n: httpx.Response(429))
    with pytest.raises(RateLimited) as e:
        p._req("GET", "/me")
    assert len(calls) == 6 and 590 < e.value.until - time.time() <= 600


def test_tidal_takes_a_long_wait_as_a_break(slept):
    p = TidalProvider({"access_token": "t", "expires_at": time.time() + 3600, "client_id": "c"})
    calls = _http(p, lambda n: httpx.Response(429, headers={"Retry-After": "900"}))
    with pytest.raises(RateLimited):
        p._req("GET", "/userCollections/me")
    assert len(calls) == 1 and limits.current("tidal").until - time.time() > 890


def test_deezer_search_quota_pauses_the_search_only(slept):
    p = DeezerProvider({})
    calls = _http(p, lambda n: httpx.Response(200, json={"error": {"code": 4, "message": "Quota limit exceeded"}}))
    with pytest.raises(RateLimited) as e:
        p._public("/search/track", {"q": "x"})
    assert e.value.scope == limits.SEARCH and len(calls) == 5
    limits.check("deezer", limits.LIBRARY)


def test_apple_search_gives_way_to_itunes_and_only_then_takes_a_break(slept, monkeypatch):
    p = AppleProvider.__new__(AppleProvider)
    monkeypatch.setattr(AppleProvider, "_itunes_last", 0.0)
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: httpx.Response(429, request=httpx.Request("GET", "https://x")))
    with pytest.raises(RateLimited) as e:
        p._itunes_get("https://itunes.apple.com/search", {})
    assert e.value.scope == limits.SEARCH
    limits.check(APPLE, limits.LIBRARY)


def test_youtube_still_too_many_requests_after_waiting_is_a_break(slept):
    p = YTMProvider.__new__(YTMProvider)
    p._persist_token = lambda: None
    calls = []

    def fn():
        calls.append(1)
        raise YTMusicServerError("Server returned HTTP 429: Too Many Requests.\n")
    with pytest.raises(RateLimited):
        p._call(fn)
    assert len(calls) == 4 and slept == [2, 4, 6]
    with pytest.raises(RateLimited):
        p._call(fn)                          # the next call isn't even tried
    assert len(calls) == 4


def test_youtube_data_api_quota_waits_for_midnight_pacific_and_leaves_youtube_music_alone(slept):
    api = DataApi.__new__(DataApi)
    api.token = {"access_token": "t", "expires_at": time.time() + 3600}
    reply = {"error": {"errors": [{"reason": "quotaExceeded"}], "message": "quota"}}
    calls = _http(api, lambda n: httpx.Response(403, json=reply))
    with pytest.raises(RateLimited) as e:
        api.req("GET", "/playlists")
    assert e.value.reason == limits.YT_QUOTA and len(calls) == 1
    assert abs(e.value.until - time.time() - limits.to_pacific_midnight()) < 5
    limits.check(YTM, limits.SEARCH)          # ytmusicapi (search, cookie logins) goes on
    limits.check(YTM, limits.LIBRARY)


# ------------------------------------------------------------------ fades wait
def run(env, lid):
    db, _, provs = env
    return LineSync(db, lid, {aid: health.measured(p) for aid, p in provs.values()}).run(apply=True)


def test_a_fade_waits_while_a_service_it_reads_is_on_a_break(env):
    db = env[0]
    P(env, YTM).put(LIKED, "", "lucky", "blind")
    lid = make_line(env, [(YTM, LIKED, "", "send"), (APPLE, LIKED, "", "receive")])
    brk = limits.pause(APPLE, 900, limits.QUOTA)
    r = run(env, lid)
    assert r.status == "waiting" and r.summary["recheck_at"] == brk.until
    assert r.summary["pause"] == {"service": APPLE, "until": brk.until, "reason": limits.QUOTA, "scope": "all"}
    assert P(env, APPLE).keys() == [] and P(env, APPLE).calls == []
    assert db.val("SELECT COUNT(*) FROM issues") == 0


def test_songs_that_need_a_search_on_a_break_wait_and_the_rest_goes_ahead(env):
    db = env[0]
    y, a = P(env, YTM), P(env, APPLE)
    y.put(LIKED, "", "lucky")
    a.put(LIKED, "", "blind")
    lid = make_line(env, [(YTM, LIKED, "", "both"), (APPLE, LIKED, "", "both")])
    limits.pause(APPLE, 900, scope=limits.SEARCH)
    r = run(env, lid)
    assert r.status == "waiting" and r.summary["pause"]["scope"] == limits.SEARCH
    assert y.keys() == ["blind", "lucky"] and a.keys() == ["blind"]     # YouTube got its song
    assert db.val("SELECT COUNT(*) FROM issues") == 0                  # nothing is "not found"
    limits.clear()
    r = run(env, lid)
    assert r.status == "ok" and a.keys() == ["blind", "lucky"]


def test_a_break_while_adding_leaves_the_rest_for_the_next_sync(env):
    db = env[0]
    y, a = P(env, YTM), P(env, APPLE)
    y.put(LIKED, "", "lucky", "blind")
    lid = make_line(env, [(YTM, LIKED, "", "send"), (APPLE, LIKED, "", "receive")])
    real_add = a.add

    def add(kind, cid, tracks):
        raise limits.pause(APPLE, 600, limits.QUOTA)
    a.add = add
    r = run(env, lid)
    assert r.status == "waiting" and r.summary["failed"] == 0
    assert db.val("SELECT COUNT(*) FROM changes WHERE ok=0") == 0
    limits.clear()
    a.add = real_add
    r = run(env, lid)
    assert r.status == "ok" and a.keys() == ["blind", "lucky"]


def test_changes_refused_because_the_service_went_on_a_break_dont_count_as_failed(env):
    db = env[0]
    y, a = P(env, YTM), P(env, APPLE)
    y.put(LIKED, "", "lucky", "blind")
    lid = make_line(env, [(YTM, LIKED, "", "send"), (APPLE, LIKED, "", "receive")])

    def add(kind, cid, tracks):          # a provider that keeps going and reports what failed
        limits.pause(APPLE, 600, limits.QUOTA)
        return [t.id for t in tracks]
    a.add = add
    r = run(env, lid)
    assert r.status == "waiting" and r.summary["failed"] == 0
    assert db.val("SELECT COUNT(*) FROM changes WHERE ok=0") == 0


# ------------------------------------------------------------------ the scheduler
def test_the_scheduler_leaves_a_waiting_fade_alone_until_the_break_is_over(env, monkeypatch):
    from crossfade.worker import Worker
    db = env[0]
    db.run("UPDATE accounts SET credentials='x', status='ok'")
    lines = {}
    for name, status, recheck in (("waiting", "waiting", time.time() + 900), ("over", "waiting", time.time() - 5),
                                  ("due", "ok", None)):
        lid = make_line(env, [(YTM, LIKED, "", "both"), (APPLE, LIKED, "", "both")])
        summary = {"recheck_at": recheck} if recheck else {}
        db.run("UPDATE lines SET name=?, interval_minutes=60, last_run_at=?, last_status=?, last_summary=? WHERE id=?",
               (name, time.time() - 7200, status, json.dumps(summary), lid))
        lines[lid] = name
    w = Worker(db)
    started = []
    monkeypatch.setattr(w, "start", lambda lid, trigger="manual", **kw: started.append(lines[lid]))
    monkeypatch.setattr(w, "_check_accounts", lambda: None)
    monkeypatch.setattr(w, "_housekeeping", lambda: None)
    w._tick()
    w.pool.shutdown(wait=False)
    assert sorted(started) == ["due", "over"]


def test_a_long_break_is_said_in_hours():
    assert "tries again in 10 min." in str(limits.pause(SPOTIFY, 600))
    assert "tries again in 9 h." in str(limits.pause(APPLE, 9 * 3600))
