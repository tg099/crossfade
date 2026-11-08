"""Friends and shared fades: nothing is visible before the friend confirms with their own collection."""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("CROSSFADE_DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("CROSSFADE_BASE_URL", "https://music.example.test")

import time  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from crossfade.api.app import app  # noqa: E402
from crossfade.api.deps import SESSION_COOKIE  # noqa: E402
from crossfade.db import get_db  # noqa: E402
from crossfade.security import encrypt, hash_password, new_token, token_hash  # noqa: E402

H = {"X-Crossfade": "1"}


def person(username: str, name: str) -> tuple[TestClient, int]:
    """A signed-in user with fake YouTube Music and Apple Music accounts."""
    db = get_db()
    now = time.time()
    uid = db.run("INSERT INTO users(username,display_name,password_hash,created_at) VALUES(?,?,?,?)",
                 (username, name, hash_password("secret-secret-1"), now))
    for svc in ("ytm", "apple"):
        db.run("INSERT INTO accounts(user_id,service,display_name,credentials,created_at,updated_at) "
               "VALUES(?,?,?,?,?,?)", (uid, svc, "Fake", encrypt({"x": 1}), now, now))
    tok = new_token()
    db.run("INSERT INTO sessions(token_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)",
           (token_hash(tok), uid, now, now + 86400))
    c = TestClient(app, base_url="https://music.example.test")
    c.cookies.set(SESSION_COOKIE, tok)
    return c, uid


@pytest.fixture(scope="module")
def trio():
    suffix = str(int(time.time() * 1000) % 100000)
    return (person(f"anna{suffix}", "Anna"), person(f"ben{suffix}", "Ben"), person(f"cleo{suffix}", "Cleo"))


def fade(client: TestClient, name="Roadtrip", members=None) -> dict:
    members = members or [{"service": "ytm", "kind": "playlist", "collection_id": "PLa", "collection_name": "Trip",
                           "role": "both"}]
    r = client.post("/api/lines", json={"name": name, "color": "#0072CE", "members": members}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def befriend(a: TestClient, b: TestClient, b_username: str):
    r = a.post("/api/friends", json={"username": b_username}, headers=H)
    assert r.status_code == 200, r.text
    req = b.get("/api/friends").json()["incoming"][0]
    assert b.post(f"/api/friends/requests/{req['id']}/accept", headers=H).status_code == 200


def test_friend_request_needs_confirmation(trio):
    (anna, anna_id), (ben, ben_id), _ = trio
    ben_name = get_db().val("SELECT username FROM users WHERE id=?", (ben_id,))
    assert anna.post("/api/friends", json={"username": "nosuchuser"}, headers=H).status_code == 404
    r = anna.post("/api/friends", json={"username": "@" + ben_name.upper()}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["outgoing"][0]["user"]["id"] == ben_id and r.json()["friends"] == []
    assert anna.post("/api/friends", json={"username": ben_name}, headers=H).status_code == 409
    # not friends yet: no invite possible
    f = fade(anna)
    assert anna.post(f"/api/lines/{f['id']}/invites", json={"user_id": ben_id}, headers=H).status_code == 403
    incoming = ben.get("/api/friends").json()["incoming"]
    assert [i["user"]["id"] for i in incoming] == [anna_id]
    # only the addressee accepts
    out_id = anna.get("/api/friends").json()["outgoing"][0]["id"]
    assert anna.post(f"/api/friends/requests/{out_id}/accept", headers=H).status_code == 403
    assert ben.post(f"/api/friends/requests/{incoming[0]['id']}/accept", headers=H).status_code == 200
    friends = anna.get("/api/friends").json()["friends"]
    assert [x["id"] for x in friends] == [ben_id]
    # a friend learns names, nothing else
    assert set(friends[0]) == {"id", "username", "display_name", "since", "shared"}


def test_invite_reveals_nothing_until_joined(trio):
    (anna, anna_id), (ben, ben_id), _ = trio
    f = fade(anna, "Summer")
    # Ben has a fade of his own that Anna must never see
    own = fade(ben, "Ben's secret", [{"service": "apple", "kind": "liked", "collection_name": "Favourites",
                                        "role": "both"}])
    r = anna.post(f"/api/lines/{f['id']}/invites", json={"user_id": ben_id}, headers=H)
    assert r.status_code == 200, r.text
    assert [i["user"]["id"] for i in r.json()["invites"]] == [ben_id]
    assert anna.post(f"/api/lines/{f['id']}/invites", json={"user_id": ben_id}, headers=H).status_code == 409

    # Ben sees the invite: name and look of the fade and who asks – no collections, no songs
    inv = next(i for i in ben.get("/api/friends").json()["invites"] if i["fade"]["name"] == "Summer")
    assert set(inv["fade"]) == {"name", "icon", "color"} and inv["from"]["id"] == anna_id
    assert ben.get(f"/api/lines/{f['id']}").status_code == 404
    assert ben.get(f"/api/lines/{f['id']}/songs").status_code == 404
    assert ben.get(f"/api/lines/{f['id']}/changes").status_code == 404
    assert f["id"] not in [x["id"] for x in ben.get("/api/lines").json()]
    # and Anna sees nothing of Ben's
    assert anna.get(f"/api/lines/{own['id']}").status_code == 404
    assert own["id"] not in [x["id"] for x in anna.get("/api/lines").json()]

    # Ben joins with a collection of his own choice
    r = ben.post(f"/api/invites/{inv['id']}/accept", json={"service": "apple", "kind": "playlist",
                 "collection_id": "pl.ben", "collection_name": "Ben's summer", "role": "send"}, headers=H)
    assert r.status_code == 200, r.text
    joined = r.json()
    assert joined["mine"] is False and joined["owner"]["id"] == anna_id and joined["invites"] == []
    assert [(m["collection_name"], m["mine"]) for m in joined["members"]] == [("Trip", False), ("Ben's summer", True)]

    view = anna.get(f"/api/lines/{f['id']}").json()
    assert view["mine"] is True
    assert [(m["user_name"], m["mine"]) for m in view["members"]] == [("Anna", True), ("Ben", False)]
    # joining one fade does not open the others
    assert anna.get(f"/api/lines/{own['id']}").status_code == 404


def test_friends_other_fades_stay_hidden(trio):
    """Ben's collection in Anna's fade is also in his own fade: Anna must not learn that fade's name."""
    (anna, _), (ben, ben_id), _ = trio
    f = fade(anna, "Autumn")
    fade(ben, "Ben's night music", [{"service": "ytm", "kind": "liked", "collection_name": "Likes", "role": "both"}])
    anna.post(f"/api/lines/{f['id']}/invites", json={"user_id": ben_id}, headers=H)
    inv = next(i for i in ben.get("/api/friends").json()["invites"] if i["fade"]["name"] == "Autumn")
    assert ben.post(f"/api/invites/{inv['id']}/accept", json={"service": "ytm", "kind": "liked",
                    "collection_name": "Likes", "role": "both"}, headers=H).status_code == 200
    assert "Ben's night music" not in anna.get(f"/api/lines/{f['id']}").text
    assert "Ben's night music" not in anna.get("/api/lines").text
    # Ben himself is warned about his own collection
    assert any(w["kind"] == "interchange" for w in ben.get(f"/api/lines/{f['id']}").json()["warnings"])
    ben.delete(f"/api/lines/{f['id']}/membership", headers=H)


def test_rights_in_a_shared_fade(trio):
    (anna, anna_id), (ben, ben_id), _ = trio
    f = next(x for x in ben.get("/api/lines").json() if x["name"] == "Summer")
    lid = f["id"]
    ben_member = next(m for m in f["members"] if m["mine"])
    anna_member = next(m for m in f["members"] if not m["mine"])
    # only the owner edits, pauses, deletes or invites
    body = {"name": "Hijacked", "color": "#0072CE", "members": [
        {"service": "ytm", "kind": "liked", "collection_name": "Likes", "role": "both"}]}
    assert ben.put(f"/api/lines/{lid}", json=body, headers=H).status_code == 403
    assert ben.put(f"/api/lines/{lid}/paused", json={"paused": True}, headers=H).status_code == 403
    assert ben.delete(f"/api/lines/{lid}", headers=H).status_code == 403
    # everyone sets the direction of their own collection only
    r = ben.put(f"/api/lines/{lid}/members/{ben_member['id']}", json={"role": "both"}, headers=H)
    assert r.status_code == 200 and next(m for m in r.json()["members"] if m["mine"])["role"] == "both"
    assert ben.put(f"/api/lines/{lid}/members/{anna_member['id']}", json={"role": "receive"},
                   headers=H).status_code == 403
    # the owner's editor keeps the friend's collection
    body = {"name": "Summer 26", "color": "#0072CE", "members": [
        {"service": "ytm", "kind": "playlist", "collection_id": "PLa", "collection_name": "Trip", "role": "both"},
        {"service": "apple", "kind": "liked", "collection_name": "Favourites", "role": "receive"}]}
    r = anna.put(f"/api/lines/{lid}", json=body, headers=H)
    assert r.status_code == 200, r.text
    assert [m["collection_name"] for m in r.json()["members"]] == ["Trip", "Favourites", "Ben's summer"]
    # held deletions are the owner's call
    assert ben.post(f"/api/lines/{lid}/run", json={"allow_deletions": True}, headers=H).status_code == 403


def test_leave_and_unfriend(trio):
    (anna, anna_id), (ben, ben_id), (cleo, cleo_id) = trio
    lid = next(x for x in ben.get("/api/lines").json() if x["name"] == "Summer 26")["id"]
    assert anna.delete(f"/api/lines/{lid}/membership", headers=H).status_code == 400
    assert ben.delete(f"/api/lines/{lid}/membership", headers=H).status_code == 200
    assert ben.get(f"/api/lines/{lid}").status_code == 404
    assert all(m["mine"] for m in anna.get(f"/api/lines/{lid}").json()["members"])

    # an open invite disappears when the friendship ends; Cleo was never part of anything
    anna.post(f"/api/lines/{lid}/invites", json={"user_id": ben_id}, headers=H)
    assert ben.get("/api/friends").json()["invites"]
    assert cleo.post(f"/api/lines/{lid}/invites", json={"user_id": ben_id}, headers=H).status_code == 404
    assert anna.delete(f"/api/friends/{ben_id}", headers=H).status_code == 200
    assert ben.get("/api/friends").json() == {"friends": [], "incoming": [], "outgoing": [], "invites": []}
    assert anna.post(f"/api/lines/{lid}/invites", json={"user_id": ben_id}, headers=H).status_code == 403


def test_unfriend_removes_shared_collections(trio):
    (anna, anna_id), _, (cleo, cleo_id) = trio
    cleo_name = get_db().val("SELECT username FROM users WHERE id=?", (cleo_id,))
    befriend(anna, cleo, cleo_name)
    f = fade(anna, "Party")
    anna.post(f"/api/lines/{f['id']}/invites", json={"user_id": cleo_id}, headers=H)
    inv = cleo.get("/api/friends").json()["invites"][0]
    r = cleo.post(f"/api/invites/{inv['id']}/accept", json={"service": "ytm", "kind": "liked",
                  "collection_name": "Likes", "role": "receive"}, headers=H)
    assert r.status_code == 200, r.text
    assert anna.get("/api/friends").json()["friends"][0]["shared"] == 1
    assert cleo.delete(f"/api/friends/{anna_id}", headers=H).status_code == 200
    assert cleo.get(f"/api/lines/{f['id']}").status_code == 404
    assert [m["user_name"] for m in anna.get(f"/api/lines/{f['id']}").json()["members"]] == ["Anna"]


def test_cover_preview_only_for_visible_songs(trio):
    (anna, anna_id), (ben, _), _ = trio
    db = get_db()
    now = time.time()
    f = fade(anna, "Preview")
    sid = db.run("INSERT INTO songs(title,artist,created_at) VALUES('Sample','X',?)", (now,))
    # a library-only Apple id has no sample; the YouTube audio track does
    db.run("INSERT INTO song_refs(service,ref_id,song_id,title,artist) VALUES('apple','i.AbC',?,'Sample','X')", (sid,))
    db.run("INSERT INTO song_refs(service,ref_id,song_id,title,artist,video_type) "
           "VALUES('ytm','abcdefghijk',?,'Sample','X','MUSIC_VIDEO_TYPE_ATV')", (sid,))
    db.run("INSERT INTO line_items(line_id,song_id,added_at) VALUES(?,?,?)", (f["id"], sid, now))
    r = anna.get(f"/api/preview/song/{sid}")
    assert r.status_code == 200 and r.json()["service"] == "ytm" and r.json()["id"] == "abcdefghijk"
    assert ben.get(f"/api/preview/song/{sid}").status_code == 404      # not his fade, not his library


def test_friends_board_counts_brought_songs_and_respects_opt_out():
    suffix = str(int(time.time() * 1000) % 100000)
    (dana, dana_id), (emil, emil_id) = person(f"dana{suffix}", "Dana"), person(f"emil{suffix}", "Emil")
    assert dana.get("/api/friends/board").json()["people"] == []          # no friends yet: no ranking
    befriend(dana, emil, f"emil{suffix}")
    f = fade(dana, "Board", [
        {"service": "ytm", "kind": "liked", "collection_name": "Likes", "role": "both"},
        {"service": "apple", "kind": "liked", "collection_name": "Favourites", "role": "both"}])
    ytm, apple = [m["id"] for m in f["members"]]
    db, now = get_db(), time.time()
    sids = [db.run("INSERT INTO songs(title,artist,created_at) VALUES(?,?,?)", (f"B{i}", "A", now)) for i in range(3)]
    for sid in sids:
        db.run("INSERT INTO member_items(member_id,song_id,confirmed) VALUES(?,?,1)", (ytm, sid))
        db.run("INSERT INTO member_items(member_id,song_id,confirmed) VALUES(?,?,1)", (apple, sid))
    # two came from YouTube Music into Apple Music, one went the other way but long ago
    for sid, member, ts in ((sids[0], apple, now - 60), (sids[1], apple, now - 60), (sids[2], ytm, now - 30 * 86400)):
        db.run("INSERT INTO changes(line_id,member_id,song_id,action,label,ok,ts) VALUES(?,?,?,?,?,?,?)",
               (f["id"], member, sid, "add", "x", 1, ts))
    people = dana.get("/api/friends/board").json()["people"]
    assert [(p["id"], p["songs"], p["me"]) for p in people] == [(dana_id, 2, True), (emil_id, 0, False)]
    assert set(people[0]) == {"id", "username", "display_name", "songs", "me"}   # a number, nothing else
    # Emil opts out: he vanishes for Dana, and sees no ranking himself
    assert emil.put("/api/auth/me", json={"share_stats": False}, headers=H).json()["user"]["share_stats"] is False
    assert dana.get("/api/friends/board").json()["people"] == []
    assert emil.get("/api/friends/board").json() == {"enabled": False, "days": 7, "people": []}
