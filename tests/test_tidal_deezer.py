"""TIDAL (official JSON:API) and Deezer (gateway + public API): parsing, paging, writes, sign-in."""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("CROSSFADE_DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("CROSSFADE_BASE_URL", "https://music.example.test")

import pytest  # noqa: E402

from crossfade.models import LIKED, PLAYLIST, AuthError, Track  # noqa: E402
from crossfade.providers import deezer as dz  # noqa: E402
from crossfade.providers import tidal as td  # noqa: E402


# ---------------------------------------------------------------------- TIDAL
def test_iso_durations():
    assert td.iso_seconds("PT2M58S") == 178
    assert td.iso_seconds("PT1H2M3S") == 3723
    assert td.iso_seconds("PT45.6S") == 46
    assert td.iso_seconds(None) is None and td.iso_seconds("garbage") is None


def _track_doc(ids, version=None):
    data, inc = [], []
    for i in ids:
        data.append({"id": i, "type": "tracks", "attributes": {"title": f"Song {i}", "version": version,
                                                               "duration": "PT3M5S", "isrc": f"ISRC{i}", "explicit": False},
                     "relationships": {"artists": {"data": [{"id": "a1", "type": "artists"}]},
                                       "albums": {"data": [{"id": f"al{i}", "type": "albums"}]}}})
        inc.append({"id": f"al{i}", "type": "albums", "attributes": {"title": "Album", "type": "SINGLE"},
                    "relationships": {"coverArt": {"data": [{"id": f"art{i}", "type": "artworks"}]}}})
        inc.append({"id": f"art{i}", "type": "artworks", "attributes": {"files": [
            {"href": "https://img/80.jpg", "meta": {"width": 80}}, {"href": "https://img/320.jpg", "meta": {"width": 320}},
            {"href": "https://img/1280.jpg", "meta": {"width": 1280}}]}})
    inc.append({"id": "a1", "type": "artists", "attributes": {"name": "Band"}})
    return {"data": data, "included": inc}


class TidalFake(td.TidalProvider):
    def __init__(self):
        super().__init__({"access_token": "t", "refresh_token": "r", "expires_at": 9e12, "client_id": "c"})
        self.calls = []
        self._me = {"attributes": {"country": "de", "username": "tidaluser"}}

    def _req(self, method, path, params=None, body=None, _attempt=0):
        self.calls.append((method, path, params, body))
        if path.startswith("/tracks") and method == "GET":
            ids = [v for k, v in params if k == "filter[id]"]
            return _track_doc(ids)
        if path == "/userCollectionTracks/me/relationships/items" and method == "GET":
            return {"data": [{"id": "1", "type": "tracks"}, {"id": "2", "type": "tracks"}],
                    "links": {"next": "/userCollectionTracks/me/relationships/items?page[cursor]=x"}}
        if "page[cursor]=x" in path:
            return {"data": [{"id": "3", "type": "tracks"}, {"id": "v9", "type": "videos"}], "links": {}}
        if path.startswith("/playlists/") and path.endswith("/relationships/items") and method == "GET":
            return {"data": [{"id": "1", "type": "tracks", "meta": {"itemId": "it-a"}},
                             {"id": "1", "type": "tracks", "meta": {"itemId": "it-b"}},
                             {"id": "2", "type": "tracks", "meta": {"itemId": "it-c"}}], "links": {}}
        if path == "/playlists" and method == "GET":
            return {"data": [{"id": "pl-1", "attributes": {"name": "Gym", "numberOfTrackItems": 12}}], "links": {}}
        if path == "/playlists" and method == "POST":
            return {"data": {"id": "pl-new", "attributes": {"name": body["data"]["attributes"]["name"]}}}
        if path.startswith("/searchResults/"):
            return {"data": [{"id": "5", "type": "tracks"}, {"id": "6", "type": "tracks"}]}
        return None


def test_tidal_reads_pages_and_details():
    p = TidalFake()
    liked = p.read(LIKED, "")
    assert [t.id for t in liked] == ["1", "2", "3"]
    t = liked[0]
    assert (t.title, t.artist, t.album, t.duration, t.isrc) == ("Song 1", "Band", "Album", 185, "ISRC1")
    assert t.artwork == "https://img/320.jpg" and t.album_type == "single" and t.url.endswith("/track/1")
    assert p.country() == "DE"
    pl = p.read(PLAYLIST, "pl-1")
    assert [x.id for x in pl] == ["1", "2"] and pl[0].handle["copies"] == 2


def test_tidal_writes():
    p = TidalFake()
    pl = p.read(PLAYLIST, "pl-1")
    p.calls.clear()
    assert p.remove(PLAYLIST, "pl-1", [pl[0]]) == []
    method, path, _, body = p.calls[-1]
    assert method == "DELETE" and {d["meta"]["itemId"] for d in body["data"]} == {"it-a", "it-b"}
    p.remove_copies(PLAYLIST, "pl-1", pl[0])
    assert [d["meta"]["itemId"] for d in p.calls[-1][3]["data"]] == ["it-b"]
    tracks = [Track(service="tidal", id=str(i), title="x") for i in range(120)]
    assert p.add(LIKED, "", tracks) == []
    posts = [c for c in p.calls if c[0] == "POST"]
    assert [len(c[3]["data"]) for c in posts] == [50, 50, 20] and posts[0][3]["data"][0] == {"type": "tracks", "id": "0"}
    col = p.create_playlist("Roadtrip")
    assert col.id == "pl-new" and p.calls[-1][3]["data"]["attributes"]["accessType"] == "UNLISTED"
    cols = p.collections()
    assert cols[0].kind == LIKED and cols[1].name == "Gym" and cols[1].count == 12
    assert [t.id for t in p.search("song", 5)] == ["5", "6"]
    assert p.calls[-2][1] == "/searchResults/song/relationships/tracks"


def test_tidal_links():
    assert td.TidalProvider.parse_link("https://tidal.com/browse/track/77640617?u") == "77640617"
    assert td.TidalProvider.parse_link("https://listen.tidal.com/track/77640617") == "77640617"
    assert td.TidalProvider.parse_link("77640617") == "77640617"
    assert td.TidalProvider.parse_link("https://open.spotify.com/track/77640617") is None


def test_tidal_search_escapes_the_query():
    p = TidalFake()
    p.search("AC/DC – T.N.T.", 3)
    assert p.calls[0][1] == "/searchResults/AC%2FDC%20%E2%80%93%20T.N.T./relationships/tracks"


# ---------------------------------------------------------------------- Deezer
def test_arl_cleanup():
    assert dz.clean_arl(' "arl=abc123; Path=/" ') == "abc123"
    assert dz.clean_arl("abc") == "abc"


SNG = {"SNG_ID": "3135556", "SNG_TITLE": "Harder, Better", "VERSION": "(Live)", "ARTISTS": [{"ART_NAME": "Daft Punk"}],
       "ALB_TITLE": "Alive", "ALB_PICTURE": "md5abc", "DURATION": "224", "ISRC": "GBDUW0000059", "EXPLICIT_LYRICS": "0"}


class DeezerFake(dz.DeezerProvider):
    def __init__(self, user_id="42"):
        super().__init__({"arl": "a" * 40})
        self.calls = []
        self.user_id = user_id
        self.likes = [dict(SNG, SNG_ID=str(i)) for i in range(1, 2501)] + [dict(SNG, SNG_ID="-5")]

    def _gw(self, method, payload=None, token=True, _attempt=0):
        self.calls.append((method, payload, token))
        if method == "deezer.getUserData":
            return {"USER": {"USER_ID": self.user_id, "BLOG_NAME": "dz", "LOVEDTRACKS_ID": "999"}, "checkForm": "csrf"}
        if method == "favorite_song.getList":
            s, n = payload["start"], payload["nb"]
            return {"data": self.likes[s:s + n], "total": len(self.likes)}
        if method == "deezer.pageProfile":
            return {"TAB": {"playlists": {"data": [
                {"PLAYLIST_ID": "999", "TITLE": "Loved", "PARENT_USER_ID": "42"},
                {"PLAYLIST_ID": "7", "TITLE": "Mine", "PARENT_USER_ID": "42", "NB_SONG": "3"},
                {"PLAYLIST_ID": "8", "TITLE": "Followed", "PARENT_USER_ID": "1"}]}}}
        if method == "playlist.create":
            return 1234
        return True


def test_deezer_parsing():
    t = dz.DeezerProvider._gw_track(SNG)
    assert (t.id, t.title, t.artist, t.duration, t.isrc, t.explicit) == (
        "3135556", "Harder, Better (Live)", "Daft Punk", 224, "GBDUW0000059", False)
    assert t.artwork == "https://e-cdns-images.dzcdn.net/images/cover/md5abc/264x264-000000-80-0-0.jpg"
    assert dz.DeezerProvider._gw_track({"SNG_ID": "-3", "SNG_TITLE": "upload"}) is None
    pub = dz.DeezerProvider._public_track({"id": 3135556, "title": "One More Time", "title_version": "(Radio Edit)",
                                           "duration": 320, "isrc": "X", "artist": {"name": "Daft Punk"},
                                           "album": {"title": "Discovery", "cover_medium": "https://c/250.jpg",
                                                     "record_type": "single"}, "explicit_lyrics": False})
    assert (pub.title, pub.album_type, pub.artwork) == ("One More Time (Radio Edit)", "single", "https://c/250.jpg")


def test_deezer_reads_all_pages_and_own_playlists():
    p = DeezerFake()
    liked = p.read(LIKED, "")
    assert len(liked) == 2500                            # uploaded MP3 (negative id) skipped
    assert [c[1]["start"] for c in p.calls if c[0] == "favorite_song.getList"] == [0, 2000]
    cols = p.collections()
    assert [c.name for c in cols] == ["Favourite tracks", "Mine"] and cols[1].count == 3
    assert p.whoami() == "dz"


def test_deezer_writes():
    p = DeezerFake()
    tr = [Track(service="deezer", id=str(i), title="x") for i in range(150)]
    assert p.add(PLAYLIST, "7", tr) == []
    adds = [c for c in p.calls if c[0] == "playlist.addSongs"]
    assert len(adds) == 2 and adds[0][1]["songs"][0] == ["0", 0] and adds[0][1]["playlist_id"] == "7"
    p.remove(LIKED, "", tr[:3])
    assert p.calls[-1] == ("song.removeFavorites", {"IDS": ["0", "1", "2"]}, True)
    assert p.create_playlist("Trip").id == "1234"
    assert p.calls[-1][1]["status"] == 1


def test_deezer_rejected_cookie():
    p = DeezerFake(user_id="0")
    with pytest.raises(AuthError):
        p.whoami()


def test_deezer_gateway_errors(monkeypatch):
    p = dz.DeezerProvider({"arl": "x" * 30})
    answers = [{"results": {"USER": {"USER_ID": 1}, "checkForm": "old"}},
               {"error": {"VALID_TOKEN_REQUIRED": "Invalid CSRF token"}},
               {"results": {"USER": {"USER_ID": 1}, "checkForm": "new"}},
               {"results": True},
               {"error": {"NEED_USER_AUTH_REQUIRED": "x"}}]
    sent = []

    class R:
        status_code = 200
        content = b"x"

        def __init__(self, d):
            self.d = d

        def json(self):
            return self.d

    def post(url, params=None, json=None):
        sent.append(params["api_token"])
        return R(answers.pop(0))
    monkeypatch.setattr(p.http, "post", post)
    assert p._gw("song.addFavorites", {"IDS": ["1"]}) is True
    assert sent == ["", "old", "", "new"]                # a stale CSRF token is fetched again once
    with pytest.raises(AuthError):
        p._gw("song.addFavorites", {"IDS": ["1"]})


def test_deezer_links():
    assert dz.DeezerProvider.parse_link("https://www.deezer.com/de/track/3135556") == "3135556"
    assert dz.DeezerProvider.parse_link("3135556") == "3135556"
    assert dz.DeezerProvider.parse_link("https://tidal.com/track/3135556") is None


def test_deezer_unknown_isrc_is_empty(monkeypatch):
    p = dz.DeezerProvider({})
    monkeypatch.setattr(p, "_public", lambda path, params=None: {})
    assert p.by_isrc("NOPE") == []


def test_sign_in_endpoints(monkeypatch):
    import time

    from crossfade.api import services as svc_api
    from crossfade.db import get_db
    from tests.test_sharing import H, person
    db = get_db()
    c, uid = person("td" + str(int(time.time() * 1e6) % 10**7), "T")
    s = {x["service"]: x for x in c.get("/api/services").json()}
    assert s["deezer"]["methods"] == ["token"] and s["tidal"]["admin_setup_needed"] is True
    assert c.post("/api/services/tidal/start", headers=H).status_code == 400
    db.set_setting("app_tidal_client_id", None)
    from crossfade.providers import set_app_credential
    set_app_credential(db, "tidal_client_id", "tidal-app")
    try:
        r = c.post("/api/services/tidal/start", headers=H)
        assert r.status_code == 200 and "login.tidal.com/authorize" in r.json()["url"]
        assert "collection.write" in r.json()["url"].replace("+", " ").replace("%20", " ")
        # someone else's browser coming back with the state is turned away
        state = r.json()["url"].split("state=")[1].split("&")[0]
        from fastapi.testclient import TestClient

        from crossfade.api.app import app
        stranger = TestClient(app, base_url="https://music.example.test")
        r = stranger.get(f"/api/services/tidal/callback?state={state}&code=x", follow_redirects=False)
        assert r.status_code == 303 and "tidal-failed" in r.headers["location"]
    finally:
        set_app_credential(db, "tidal_client_id", None)

    class Ok:
        def whoami(self):
            return "dzuser"
    monkeypatch.setattr(svc_api, "make_provider", lambda db, service, creds, save=None: Ok())
    r = c.post("/api/services/deezer/arl", json={"arl": "arl=" + "b" * 40 + "; path=/"}, headers=H)
    assert r.status_code == 200 and r.json()["display_name"] == "dzuser"
    from crossfade.security import decrypt
    acc = db.one("SELECT * FROM accounts WHERE user_id=? AND service='deezer'", (uid,))
    assert decrypt(acc["credentials"]) == {"arl": "b" * 40}
