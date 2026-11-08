"""Listening samples: id checks, sources per service, caching."""
from __future__ import annotations

import httpx
import pytest

from crossfade import preview as pv


@pytest.fixture(autouse=True)
def _fresh_cache():
    pv._cache.clear()
    yield
    pv._cache.clear()


def test_valid_ids():
    assert pv.valid_id("apple", "617154366")
    assert not pv.valid_id("apple", "i.KoJeg8ZCXK4jp")  # library-only id: no catalog sample
    assert pv.valid_id("spotify", "4cOdK2wGLETKBW3PvgPWqT")
    assert not pv.valid_id("spotify", "../../embed/x")
    assert pv.valid_id("ytm", "dQw4w9WgXcQ")
    assert not pv.valid_id("ytm", "dQw4w9WgXcQ/../x")
    assert not pv.valid_id("napster", "123")
    assert pv.valid_id("deezer", "3135556") and not pv.valid_id("deezer", "x/../1")


def test_tidal_has_no_samples(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: pytest.fail("no request expected"))
    assert pv.preview("tidal", "77640617") is None


def test_deezer_sample_only_from_its_cdn(monkeypatch):
    class R:
        status_code = 200

        def __init__(self, url):
            self.url = url

        def json(self):
            return {"preview": self.url}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: R("https://cdnt-preview.dzcdn.net/api/1/1/a.mp3"))
    assert pv.preview("deezer", "3135556") == {"kind": "audio", "url": "https://cdnt-preview.dzcdn.net/api/1/1/a.mp3"}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: R("https://evil.example/a.mp3"))
    assert pv.preview("deezer", "3135557") is None


def test_youtube_needs_no_lookup(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: pytest.fail("no request expected"))
    assert pv.preview("ytm", "dQw4w9WgXcQ") == {"kind": "youtube", "id": "dQw4w9WgXcQ"}


def test_apple_prefers_catalog_previews(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: pytest.fail("lookup not needed"))
    url = "https://audio-ssl.itunes.apple.com/itunes-assets/AudioPreview/x.m4a"
    amp = lambda path: {"data": [{"attributes": {"previews": [{"url": url}]}}]}  # noqa: E731
    assert pv.preview("apple", "1", "de", amp) == {"kind": "audio", "url": url}


def test_apple_falls_back_to_itunes_lookup(monkeypatch):
    url = "https://audio-ssl.itunes.apple.com/itunes-assets/AudioPreview/y.m4a"
    seen = {}

    def get(u, params=None, **k):
        seen.update(params)
        return httpx.Response(200, json={"results": [{"previewUrl": url}]})

    def amp(path):
        raise RuntimeError("429")

    monkeypatch.setattr(httpx, "get", get)
    assert pv.preview("apple", "2", "de", amp) == {"kind": "audio", "url": url}
    assert seen == {"id": "2", "country": "de"}


def test_spotify_reads_embed_page_and_caches(monkeypatch):
    calls = []

    def get(u, **k):
        calls.append(u)
        return httpx.Response(200, text='..."audioPreview":{"url":"https://p.scdn.co/mp3-preview/abc"},...')

    monkeypatch.setattr(httpx, "get", get)
    tid = "4cOdK2wGLETKBW3PvgPWqT"
    assert pv.preview("spotify", tid) == {"kind": "audio", "url": "https://p.scdn.co/mp3-preview/abc"}
    assert pv.preview("spotify", tid)["url"].endswith("/abc")
    assert len(calls) == 1


def test_foreign_audio_hosts_are_refused(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda u, **k: httpx.Response(
        200, text='"audioPreview":{"url":"https://evil.example/x.mp3"}'))
    assert pv.preview("spotify", "4cOdK2wGLETKBW3PvgPWqT") is None


def test_network_errors_are_not_cached(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "get", boom)
    assert pv.preview("spotify", "4cOdK2wGLETKBW3PvgPWqT") is None
    assert not pv._cache
