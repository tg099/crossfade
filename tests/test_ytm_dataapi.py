"""YouTube Music via the official Data API (Google-code login)."""
from crossfade.models import LIKED, ApiError, Track
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
