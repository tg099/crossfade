"""Apple answers catalog search with 429 'Request is forbidden' for one auth mode at a time (seen live
both ways). Search must switch to the other mode at once instead of retrying the blocked one."""
from crossfade.models import ApiError
from crossfade.providers.apple import AppleProvider


def make(blocked_user: bool):
    am = AppleProvider.__new__(AppleProvider)
    am.credentials = {"media_user_token": "x", "storefront": "de"}
    calls: list[tuple[bool, bool]] = []

    def req(method, path, params=None, body=None, user=True, _attempt=0, patient=True):
        calls.append((user, patient))
        if user == blocked_user:
            raise ApiError("HTTP 429", 429)
        return {"results": {"songs": {"data": [{"id": "1", "attributes": {"name": "Noch da", "artistName": "Cro"}}]}}}

    am._req = req
    return am, calls


def test_search_switches_mode_on_429(monkeypatch):
    monkeypatch.setattr(AppleProvider, "_search_as_user", None)
    am, calls = make(blocked_user=True)
    assert [t.title for t in am.search("noch da cro")] == ["Noch da"]
    assert calls == [(True, False), (False, False)]
    # remembered: the next search goes straight to the working mode
    calls.clear()
    am.search("x")
    assert calls == [(False, False)]


def test_search_switches_back_when_anonymous_gets_blocked(monkeypatch):
    monkeypatch.setattr(AppleProvider, "_search_as_user", False)
    am, calls = make(blocked_user=False)
    assert am.search("x")
    assert calls == [(False, False), (True, False)]
    assert AppleProvider._search_as_user is True


def test_both_modes_blocked_falls_back_to_itunes(monkeypatch):
    monkeypatch.setattr(AppleProvider, "_search_as_user", None)
    monkeypatch.setattr(AppleProvider, "_amp_blocked_until", 0.0)
    am = AppleProvider.__new__(AppleProvider)
    am.credentials = {"media_user_token": "x", "storefront": "de"}
    amp_calls = []

    def req(method, path, params=None, body=None, user=True, _attempt=0, patient=True):
        amp_calls.append(path)
        if path.endswith("/search"):
            raise ApiError("HTTP 429", 429)
        return {"data": []}          # hydration finds nothing -> iTunes data is kept

    am._req = req
    am._itunes_search = lambda q, limit: [__import__("crossfade.models", fromlist=["Track"]).Track("apple", "9", "noch da", "CRO")]
    assert [t.id for t in am.search("noch da cro")] == ["9"]
    # the block is remembered: the next search skips the catalog search entirely
    amp_calls.clear()
    am.search("x")
    assert amp_calls == []


def test_itunes_result_parsing():
    t = AppleProvider._itunes({"kind": "song", "trackId": 1802920905, "trackName": "noch da", "artistName": "CRO",
                               "collectionName": "tru.", "trackTimeMillis": 192000, "trackExplicitness": "notExplicit",
                               "artworkUrl100": "https://x/100x100bb.jpg"})
    assert (t.id, t.title, t.artist, t.album, t.duration, t.artwork) == ("1802920905", "noch da", "CRO", "tru.", 192,
                                                                         "https://x/300x300bb.jpg")
    assert AppleProvider._itunes({"kind": "music-video", "trackId": 1}) is None
