"""MCP protocol, isolation, scopes, approvals and real worker playlist execution with fake services."""
from __future__ import annotations

import importlib
import json
import os
import tempfile
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

os.environ.setdefault("CROSSFADE_DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("CROSSFADE_BASE_URL", "https://music.example.test")

from test_catalog import CatalogFake  # noqa: E402

from crossfade import db as database
from crossfade import history, mcp_actions, music
from crossfade import worker as workers
from crossfade.api.app import app
from crossfade.config import config
from crossfade.db import Database
from crossfade.identity import Identity, Match
from crossfade.mcp_access import authenticate, issue_token
from crossfade.models import LIKED, PLAYLIST, YTM, Collection
from crossfade.security import encrypt, new_token, token_hash


class MoodProvider(CatalogFake):
    def __init__(self):
        super().__init__(YTM)
        self.created = []

    def create_playlist(self, name, description=""):
        cid = f"mood-{len(self.created)}"
        self.created.append((cid, name))
        self.cols[(PLAYLIST, cid)] = {}
        return Collection(PLAYLIST, cid, name, 0)


@pytest.fixture(scope="module")
def host(tmp_path_factory):
    """One server lifespan (the SDK manager is intentionally single-use), with an isolated database."""
    db = Database(tmp_path_factory.mktemp("mcp") / "mcp.db")
    patch = pytest.MonkeyPatch()
    patch.setattr(database, "_db", db)
    worker = workers.Worker(db)
    patch.setattr(workers, "_worker", worker)
    app_module = importlib.import_module("crossfade.api.app")
    patch.setattr(app_module, "config", replace(config, scheduler=False))
    providers = {}

    def provider(_db, account):
        return providers[account["id"]]

    patch.setattr(workers, "build_provider", provider)
    patch.setattr("crossfade.api.lines.build_provider", provider)
    with TestClient(app, base_url=config.base_url) as client:
        yield SimpleNamespace(db=db, client=client, providers=providers, worker=worker)
    worker.pool.shutdown(wait=True)
    patch.undo()


def person(host, name, scopes=None):
    db = host.db
    name += new_token()[:8]
    uid = db.run("INSERT INTO users(username,display_name,password_hash,created_at) VALUES(?,?,?,?)",
                 (name, name, "x", time.time()))
    aid = db.run("INSERT INTO accounts(user_id,service,display_name,credentials,created_at,updated_at) "
                 "VALUES(?,?,?,?,?,?)", (uid, YTM, name, encrypt({"private": "must-not-leak"}), 0, 0))
    provider = MoodProvider()
    provider.put(LIKED, "", "lucky", "blind", "hello", "haus")
    host.providers[aid] = provider
    from crossfade.catalog import Catalog
    Catalog(db, uid, {aid: provider}).scan()
    sid = {r["title"]: r["id"] for r in db.all("SELECT id,title FROM songs")}
    token = issue_token(db, uid, "Assistant " + name, scopes or ["read", "sync", "manage"], 30)
    cookie = new_token()
    db.run("INSERT INTO sessions(token_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)",
           (token_hash(cookie), uid, time.time(), time.time() + 86400))
    return SimpleNamespace(host=host, db=db, client=host.client, uid=uid, aid=aid, provider=provider,
                           ids=sid, token=token, browser={"Cookie": "cf_session=" + cookie, "X-Crossfade": "1"})


@pytest.fixture
def env(host):
    return person(host, "MCP")


def rpc(env, method, params=None, token=None, path="/mcp", headers=None):
    return env.client.post(path, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
                           headers={"Authorization": "Bearer " + (token or env.token["token"]),
                                    "Accept": "application/json, text/event-stream", **(headers or {})})


def call(env, name, arguments=None, token=None, error=False):
    response = rpc(env, "tools/call", {"name": name, "arguments": arguments or {}}, token=token)
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert bool(result.get("isError")) == error, result
    if error:
        return result["content"][0]["text"]
    return result.get("structuredContent") or json.loads(result["content"][0]["text"])


def annotate(env, song_id, mood="relaxed", confidence=0.9, **extra):
    return call(env, "annotate_songs", {"annotations": [{"song_id": song_id, "moods": [mood],
                                                         "confidence": confidence,
                                                         "evidence": "Test classification of known music.", **extra}]})


def wait_action(env, action_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        action = call(env, "get_action", {"action_id": action_id})
        if action["state"] not in ("executing", "queued"):
            return action
        time.sleep(0.01)
    pytest.fail("Background action did not finish")


def test_real_protocol_and_structured_tools(env):
    result = rpc(env, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                     "clientInfo": {"name": "test", "version": "1"}}).json()["result"]
    assert result["serverInfo"]["name"] == "Crossfade"
    tools = rpc(env, "tools/list").json()["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"prepare_playlist", "search_library", "prepare_restore", "prepare_sync", "annotate_songs"} <= names
    assert all("ctx" not in t["inputSchema"].get("properties", {}) for t in tools)
    assert all(t.get("outputSchema") for t in tools)
    assert rpc(env, "tools/list", path="/mcp/").status_code == 200
    prompts = rpc(env, "prompts/list").json()["result"]["prompts"]
    assert any(p["name"] == "mood_playlist" for p in prompts)


def test_cookies_never_authenticate_mcp_and_headers_never_authenticate_browser(env):
    assert env.client.post("/mcp", json={}, headers=env.browser).status_code == 401
    assert env.client.post("/mcp?token=" + env.token["token"], json={}).status_code == 401
    assert env.client.get("/api/mcp", headers={"Authorization": "Bearer " + env.token["token"]}).status_code == 401
    assert env.client.post("/api/mcp/tokens", json={"name": "Bad"},
                           headers={"Cookie": env.browser["Cookie"]}).status_code == 403


@pytest.mark.parametrize("headers,code", [({"Origin": "https://evil.example"}, 403),
                                          ({"Host": "evil.example"}, 421)])
def test_transport_rejects_untrusted_origin_and_host(env, headers, code):
    assert rpc(env, "tools/list", headers=headers).status_code == code


def test_only_hashed_keys_are_stored_and_secrets_are_not_exposed(env):
    row = env.db.one("SELECT * FROM mcp_tokens WHERE id=?", (env.token["id"],))
    assert row["token_hash"] == token_hash(env.token["token"])
    overview = env.client.get("/api/mcp", headers=env.browser)
    assert overview.status_code == 200
    assert env.token["token"] not in overview.text and "token_hash" not in overview.text
    status = call(env, "status")
    text = json.dumps(status)
    assert "must-not-leak" not in text and "credentials" not in text and "password_hash" not in text


def test_read_permission_cannot_modify_or_start_sync(env):
    token = issue_token(env.db, env.uid, "Read only", ["read"], 1)["token"]
    assert call(env, "search_library", token=token)["total"] == 4
    assert "manage" in call(env, "refresh_library", token=token, error=True)
    assert "sync" in call(env, "preview_sync", {"fade_id": 123}, token=token, error=True)
    assert "manage" in call(env, "annotate_songs", {"annotations": [{"song_id": env.ids["Hello"],
                               "confidence": 1, "evidence": "test"}]}, token=token, error=True)


def test_revoke_and_expiry_are_enforced_on_every_message(env):
    token = issue_token(env.db, env.uid, "Revocable", ["read"], 1)
    assert rpc(env, "tools/list", token=token["token"]).status_code == 200
    assert env.client.delete(f"/api/mcp/tokens/{token['id']}", headers=env.browser).status_code == 200
    assert rpc(env, "tools/list", token=token["token"]).status_code == 401
    env.db.run("UPDATE mcp_tokens SET expires_at=0 WHERE id=?", (env.token["id"],))
    assert rpc(env, "tools/list").status_code == 401


def test_must_change_password_disables_existing_keys(env):
    env.db.run("UPDATE users SET must_change_password=1 WHERE id=?", (env.uid,))
    assert rpc(env, "tools/list").status_code == 401
    assert env.client.post("/api/mcp/tokens", json={"name": "Blocked"}, headers=env.browser).status_code == 403


def test_library_pagination_comparison_and_personal_isolation(env, host):
    other = person(host, "Other")
    unique = env.db.run("INSERT INTO songs(title,artist,created_at) VALUES('Private Song','Other',0)")
    source = env.db.val("SELECT id FROM catalog_sources WHERE account_id=?", (other.aid,))
    env.db.run("INSERT INTO catalog_items(source_id,ref_id,song_id,first_seen) VALUES(?,?,?,0)",
               (source, "private-ref", unique))
    page = call(env, "search_library", {"limit": 2})
    assert page["total"] == 4 and page["next_offset"] == 2
    next_page = call(env, "search_library", {"limit": 2, "offset": 2})
    assert next_page["next_offset"] is None
    assert not {s["id"] for s in page["songs"]} & {s["id"] for s in next_page["songs"]}
    assert call(env, "search_library", {"missing_on": "spotify"})["total"] == 4
    assert call(env, "search_library", {"unassigned": True})["total"] == 4
    call(env, "search_library", {"source_id": source}, error=True)
    call(env, "get_song", {"song_id": unique}, error=True)
    call(env, "annotate_songs", {"annotations": [{"song_id": unique, "confidence": 1,
                                                  "evidence": "must not write"}]}, error=True)
    assert not env.db.val("SELECT 1 FROM music_annotations WHERE user_id=? AND song_id=?", (env.uid, unique))


def test_personal_corrections_are_authoritative_and_private(env, host):
    sid = env.ids["Hello"]
    annotate(env, sid)
    correction = {"song_id": sid, "moods": ["MELANCHOLIC"], "genres": ["Pop"],
                  "confidence": 1, "evidence": "My own interpretation"}
    assert env.client.put(f"/api/mcp/annotations/{sid}", json=correction, headers=env.browser).status_code == 200
    assert annotate(env, sid)["protected_corrections"] == [sid]
    song = call(env, "search_library", {"mood": "melancholic"})["songs"][0]
    assert song["annotation"]["moods"] == ["melancholic"] and song["origin"] == "user"
    other = person(host, "Other")
    assert call(other, "search_library", {"mood": "melancholic"})["total"] == 0
    assert env.client.delete(f"/api/mcp/annotations/{sid}", headers=env.browser).status_code == 200
    assert annotate(env, sid)["saved"] == [sid]


def test_mood_playlist_preview_apply_worker_result_and_idempotence(env):
    sid = env.ids["Hello"]
    annotate(env, sid, "melancholic")
    draft = call(env, "prepare_playlist", {"request": {"name": "Rainy evening", "account_id": env.aid,
                                                       "mood": "melancholic", "duration_minutes": 60}})
    assert draft["state"] == "pending" and draft["preview"]["count"] == 1
    assert draft["preview"]["songs"][0]["id"] == sid
    assert not env.provider.created
    call(env, "apply_action", {"action_id": draft["id"]})
    done = wait_action(env, draft["id"])
    assert done["state"] == "ok", done
    assert done["result"]["added"] == 1
    assert env.provider.keys(PLAYLIST, env.provider.created[0][0]) == ["hello"]
    call(env, "apply_action", {"action_id": draft["id"]})
    assert len(env.provider.created) == 1


def test_free_form_mood_limits_exclusions_and_unknown_duration(env):
    ids = env.ids
    # A display title is not used for automatic mood classification.
    call(env, "prepare_playlist", {"request": {"name": "No guessing", "account_id": env.aid,
                                               "mood": "Hello"}}, error=True)
    env.db.run("UPDATE songs SET duration=NULL WHERE id=?", (ids["Get Lucky"],))
    selections = [{"song_id": sid, "reason": "Known music, selected for this mood", "confidence": confidence}
                  for sid, confidence in [(ids["Get Lucky"], .9), (ids["Hello"], .9),
                                          (ids["Haus am See"], .3), (ids["Blinding Lights"], .9)]]
    draft = call(env, "prepare_playlist", {"request": {"name": "Calm but not sad", "account_id": env.aid,
                 "mood": "calm but not sad", "duration_minutes": 6, "selections": selections,
                 "exclude_song_ids": [ids["Blinding Lights"]]}})
    assert [s["id"] for s in draft["preview"]["songs"]] == [ids["Hello"]]
    assert {s["reason"] for s in draft["preview"]["skipped"]} == {"unknown_duration", "uncertain", "excluded"}
    assert draft["preview"]["known_duration_seconds"] <= 360


def test_empty_correction_excludes_song_even_from_explicit_mood_selection(env):
    sid = env.ids["Hello"]
    correction = {"song_id": sid, "moods": [], "confidence": 1, "evidence": "No mood playlists"}
    env.client.put(f"/api/mcp/annotations/{sid}", json=correction, headers=env.browser)
    call(env, "prepare_playlist", {"request": {"name": "Mood", "account_id": env.aid, "mood": "relaxed",
                 "selections": [{"song_id": sid, "reason": "Guess", "confidence": 1}]}}, error=True)
    assert not env.provider.created


def test_free_form_mood_respects_positive_corrections(env):
    sid = env.ids["Hello"]
    env.client.put(f"/api/mcp/annotations/{sid}", json={"song_id": sid, "moods": ["melancholic"],
                  "confidence": 1, "evidence": "My classification"}, headers=env.browser)
    draft = call(env, "prepare_playlist", {"request": {"name": "Rainy night", "account_id": env.aid,
                 "mood": "for a rainy night", "selections": [{"song_id": sid, "matched_moods": ["melancholic"],
                 "reason": "Your melancholic tag fits a rainy night", "confidence": .8}]}})
    assert draft["preview"]["count"] == 1


def test_destructive_actions_require_browser_approval_and_are_isolated(env, host):
    source = env.db.val("SELECT id FROM catalog_sources WHERE account_id=?", (env.aid,))
    sid = env.ids["Hello"]
    draft = call(env, "prepare_song_removal", {"selection": {"song_ids": [sid], "source_ids": [source]}})
    assert draft["requires_browser_confirmation"]
    assert "Confirm" in call(env, "apply_action", {"action_id": draft["id"]}, error=True)
    other = person(host, "Other")
    call(other, "get_action", {"action_id": draft["id"]}, error=True)
    assert env.client.post(f"/api/mcp/actions/{draft['id']}/apply", headers=other.browser).status_code == 404
    assert "hello" in env.provider.keys()
    response = env.client.post(f"/api/mcp/actions/{draft['id']}/apply", headers=env.browser)
    assert response.status_code == 200, response.text
    assert wait_action(env, draft["id"])["state"] == "ok"
    assert "hello" not in env.provider.keys()


def test_read_connection_cannot_cancel_manage_proposal(env):
    sid = env.ids["Hello"]
    draft = call(env, "prepare_playlist", {"request": {"name": "Test", "account_id": env.aid,
                 "selections": [{"song_id": sid, "reason": "Explicit selection", "confidence": 1}]}})
    readonly = issue_token(env.db, env.uid, "Reader", ["read"], 1)
    call(env, "cancel_action", {"action_id": draft["id"]}, token=readonly["token"], error=True)
    call(env, "apply_action", {"action_id": draft["id"]}, token=readonly["token"], error=True)
    assert call(env, "cancel_action", {"action_id": draft["id"]})["state"] == "cancelled"
    assert call(env, "apply_action", {"action_id": draft["id"]})["state"] == "cancelled"


def test_revoked_and_expired_previews_cannot_execute(env):
    sid = env.ids["Hello"]
    draft = call(env, "prepare_playlist", {"request": {"name": "Test", "account_id": env.aid,
                 "selections": [{"song_id": sid, "reason": "Explicit selection", "confidence": 1}]}})
    env.db.run("UPDATE mcp_actions SET expires_at=0 WHERE id=?", (draft["id"],))
    call(env, "apply_action", {"action_id": draft["id"]}, error=True)
    assert not env.provider.created
    env.db.run("UPDATE mcp_actions SET expires_at=? WHERE id=?", (time.time() + 60, draft["id"]))
    env.client.delete(f"/api/mcp/tokens/{env.token['id']}", headers=env.browser)
    response = env.client.post(f"/api/mcp/actions/{draft['id']}/apply", headers=env.browser)
    assert response.json()["state"] == "cancelled" and not env.provider.created


def test_restore_uses_own_snapshots_and_requires_browser(env, host):
    history.capture(env.db, env.aid, LIKED, "", ["ytm_hello"], "scan")
    snap = env.db.val("SELECT id FROM snapshots WHERE account_id=? ORDER BY id DESC LIMIT 1", (env.aid,))
    draft = call(env, "prepare_restore", {"snapshot_id": snap, "mode": "exact"})
    assert draft["requires_browser_confirmation"]
    call(env, "apply_action", {"action_id": draft["id"]}, error=True)
    other = person(host, "Other")
    call(other, "history_snapshot", {"snapshot_id": snap}, error=True)


def test_annotations_survive_song_identity_merge(env):
    keep = env.ids["Hello"]
    drop = env.db.run("INSERT INTO songs(title,artist,created_at) VALUES('Hello copy','Adele',0)")
    env.db.run("INSERT INTO music_annotations(user_id,song_id,data,origin,updated_at) VALUES(?,?,?,'user',0)",
               (env.uid, drop, json.dumps({"moods": ["melancholic"]})))
    annotate(env, keep, "relaxed")
    Identity(env.db).merge(keep, drop)
    row = env.db.one("SELECT * FROM music_annotations WHERE user_id=? AND song_id=?", (env.uid, keep))
    assert row["origin"] == "user" and json.loads(row["data"])["moods"] == ["melancholic"]


def test_annotation_batch_is_atomic_for_foreign_song_ids(env):
    principal = authenticate(env.db, "Bearer " + env.token["token"])
    with pytest.raises(HTTPException):
        music.save_annotations(env.db, principal["id"], [
            music.Annotation(song_id=env.ids["Hello"], confidence=.8, evidence="Test", moods=["relaxed"]),
            music.Annotation(song_id=999999, confidence=.8, evidence="Test", moods=["relaxed"])])
    assert not env.db.val("SELECT 1 FROM music_annotations WHERE user_id=?", (env.uid,))


def test_audit_records_action_names_without_token_or_music_arguments(env):
    call(env, "search_library", {"query": "Adele"})
    entries = env.client.get("/api/mcp", headers=env.browser).json()["audit"]
    assert entries[0]["tool"] == "search_library" and entries[0]["status"] == "ok"
    assert env.token["token"] not in json.dumps(entries) and "Adele" not in json.dumps(entries)


def test_annotation_validation_and_tool_argument_limits(env):
    call(env, "search_library", {"limit": 10000}, error=True)
    call(env, "annotate_songs", {"annotations": [{"song_id": env.ids["Hello"], "confidence": 2,
                                                  "evidence": "invalid"}]}, error=True)
    call(env, "annotate_songs", {"annotations": [{"song_id": env.ids["Hello"], "confidence": 1,
                                                  "evidence": "   "}]}, error=True)


def test_resuming_deletion_enabled_fade_requires_approval(env):
    principal = authenticate(env.db, "Bearer " + env.token["token"])
    fade = env.db.run("INSERT INTO lines(user_id,name,color,created_at,updated_at,paused) VALUES(?,?,?,0,0,1)",
                      (env.uid, "Test fade", "#0072CE"))
    action = mcp_actions.prepare(env.db, principal, "fade_pause", {"fade_id": fade, "paused": False})
    assert action["requires_browser_confirmation"]


def test_disabling_mcp_does_not_disable_web_account_management(env, monkeypatch):
    module = importlib.import_module("crossfade.mcp_server")
    monkeypatch.setattr(module, "config", replace(config, mcp_enabled=False))
    assert rpc(env, "tools/list").status_code == 404
    assert env.client.get("/api/mcp", headers=env.browser).status_code == 200


def test_new_corrections_invalidate_an_old_mood_preview(env):
    sid = env.ids["Hello"]
    annotate(env, sid)
    draft = call(env, "prepare_playlist", {"request": {"name": "Relaxed", "account_id": env.aid, "mood": "relaxed"}})
    env.client.put(f"/api/mcp/annotations/{sid}", json={"song_id": sid, "moods": [], "confidence": 1,
                  "evidence": "Changed my mind"}, headers=env.browser)
    assert "changed" in call(env, "apply_action", {"action_id": draft["id"]}, error=True)
    assert not env.provider.created


def test_changed_fade_settings_cannot_bypass_deletion_approval(env):
    principal = authenticate(env.db, "Bearer " + env.token["token"])
    fade = env.db.run("INSERT INTO lines(user_id,name,color,created_at,updated_at,sync_deletions) "
                      "VALUES(?,?,?,0,0,0)", (env.uid, "Test fade", "#0072CE"))
    action = mcp_actions.prepare(env.db, principal, "sync", {"fade_id": fade})
    assert not action["requires_browser_confirmation"]
    env.db.run("UPDATE lines SET sync_deletions=1 WHERE id=?", (fade,))
    assert "changed" in call(env, "apply_action", {"action_id": action["id"]}, error=True)
    assert env.host.worker.job(fade) is None


def test_restore_rejects_a_changed_snapshot_preview(env):
    snap = env.db.val("SELECT id FROM snapshots WHERE account_id=? ORDER BY id DESC LIMIT 1", (env.aid,))
    draft = call(env, "prepare_restore", {"snapshot_id": snap, "mode": "exact"})
    history.capture(env.db, env.aid, LIKED, "", ["ytm_hello"], "scan")
    r = env.client.post(f"/api/mcp/actions/{draft['id']}/apply", headers=env.browser)
    assert r.status_code == 409 and "changed" in r.json()["detail"]


def test_missing_tracks_are_reported_as_partial_results(env, monkeypatch):
    sid = env.ids["Hello"]
    monkeypatch.setattr(Identity, "find", lambda *a, **kw: Match(None, 0, False, [], []))
    draft = call(env, "prepare_playlist", {"request": {"name": "Unavailable", "account_id": env.aid,
                 "selections": [{"song_id": sid, "reason": "Explicit choice", "confidence": 1}]}})
    call(env, "apply_action", {"action_id": draft["id"]})
    done = wait_action(env, draft["id"])
    assert done["state"] == "partial", done
    assert done["result"]["added"] == 0 and done["result"]["missing"][0]["song_id"] == sid


def test_fade_lifecycle_uses_existing_application_checks(env):
    definition = {"name": "MCP Fade", "paused": True, "interval_minutes": 0, "sync_deletions": False,
                  "members": [{"service": "ytm", "kind": "liked", "collection_name": "Likes", "role": "send"}]}
    draft = call(env, "prepare_fade", {"fade": definition})
    call(env, "apply_action", {"action_id": draft["id"]}, error=True)
    created = env.client.post(f"/api/mcp/actions/{draft['id']}/apply", headers=env.browser).json()
    assert created["state"] == "ok", created
    fade_id = created["result"]["id"]
    assert call(env, "get_fade", {"fade_id": fade_id})["name"] == "MCP Fade"
    call(env, "preview_sync", {"fade_id": fade_id}, error=True)  # only one collection
    assert call(env, "list_issues", {"fade_id": fade_id})["issues"] == []
    assert call(env, "version_audit", {"fade_id": fade_id})["versions"] == []
    assert call(env, "fade_changes", {"fade_id": fade_id})["changes"] == []
    pause = call(env, "prepare_fade_pause", {"fade_id": fade_id, "paused": False})
    assert call(env, "apply_action", {"action_id": pause["id"]})["result"]["paused"] is False
    updated = call(env, "prepare_fade", {"fade_id": fade_id, "fade": {**definition, "name": "Updated"}})
    r = env.client.post(f"/api/mcp/actions/{updated['id']}/apply", headers=env.browser)
    assert r.json()["result"]["name"] == "Updated", r.text
    deleted = call(env, "prepare_fade_deletion", {"fade_id": fade_id})
    assert env.client.post(f"/api/mcp/actions/{deleted['id']}/apply", headers=env.browser).json()["state"] == "ok"
    call(env, "get_fade", {"fade_id": fade_id}, error=True)


def test_missing_service_and_unassigned_filters_intersect_with_pagination(env):
    db = env.db
    source = db.run("INSERT INTO catalog_sources(account_id,kind,collection_id,name) VALUES(?,'playlist','p','Mine')",
                    (env.aid,))
    db.run("INSERT INTO catalog_items(source_id,ref_id,song_id,first_seen) VALUES(?,?,?,0)",
           (source, "ytm_hello", env.ids["Hello"]))
    spotify = db.run("INSERT INTO accounts(user_id,service,created_at,updated_at) VALUES(?,'spotify',0,0)", (env.uid,))
    spotify_source = db.run("INSERT INTO catalog_sources(account_id,kind,name) VALUES(?,'liked','Spotify likes')",
                            (spotify,))
    db.run("INSERT INTO catalog_items(source_id,ref_id,song_id,first_seen) VALUES(?,?,?,0)",
           (spotify_source, "spotify_haus", env.ids["Haus am See"]))
    filters = {"missing_on": "spotify", "unassigned": True, "limit": 1}
    first = call(env, "search_library", filters)
    second = call(env, "search_library", {**filters, "offset": first["next_offset"]})
    assert first["total"] == second["total"] == 2
    assert second["next_offset"] is None
    assert {s["id"] for s in first["songs"] + second["songs"]} == {
        env.ids["Get Lucky"], env.ids["Blinding Lights"]}
    annotate(env, env.ids["Blinding Lights"], "relaxed")
    classified = call(env, "search_library", {**filters, "mood": "relaxed"})
    assert classified["total"] == 1 and classified["songs"][0]["id"] == env.ids["Blinding Lights"]


@pytest.mark.parametrize("length", [1000, 1500])
def test_long_annotation_evidence_can_be_reused_in_playlist_preview(env, length):
    sid, reason = env.ids["Hello"], "x" * length
    annotate(env, sid, evidence=reason)
    draft = call(env, "prepare_playlist", {"request": {"name": "Long explanation", "account_id": env.aid,
                                                       "mood": "relaxed"}})
    assert draft["preview"]["songs"][0]["reason"] == reason
    explicit = call(env, "prepare_playlist", {"request": {"name": "Explicit explanation", "account_id": env.aid,
        "selections": [{"song_id": sid, "reason": reason, "confidence": .9}]}})
    assert explicit["preview"]["songs"][0]["reason"] == reason


def test_explanations_exceeding_shared_limit_report_validation_errors(env):
    reason = "x" * 1501
    error = call(env, "annotate_songs", {"annotations": [{"song_id": env.ids["Hello"], "evidence": reason,
                                                          "confidence": .9}]}, error=True)
    assert "1500" in error and "evidence" in error
    error = call(env, "prepare_playlist", {"request": {"name": "Too long", "account_id": env.aid,
        "selections": [{"song_id": env.ids["Hello"], "reason": reason, "confidence": .9}]}}, error=True)
    assert "1500" in error and "reason" in error


def batch_songs(env, count):
    source = env.db.val("SELECT id FROM catalog_sources WHERE account_id=? AND kind='liked'", (env.aid,))
    ids = []
    for n in range(count):
        sid = env.db.run("INSERT INTO songs(title,artist,duration,created_at) VALUES(?,?,180,0)",
                         (f"Batch {env.uid}-{n:04}", "Batch artist"))
        env.db.run("INSERT INTO catalog_items(source_id,ref_id,song_id,first_seen) VALUES(?,?,?,0)",
                   (source, f"batch-{n}", sid))
        ids.append(sid)
    return ids


def traced_queries(db, work):
    queries = []
    db.conn.set_trace_callback(queries.append)
    try:
        result = work()
    finally:
        db.conn.set_trace_callback(None)
    return result, queries


def test_library_query_count_stays_bounded_as_page_size_grows(env):
    batch_songs(env, 260)
    small, small_queries = traced_queries(env.db, lambda: music.library(env.db, env.uid, limit=10))
    large, large_queries = traced_queries(env.db, lambda: music.library(env.db, env.uid, limit=250))
    assert len(small["songs"]) == 10 and len(large["songs"]) == 250
    assert len(large_queries) <= len(small_queries) <= 5
    assert all(song["sources"] and all(s["service"] == "ytm" for s in song["sources"]) for song in large["songs"])


def test_batch_playlist_preview_preserves_selection_order_and_has_bounded_queries(env):
    ids = batch_songs(env, 500)
    ordered = list(reversed(ids))
    principal = authenticate(env.db, "Bearer " + env.token["token"])
    request = mcp_actions.PlaylistRequest(name="Large playlist", account_id=env.aid, max_tracks=500,
        selections=[mcp_actions.Selection(song_id=sid, reason="Explicit selection", confidence=.9) for sid in ordered])

    def prepare():
        params, preview = mcp_actions.playlist_preview(env.db, principal, request)
        return mcp_actions.prepare(env.db, principal, "playlist", params, preview)

    draft, queries = traced_queries(env.db, prepare)
    assert [song["id"] for song in draft["preview"]["songs"]] == ordered
    assert len(queries) < 20


def test_automatic_playlist_preview_loads_annotations_in_batches(env):
    ids = batch_songs(env, 500)
    data = {"moods": ["batch mood"], "genres": [], "tags": [], "confidence": .9, "evidence": "Known test music"}
    env.db.many("INSERT INTO music_annotations(user_id,song_id,data,origin,updated_at) VALUES(?,?,?,'assistant',0)",
                [(env.uid, sid, json.dumps(data)) for sid in ids])
    principal = authenticate(env.db, "Bearer " + env.token["token"])
    request = mcp_actions.PlaylistRequest(name="Automatic", account_id=env.aid, mood="batch mood", max_tracks=500)
    (_, preview), queries = traced_queries(env.db, lambda: mcp_actions.playlist_preview(env.db, principal, request))
    assert [s["id"] for s in preview["songs"]] == ids
    assert len(queries) < 20


def test_selected_song_batches_reject_foreign_ids_and_keep_annotations_private(env, host):
    sid = env.ids["Hello"]
    annotate(env, sid)
    other = person(host, "Other")
    foreign = env.db.run("INSERT INTO songs(title,created_at) VALUES('Foreign song',0)")
    source = env.db.val("SELECT id FROM catalog_sources WHERE account_id=?", (other.aid,))
    env.db.run("INSERT INTO catalog_items(source_id,ref_id,song_id,first_seen) VALUES(?,?,?,0)",
               (source, "foreign", foreign))
    with pytest.raises(HTTPException):
        music.selected_songs(env.db, env.uid, [sid, foreign])
    assert music.selected_songs(env.db, other.uid, [sid])[sid]["annotation"] is None
    assert music.selected_songs(env.db, env.uid, [sid, sid])[sid]["annotation"]["moods"] == ["relaxed"]


def test_action_summaries_omit_large_data_and_details_are_loaded_separately(env, host):
    draft = call(env, "prepare_playlist", {"request": {"name": "Lazy detail", "account_id": env.aid,
        "selections": [{"song_id": env.ids["Hello"], "reason": "Known music", "confidence": .9}]}})
    env.db.run("UPDATE mcp_actions SET state='ok',result=? WHERE id=?",
               (json.dumps({"added": 1, "details": "large-result-" * 1000}), draft["id"]))
    for overview in (env.client.get("/api/mcp", headers=env.browser).json(), call(env, "list_actions")):
        summary = next(a for a in overview["actions"] if a["id"] == draft["id"])
        assert summary["has_result"] is True and summary["state"] == "ok"
        assert not {"preview", "params", "result"} & summary.keys()
        assert "large-result-" not in json.dumps(overview)
    details = env.client.get(f"/api/mcp/actions/{draft['id']}", headers=env.browser)
    assert details.status_code == 200 and details.json()["preview"] == draft["preview"]
    assert details.json()["result"]["added"] == 1
    other = person(host, "Other")
    assert env.client.get(f"/api/mcp/actions/{draft['id']}", headers=other.browser).status_code == 404
    assert env.client.get(f"/api/mcp/actions/{draft['id']}").status_code == 401


def test_action_summary_query_count_is_independent_of_number_of_actions(env):
    now = time.time()
    rows = [(f"summary-{env.uid}-{n}", env.uid, env.token["id"], now + n) for n in range(50)]
    env.db.many("INSERT INTO mcp_actions(id,user_id,token_id,operation,scope,params,preview,destructive,"
                "state,created_at,expires_at) VALUES(?,?,?,'playlist','manage','{}','{}',0,'pending',?,0)", rows)
    actions, queries = traced_queries(env.db, lambda: mcp_actions.action_summaries(env.db, env.uid))
    assert len(actions) == 50 and len(queries) == 1
    assert all(a["state"] == "expired" and a["has_result"] is False for a in actions)
    assert [a["id"] for a in actions[:2]] == [f"summary-{env.uid}-49", f"summary-{env.uid}-48"]
