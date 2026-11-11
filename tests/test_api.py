"""API tests (auth, admin-created users, lines CRUD, isolation between users)."""
from __future__ import annotations

import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["CROSSFADE_DATA_DIR"] = _tmp
os.environ["CROSSFADE_BASE_URL"] = "https://music.example.test"

import time  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from crossfade.api import auth as auth_api  # noqa: E402
from crossfade.api.app import app  # noqa: E402
from crossfade.db import get_db  # noqa: E402
from crossfade.security import encrypt  # noqa: E402

H = {"X-Crossfade": "1"}


@pytest.fixture(scope="module")
def admin():
    c = TestClient(app, base_url="https://music.example.test")
    assert c.get("/api/auth/state").json()["setup_needed"] is True
    r = c.post("/api/auth/setup", json={"username": "tristan", "display_name": "Tristan",
                                        "password": "very-secret-123"}, headers=H)
    assert r.status_code == 200, r.text
    return c


def connect_fake(user_id: int, service: str):
    db = get_db()
    db.run("INSERT OR REPLACE INTO accounts(user_id,service,display_name,credentials,created_at,updated_at) "
           "VALUES(?,?,?,?,?,?)", (user_id, service, "Fake", encrypt({"x": 1}), time.time(), time.time()))


def test_csrf_header_required(admin):
    r = admin.post("/api/auth/logout")
    assert r.status_code == 403


def test_setup_only_once(admin):
    r = admin.post("/api/auth/setup", json={"username": "x2", "display_name": "X", "password": "0123456789"},
                   headers=H)
    assert r.status_code == 409


def test_login_wrong_password_and_rate_limit():
    c = TestClient(app, base_url="https://music.example.test")
    codes = [c.post("/api/auth/login", json={"username": "tristan", "password": "wrong-password"}, headers=H).status_code
             for _ in range(9)]
    assert codes[0] == 401 and codes[-1] == 429


def test_unauthenticated_is_rejected():
    c = TestClient(app, base_url="https://music.example.test")
    assert c.get("/api/lines").status_code == 401
    assert c.get("/api/services").status_code == 401


def test_admin_creates_user_and_isolation(admin):
    r = admin.post("/api/admin/users", json={"display_name": "Lena", "username": "lena"}, headers=H)
    assert r.status_code == 200, r.text
    lena_id, start_pw = r.json()["id"], r.json()["password"]
    assert len(start_pw) >= 10
    assert admin.post("/api/admin/users", json={"display_name": "L2", "username": "LENA"}, headers=H).status_code == 409
    auth_api._attempts.clear()   # the rate-limit test above used up this client's attempts
    guest = TestClient(app, base_url="https://music.example.test")
    r = guest.post("/api/auth/login", json={"username": "lena", "password": start_pw}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["user"]["must_change_password"] is True and r.json()["user"]["is_admin"] is False
    # the start password has to be replaced, and is good for nothing else
    assert guest.get("/api/lines").status_code == 403
    assert guest.get("/api/services").status_code == 403
    assert guest.put("/api/auth/me/password", json={"current": start_pw, "new": start_pw}, headers=H).status_code == 400
    r = guest.put("/api/auth/me/password", json={"current": start_pw, "new": "also-secret-456"}, headers=H)
    assert r.status_code == 200, r.text
    assert guest.get("/api/auth/state").json()["user"]["must_change_password"] is False
    # non-admin cannot use admin endpoints
    assert guest.get("/api/admin/users").status_code == 403
    assert guest.post("/api/admin/users", json={"display_name": "X", "username": "x"}, headers=H).status_code == 403

    # lines are per user
    admin_id = admin.get("/api/auth/state").json()["user"]["id"]
    for uid in (admin_id, lena_id):
        connect_fake(uid, "ytm")
        connect_fake(uid, "apple")
    body = {"name": "Favourite songs", "color": "#0072CE", "members": [
        {"service": "ytm", "kind": "liked", "collection_name": "Likes", "role": "both"},
        {"service": "apple", "kind": "liked", "collection_name": "Favourites", "role": "both"}]}
    r = admin.post("/api/lines", json=body, headers=H)
    assert r.status_code == 200, r.text
    line = r.json()
    assert line["number"] == 1 and len(line["members"]) == 2 and line["warnings"] == []
    assert guest.get(f"/api/lines/{line['id']}").status_code == 404
    assert guest.get("/api/lines").json() == []

    # editing keeps member state, role change allowed
    body["members"][1]["role"] = "receive"
    r = admin.put(f"/api/lines/{line['id']}", json=body, headers=H)
    assert r.status_code == 200
    assert [m["id"] for m in r.json()["members"]] == [m["id"] for m in line["members"]]
    assert r.json()["members"][1]["role"] == "receive"


def test_admin_user_management(admin):
    r = admin.post("/api/admin/users", json={"display_name": "Sam", "username": "sam"}, headers=H)
    sam = r.json()["id"]
    assert admin.put(f"/api/admin/users/{sam}", json={"display_name": "Samuel", "username": "samuel"},
                     headers=H).status_code == 200
    users = {u["id"]: u for u in admin.get("/api/admin/users").json()}
    assert users[sam]["username"] == "samuel" and users[sam]["must_change_password"] is True
    # reset: new start password, signed out everywhere
    auth_api._attempts.clear()
    c = TestClient(app, base_url="https://music.example.test")
    first = admin.post(f"/api/admin/users/{sam}/password", headers=H).json()["password"]
    assert c.post("/api/auth/login", json={"username": "samuel", "password": first}, headers=H).status_code == 200
    second = admin.post(f"/api/admin/users/{sam}/password", headers=H).json()["password"]
    assert c.get("/api/lines").status_code == 401
    assert c.post("/api/auth/login", json={"username": "samuel", "password": second}, headers=H).status_code == 200
    me = admin.get("/api/auth/state").json()["user"]["id"]
    assert admin.post(f"/api/admin/users/{me}/password", headers=H).status_code == 400
    # the last admin stays
    assert admin.put(f"/api/admin/users/{me}/admin", json={"is_admin": False}, headers=H).status_code == 400
    assert admin.put(f"/api/admin/users/{sam}/admin", json={"is_admin": True}, headers=H).status_code == 200
    assert admin.put(f"/api/admin/users/{sam}/admin", json={"is_admin": False}, headers=H).status_code == 200
    assert admin.delete(f"/api/admin/users/{sam}", headers=H).status_code == 200


def test_preferences_are_per_user(admin):
    r = admin.put("/api/auth/me", json={"theme": "dark", "lang": "en"}, headers=H)
    assert r.status_code == 200, r.text
    u = admin.get("/api/auth/state").json()["user"]
    assert (u["theme"], u["lang"]) == ("dark", "en")
    assert admin.put("/api/auth/me", json={"theme": "pink"}, headers=H).status_code == 422
    admin.put("/api/auth/me", json={"theme": "system", "lang": "system"}, headers=H)


def test_fade_icon_order_and_week(admin):
    me = admin.get("/api/auth/state").json()["user"]["id"]
    connect_fake(me, "ytm")
    connect_fake(me, "apple")
    body = {"name": "Car ride", "color": "#E4A100", "icon": "car", "members": [
        {"service": "ytm", "kind": "liked", "collection_name": "Likes", "role": "send"},
        {"service": "apple", "kind": "liked", "collection_name": "Favourites", "role": "receive"}]}
    r = admin.post("/api/lines", json=body, headers=H)
    assert r.status_code == 200 and r.json()["icon"] == "car"
    assert admin.post("/api/lines", json={**body, "icon": "<b>"}, headers=H).status_code == 422
    ids = [line["id"] for line in admin.get("/api/lines").json()]
    r = admin.put("/api/lines", json={"ids": list(reversed(ids))}, headers=H)
    assert [line["id"] for line in r.json()] == list(reversed(ids))
    week = admin.get("/api/lines/activity?tz_offset=-60").json()["week"]
    assert len(week["days"]) == 7 and week["total"] == sum(week["days"])
    assert week["prev_total"] == 0


def test_follow_deletions_flag(admin):
    # ytm and apple are connected by the tests above (reconnecting would cascade-delete their fades)
    body = {"name": "Mirror", "members": [
        {"service": "ytm", "kind": "liked", "collection_name": "Likes", "role": "send", "follow_deletions": True},
        {"service": "apple", "kind": "liked", "collection_name": "Favourites", "role": "both", "follow_deletions": True}]}
    r = admin.post("/api/lines", json=body, headers=H)
    assert r.status_code == 200
    ms = r.json()["members"]
    assert ms[0]["follow_deletions"] is True
    assert ms[1]["follow_deletions"] is False          # only stored for send-only collections
    # two send-only collections, one following deletions: no "nothing receives" warning
    body["members"][1]["role"] = "send"
    body["members"][1]["follow_deletions"] = False
    r = admin.put(f"/api/lines/{r.json()['id']}", json=body, headers=H)
    assert r.status_code == 200
    assert not any(w["kind"] == "no_receiver" for w in r.json()["warnings"])
    body["members"][0]["follow_deletions"] = False
    r = admin.put(f"/api/lines/{r.json()['id']}", json=body, headers=H)
    assert r.json()["members"][0]["follow_deletions"] is False
    assert any(w["kind"] == "no_receiver" for w in r.json()["warnings"])
    assert admin.delete(f"/api/lines/{r.json()['id']}", headers=H).status_code == 200   # module-shared state


def test_line_validation(admin):
    body = {"name": "X", "color": "red", "members": [
        {"service": "ytm", "kind": "liked", "collection_name": "Likes"},
        {"service": "apple", "kind": "liked", "collection_name": "Favourites"}]}
    assert admin.post("/api/lines", json=body, headers=H).status_code == 422
    body["color"] = "#112233"
    body["members"][1] = body["members"][0]
    assert admin.post("/api/lines", json=body, headers=H).status_code == 400   # duplicate stop
    body["members"][1] = {"service": "spotify", "kind": "liked", "collection_name": "Favourite songs"}
    r = admin.post("/api/lines", json=body, headers=H)
    assert r.status_code == 400 and "not connected" in r.json()["detail"]


def test_interchange_warning(admin):
    body = {"name": "Second", "color": "#2E9E44", "members": [
        {"service": "apple", "kind": "liked", "collection_name": "Favourites", "role": "both"},
        {"service": "ytm", "kind": "playlist", "collection_id": "PL1", "collection_name": "Gym", "role": "both"}]}
    r = admin.post("/api/lines", json=body, headers=H)
    assert r.status_code == 200, r.text
    kinds = [w["kind"] for w in r.json()["warnings"]]
    assert "interchange" in kinds


def test_security_headers_and_spa_fallback(admin):
    r = admin.get("/api/health")
    assert r.headers["x-frame-options"] == "DENY" and "default-src 'self'" in r.headers["content-security-policy"]
    r = admin.get("/linien/123")
    assert r.status_code in (200, 503)
    assert admin.get("/api/nope").status_code == 404


def test_services_overview(admin):
    s = {x["service"]: x for x in admin.get("/api/services").json()}
    assert s["ytm"]["connected"] and s["spotify"]["connected"] is False
    assert s["spotify"]["admin_setup_needed"] is True
    assert "paste" in s["ytm"]["methods"]
    apps = admin.get("/api/admin/apps").json()
    assert apps["spotify_redirect_uri"] == "https://music.example.test/api/services/spotify/callback"
    admin.put("/api/admin/apps", json={"spotify_client_id": "abc123"}, headers=H)
    s = {x["service"]: x for x in admin.get("/api/services").json()}
    assert s["spotify"]["methods"] == ["oauth"]
    r = admin.post("/api/services/spotify/start", headers=H)
    assert r.status_code == 200 and "code_challenge" in r.json()["url"]


def test_version_audit_and_detail(admin):
    db = get_db()
    line = admin.get("/api/lines").json()[0]
    ytm_m, apple_m = line["members"][0], line["members"][1]
    sid = db.run("INSERT INTO songs(title,artist,album,duration,isrc,created_at) VALUES('Bohemian Rhapsody','Queen','A Night at the Opera',355,'GBUM71029604',0)")
    db.run("INSERT INTO song_refs(service,ref_id,song_id,title,artist,album,duration,isrc) VALUES('apple','a1',?,'Bohemian Rhapsody','Queen','A Night at the Opera',355,'GBUM71029604')", (sid,))
    db.run("INSERT INTO song_refs(service,ref_id,song_id,title,artist,album,duration,video_type) VALUES('ytm','y1',?,'Bohemian Rhapsody (Remastered 2011)','Queen','A Night at the Opera',356,'MUSIC_VIDEO_TYPE_ATV')", (sid,))
    db.run("INSERT INTO line_items(line_id,song_id,added_at) VALUES(?,?,0)", (line["id"], sid))
    db.run("INSERT INTO member_items(member_id,song_id,ref_id,confirmed) VALUES(?,?,'y1',1)", (ytm_m["id"], sid))
    db.run("INSERT INTO member_items(member_id,song_id,ref_id,confirmed) VALUES(?,?,'a1',1)", (apple_m["id"], sid))
    audit = admin.get(f"/api/lines/{line['id']}/versions").json()
    assert len(audit) == 1 and audit[0]["stops"][0]["service"] == "ytm" and "Remaster" in audit[0]["stops"][0]["notes"]
    detail = admin.get(f"/api/lines/{line['id']}/songs/{sid}").json()
    by = {s["service"]: s for s in detail["stops"]}
    assert by["apple"]["exact"] is True and by["ytm"]["exact"] is False and by["ytm"]["version"].startswith("Remaster")


def test_database_backup(tmp_path):
    from crossfade.db import Database
    db = Database(tmp_path / "x.sqlite3")
    db.run("INSERT INTO instance_settings(key,value) VALUES('a','1')")
    for _ in range(12):
        Database(tmp_path / "x.sqlite3", backup=True)
    files = sorted((tmp_path / "backups").glob("*.sqlite3"))
    from crossfade.db import MIGRATIONS
    assert 1 <= len(files) <= 10 and f"schema{len(MIGRATIONS)}" in files[-1].name


def test_catalog_payload_and_isolation(admin):
    db = get_db()
    uid = admin.get("/api/auth/state").json()["user"]["id"]
    connect_fake(uid, "apple")
    acc = db.val("SELECT id FROM accounts WHERE user_id=? AND service='apple'", (uid,))
    src = db.run("INSERT INTO catalog_sources(account_id,kind,collection_id,name) VALUES(?,?,?,?)",
                 (acc, "liked", "", "Favourites"))
    sid = db.run("INSERT INTO songs(title,artist,album,duration,created_at) VALUES('Haus am See','Peter Fox','',215,0)")
    db.run("INSERT INTO song_refs(service,ref_id,song_id,title,artist) VALUES('apple','cat-a1',?,'Haus am See','Peter Fox')",
           (sid,))
    db.run("INSERT INTO catalog_items(source_id,ref_id,song_id,first_seen) VALUES(?,?,?,1)", (src, "cat-a1", sid))
    data = admin.get("/api/catalog").json()
    song = next(s for s in data["songs"] if s["id"] == sid)
    assert song["t"] == "Haus am See" and song["in"] == [[src, "cat-a1"]]
    assert any(s["id"] == src and s["service"] == "apple" for s in data["sources"])
    assert admin.get(f"/api/catalog/songs/{sid}").json()["refs"][0]["collections"][0]["name"] == "Favourites"
    # other users neither see nor change it
    other = TestClient(app, base_url="https://music.example.test")
    assert other.get("/api/catalog").status_code == 401
    assert admin.put(f"/api/catalog/sources/{src}", json={"included": False}, headers=H).status_code == 200
    assert not [s for s in admin.get("/api/catalog").json()["songs"] if s["id"] == sid]
    assert admin.post("/api/catalog/remove", json={"song_ids": [sid], "source_ids": [999999]},
                      headers=H).status_code == 404


def test_live_results_endpoint(admin):
    line = admin.get("/api/lines").json()[0]
    r = admin.get(f"/api/lines/{line['id']}/live?since=0")
    assert r.status_code == 200 and r.json() == {"running": False, "started_at": None, "n": 0, "items": []}
    assert admin.get("/api/lines/99999/live").status_code == 404


def test_preview_endpoint(admin):
    anon = TestClient(app, base_url="https://music.example.test")
    assert anon.get("/api/preview/ytm/dQw4w9WgXcQ").status_code == 401
    r = admin.get("/api/preview/ytm/dQw4w9WgXcQ")
    assert r.status_code == 200 and r.json() == {"kind": "youtube", "id": "dQw4w9WgXcQ"}
    assert admin.get("/api/preview/apple/i.abc").status_code == 404
    assert admin.get("/api/preview/tidal/123").status_code == 404
    csp = admin.get("/api/auth/state").headers["content-security-policy"]
    assert "media-src 'self' https://*.itunes.apple.com" in csp and "frame-src https://www.youtube-nocookie.com" in csp


def test_fade_stats_from_history(admin):
    me = admin.get("/api/auth/state").json()["user"]["id"]
    connect_fake(me, "ytm")
    connect_fake(me, "apple")
    body = {"name": "Stats", "members": [
        {"service": "ytm", "kind": "liked", "collection_name": "Likes", "role": "send"},
        {"service": "apple", "kind": "liked", "collection_name": "Favourites", "role": "receive"}]}
    line = admin.post("/api/lines", json=body, headers=H).json()
    lid, (send, recv) = line["id"], [m["id"] for m in line["members"]]
    db, now = get_db(), time.time()
    songs = [db.run("INSERT INTO songs(title,artist,created_at) VALUES(?,?,?)", (f"S{i}", "A", now))
             for i in range(4)]
    run = db.run("INSERT INTO runs(line_id,trigger,started_at,finished_at,status) VALUES(?,?,?,?,?)",
                    (lid, "manual", now - 3600, now - 3500, "ok"))
    db.run("INSERT INTO runs(line_id,trigger,started_at,status) VALUES(?,?,?,?)", (lid, "preview", now - 9e5, "preview"))
    db.run("UPDATE line_members SET initialized=1 WHERE line_id=?", (lid,))
    for sid in songs:
        db.run("INSERT INTO line_items(line_id,song_id,added_at) VALUES(?,?,?)", (lid, sid, now))
        db.run("INSERT INTO member_items(member_id,song_id,confirmed) VALUES(?,?,1)", (send, sid))
    # three arrived in the receiver (one of them later removed there on purpose), one is missing
    for sid, conf, excl in ((songs[0], 1, 0), (songs[1], 0, 0), (songs[2], 0, 1)):
        db.run("INSERT INTO member_items(member_id,song_id,confirmed,excluded) VALUES(?,?,?,?)", (recv, sid, conf, excl))
    for sid, action, ok in ((songs[0], "add", 1), (songs[1], "add", 1), (songs[3], "add", 0), (songs[2], "remove", 1)):
        db.run("INSERT INTO changes(run_id,line_id,member_id,song_id,action,label,ok,ts) VALUES(?,?,?,?,?,?,?,?)",
               (run, lid, recv, sid, action, "x", ok, now - 3500))
    s = admin.get(f"/api/lines/{lid}/stats").json()
    assert (s["songs"], s["complete"], s["syncs"]) == (4, 3, 1)
    assert (s["added"], s["removed"], s["failed_30d"], s["saved_s"]) == (2, 1, 1, 60)
    assert s["since"] == pytest.approx(now - 3600)       # the first real sync, not the preview
    assert {m["member_id"]: m["added"] for m in s["members"]} == {send: 0, recv: 2}



def test_health_links_the_source_of_this_version(admin, monkeypatch):
    """Every instance points its users to the source code of the running version."""
    import dataclasses

    from crossfade.api import app as app_module
    cfg = app_module.config
    assert admin.get("/api/health").json()["source"] == "https://github.com/tg099/crossfade"  # dev build: the repository
    monkeypatch.setattr(app_module, "config", dataclasses.replace(cfg, version="1.4.2"))
    assert admin.get("/api/health").json()["source"] == "https://github.com/tg099/crossfade/tree/v1.4.2"


def test_spotify_callback_needs_the_session_that_started_it(admin):
    """Someone else's sign-in link must not link the visitor's Spotify to the link owner's account."""
    from crossfade.api import services as services_api
    admin_id = admin.get("/api/auth/state").json()["user"]["id"]
    state = services_api._put_flow({"user_id": admin_id, "verifier": "v", "kind": "spotify"})
    stranger = TestClient(app, base_url="https://music.example.test", follow_redirects=False)
    r = stranger.get(f"/api/services/spotify/callback?state={state}&code=abc")
    assert r.status_code == 303 and r.headers["location"] == "/services?error=spotify-failed"
    assert state not in services_api._flows                   # used up either way
    r = admin.get("/api/services/spotify/callback?state=unknown&code=abc", follow_redirects=False)
    assert r.headers["location"] == "/services?error=spotify-failed"


def test_device_flow_keys_are_not_interchangeable(admin):
    from crossfade.api import services as services_api
    admin_id = admin.get("/api/auth/state").json()["user"]["id"]
    state = services_api._put_flow({"user_id": admin_id, "verifier": "v", "kind": "spotify"})
    assert admin.post("/api/services/ytm/device/poll", json={"flow": state}, headers=H).status_code == 404


def test_login_limits_and_unknown_users():
    auth_api._attempts.clear()
    c = TestClient(app, base_url="https://music.example.test")
    assert c.post("/api/auth/login", json={"username": "nobody-here", "password": "x" * 12},
                  headers=H).status_code == 401
    assert c.post("/api/auth/login", json={"username": "tristan", "password": "x" * 300},
                  headers=H).status_code == 422
    auth_api._attempts.clear()


def test_page_sizes_are_clamped(admin):
    lid = admin.get("/api/lines").json()[0]["id"]
    assert admin.get(f"/api/lines/{lid}/changes?limit=-1").status_code == 200
    assert admin.get(f"/api/lines/{lid}/songs?limit=-1&offset=-5").status_code == 200
    from crossfade.api.lines import _clamp
    assert (_clamp(-1, 500), _clamp(0, 500), _clamp(9999, 500), _clamp(30, 500)) == (1, 1, 500, 30)


def test_event_bus_delivers_across_threads():
    import asyncio
    import threading

    from crossfade.worker import EventBus

    async def main():
        bus = EventBus()
        q = bus.subscribe(7)
        threading.Thread(target=bus.publish, args=(7, {"type": "job"})).start()
        ev = await asyncio.wait_for(q.get(), 2)
        bus.unsubscribe(7, q)
        bus.publish(7, {"type": "late"})                        # nobody listens any more
        await asyncio.sleep(0.05)
        return ev, q.empty(), bus._subs
    assert asyncio.run(main()) == ({"type": "job"}, True, {})


def test_spotify_callback_links_the_starting_user(admin, monkeypatch):
    from crossfade.api import services as services_api

    class FakeSpotify:
        def whoami(self):
            return "Me on Spotify"
    monkeypatch.setattr(services_api.sp, "exchange_code", lambda *a, **k: {"access_token": "t"})
    monkeypatch.setattr(services_api, "make_provider", lambda *a, **k: FakeSpotify())
    admin_id = admin.get("/api/auth/state").json()["user"]["id"]
    state = services_api._put_flow({"user_id": admin_id, "verifier": "v", "kind": "spotify"})
    r = admin.get(f"/api/services/spotify/callback?state={state}&code=abc", follow_redirects=False)
    assert r.headers["location"] == "/services?connected=spotify"
    assert get_db().val("SELECT display_name FROM accounts WHERE user_id=? AND service='spotify'",
                        (admin_id,)) == "Me on Spotify"


def test_refreshed_tokens_dont_undo_a_disconnect(admin):
    from crossfade.providers import save_account_credentials
    admin_id = admin.get("/api/auth/state").json()["user"]["id"]
    connect_fake(admin_id, "spotify")
    acc = get_db().val("SELECT id FROM accounts WHERE user_id=? AND service='spotify'", (admin_id,))
    assert admin.delete("/api/services/spotify", headers=H).status_code == 200
    save_account_credentials(get_db(), acc, {"access_token": "refreshed"})     # a sync still running
    assert get_db().val("SELECT credentials FROM accounts WHERE id=?", (acc,)) is None


def test_another_account_of_the_same_service_starts_fresh(admin):
    from crossfade.api.services import _save_account
    db = get_db()
    admin_id = admin.get("/api/auth/state").json()["user"]["id"]
    _save_account(db, admin_id, "spotify", {"t": 1}, "First account")
    acc = db.val("SELECT id FROM accounts WHERE user_id=? AND service='spotify'", (admin_id,))
    lid = db.run("INSERT INTO lines(user_id,name,color,created_at,updated_at) VALUES(?,?,?,?,?)",
                 (admin_id, "Fresh", "#0072CE", time.time(), time.time()))
    mid = db.run("INSERT INTO line_members(line_id,account_id,kind,collection_name,role,initialized) "
                 "VALUES(?,?,'liked','Liked Songs','both',1)", (lid, acc))
    sid = db.run("INSERT INTO songs(title,artist,created_at) VALUES('A','B',?)", (time.time(),))
    db.run("INSERT INTO member_items(member_id,song_id,ref_id,confirmed) VALUES(?,?,'x',1)", (mid, sid))
    _save_account(db, admin_id, "spotify", {"t": 2}, "First account")         # same account again: kept
    assert db.val("SELECT COUNT(*) FROM member_items WHERE member_id=?", (mid,)) == 1
    _save_account(db, admin_id, "spotify", {"t": 3}, "Someone else")          # another account: forgotten
    assert db.val("SELECT COUNT(*) FROM member_items WHERE member_id=?", (mid,)) == 0
    assert db.val("SELECT initialized FROM line_members WHERE id=?", (mid,)) == 0
    db.run("DELETE FROM lines WHERE id=?", (lid,))


def test_versions_only_for_songs_of_the_fade(admin):
    admin_id = admin.get("/api/auth/state").json()["user"]["id"]
    connect_fake(admin_id, "ytm")
    connect_fake(admin_id, "apple")
    r = admin.post("/api/lines", json={"name": "Versions", "members": [
        {"service": "ytm", "kind": "liked", "collection_name": "Likes"},
        {"service": "apple", "kind": "liked", "collection_name": "Favourites"}]}, headers=H)
    lid, mid = r.json()["id"], r.json()["members"][0]["id"]
    for path in ("candidates", "replace"):
        r = admin.post(f"/api/lines/{lid}/songs/999999/{path}", json={"member_id": mid, "target": "x"}, headers=H)
        assert r.status_code == 404, (path, r.text)
    assert admin.delete(f"/api/lines/{lid}", headers=H).status_code == 200


def test_new_fade_keeps_its_deletion_settings(admin):
    """Each deletion setting lands in its own column when a fade is created."""
    body = {"name": "Guard", "delete_confirm": False, "delete_guard_min": 7, "delete_guard_ratio": 0.3,
            "members": [{"service": "ytm", "kind": "liked", "collection_name": "Likes", "role": "both"}]}
    r = admin.post("/api/lines", json=body, headers=H)
    assert r.status_code == 200, r.text
    row = get_db().one("SELECT delete_confirm, delete_guard_min, delete_guard_ratio FROM lines WHERE id=?",
                       (r.json()["id"],))
    assert (row["delete_confirm"], row["delete_guard_min"], row["delete_guard_ratio"]) == (0, 7, 0.3)
