"""YouTube Music via the official Data API (Google-code login), and when a failed call is tried again."""
import httpx
import pytest
import requests
from ytmusicapi.exceptions import YTMusicServerError, YTMusicUserError

from crossfade.models import LIKED, ApiError, AuthError, Track
from crossfade.providers.ytm import DataApi, YTMProvider, _iso_duration


def test_iso_duration():
    assert _iso_duration("PT3M21S") == 201
    assert _iso_duration("PT1H2M") == 3720
    assert _iso_duration(None) is None


class FakeApi(DataApi):
    def __init__(self, responses=None, fail_after=None):
        self.calls, self.responses, self.fail_after = [], responses or {}, fail_after

    def req(self, method, path, params=None, body=None):
        self.calls.append((method, path, params))
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            raise ApiError("The daily quota of the YouTube API is used up.", 403)
        return self.responses.get(path, {})


def test_tracks_parses_topic_and_music_video():
    api = FakeApi({"/videos": {"items": [
        {"id": "aaaaaaaaaaa", "snippet": {"title": "I Got Love", "channelTitle": "Miyagi - Topic"},
         "contentDetails": {"duration": "PT3M5S"}},
        {"id": "bbbbbbbbbbb", "snippet": {"title": "Daft Punk - One More Time (Official Video)",
                                          "channelTitle": "DaftPunkVEVO"}, "contentDetails": {"duration": "PT5M"}},
    ]}})
    items = [{"id": "it1", "snippet": {"resourceId": {"videoId": "aaaaaaaaaaa"}}},
             {"id": "it2", "snippet": {"resourceId": {"videoId": "bbbbbbbbbbb"}}},
             {"id": "it3", "snippet": {"resourceId": {"videoId": "deletedvid1"}}}]
    t = api.tracks(items)
    assert [(x.title, x.artist, x.duration) for x in t] == [
        ("I Got Love", "Miyagi", 185), ("One More Time (Official Video)", "Daft Punk", 300)]
    assert t[0].handle == {"item_id": "it1"}


def _provider(api):
    p = YTMProvider.__new__(YTMProvider)
    p.api = api
    return p


def test_like_uses_rate_and_stops_when_quota_is_gone(monkeypatch):
    monkeypatch.setattr("crossfade.providers.ytm.time.sleep", lambda s: None)
    api = FakeApi(fail_after=2)
    tracks = [Track(service="ytm", id=f"v{i}", title="t") for i in range(5)]
    failed = _provider(api).add(LIKED, "", tracks)
    assert failed == ["v2", "v3", "v4"]
    assert len(api.calls) == 3          # stopped after the quota error
    assert api.calls[0] == ("POST", "/videos/rate", {"id": "v0", "rating": "like"})


# ---------------------------------------------------------------- trying again (or not)
def _flaky(*errors):
    """A ytmusicapi call that fails with these errors first, then answers."""
    left = list(errors)
    calls = []

    def fn():
        calls.append(1)
        if left:
            raise left.pop(0)
        return "ok"
    return fn, calls


@pytest.fixture
def slept(monkeypatch):
    out: list[float] = []
    monkeypatch.setattr("crossfade.providers.ytm.time.sleep", out.append)
    return out


def _yt():
    p = YTMProvider.__new__(YTMProvider)
    p._persist_token = lambda: None
    return p


def test_a_refusal_is_not_tried_again(slept):
    fn, calls = _flaky(YTMusicServerError("Server returned HTTP 400: Bad Request.\nInvalid argument"))
    with pytest.raises(ApiError) as e:
        _yt()._call(fn)
    assert len(calls) == 1 and slept == [] and e.value.status == 400


def test_a_hiccup_is_waited_for(slept):
    fn, calls = _flaky(requests.ConnectionError("reset"),
                       YTMusicServerError("Server returned HTTP 503: Service Unavailable.\n"))
    assert _yt()._call(fn) == "ok"
    assert len(calls) == 3 and slept == [2, 4]


def test_a_lasting_hiccup_gives_up_without_a_last_pause(slept):
    fn, calls = _flaky(*[requests.Timeout("slow")] * 9)
    with pytest.raises(ApiError):
        _yt()._call(fn)
    assert len(calls) == 4 and slept == [2, 4, 6]


def test_an_unreadable_answer_is_tried_once_more(slept):
    fn, calls = _flaky(KeyError("contents"), KeyError("contents"))
    with pytest.raises(ApiError):
        _yt()._call(fn)
    assert len(calls) == 2 and slept == [1]


def test_missing_login_is_a_login_problem_at_once(slept):
    fn, calls = _flaky(YTMusicUserError("Please provide authentication before using this function"))
    with pytest.raises(AuthError):
        _yt()._call(fn)
    assert len(calls) == 1 and slept == []


def test_data_api_rides_out_a_network_hiccup(slept):
    class Http:
        def __init__(self):
            self.n = 0

        def request(self, *a, **kw):
            self.n += 1
            if self.n < 3:
                raise httpx.ConnectError("reset")
            return httpx.Response(200, json={"items": []})
    api = DataApi.__new__(DataApi)
    api.token, api.http = {"access_token": "t", "expires_at": 9e12}, Http()
    assert api.req("GET", "/videos") == {"items": []}
    assert slept == [2, 4]
    api.http = type("Down", (), {"request": lambda self, *a, **kw: (_ for _ in ()).throw(httpx.ConnectError("x"))})()
    with pytest.raises(ApiError, match="unreachable"):
        api.req("GET", "/videos")
