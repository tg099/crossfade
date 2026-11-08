"""Notifications: channels per user, events reported once, logins checked before a sync needs them."""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("CROSSFADE_DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("CROSSFADE_BASE_URL", "https://music.example.test")

import time  # noqa: E402

import pytest  # noqa: E402

from crossfade import notify, worker  # noqa: E402
from crossfade.db import get_db  # noqa: E402
from crossfade.models import AuthError  # noqa: E402
from tests.test_sharing import H, befriend, fade, person  # noqa: E402


@pytest.fixture()
def sent(monkeypatch):
    out: list[tuple[dict, dict, dict]] = []
    monkeypatch.setattr(notify, "_deliver_hook", lambda ch, cfg, msg: out.append((ch, cfg, msg)))
    monkeypatch.setattr(notify, "_dispatch", _sync_dispatch)
    return out


_real_dispatch = notify._dispatch


def _sync_dispatch(db, user_id, msg, sync=False):
    _real_dispatch(db, user_id, msg, sync=True)


def _suffix() -> str:
    return str(int(time.time() * 1e6) % 10_000_000)


def test_channels_validate_and_hide_secrets():
    c, _ = person("ntfy" + _suffix(), "N")
    assert c.post("/api/notifications/channels", json={"kind": "ntfy", "config": {"topic": "bad topic!"}},
                  headers=H).status_code == 400
    assert c.post("/api/notifications/channels", json={"kind": "webhook", "config": {"url": "file:///etc/passwd"}},
                  headers=H).status_code == 400
    assert c.post("/api/notifications/channels", json={"kind": "ntfy", "config": {"topic": "x"}, "events": ["nope"]},
                  headers=H).status_code == 400
    # e-mail needs the admin's mail server first
    assert c.post("/api/notifications/channels", json={"kind": "email", "config": {"to": "a@b.cd"}},
                  headers=H).status_code == 400
    r = c.post("/api/notifications/channels", json={"kind": "ntfy", "config": {"topic": "cf-test", "token": "tk_secret"},
                                                     "events": ["auth", "confirm"]}, headers=H)
    assert r.status_code == 200, r.text
    ch = r.json()
    assert ch["config"] == {"server": "https://ntfy.sh", "topic": "cf-test", "token_set": True}
    assert ch["events"] == ["auth", "confirm"]
    r = c.post("/api/notifications/channels",
               json={"kind": "webhook", "config": {"url": "https://discord.com/api/webhooks/1/secret-part"}}, headers=H)
    assert r.json()["flavor"] == "discord" and "secret-part" not in str(r.json())
    # editing keeps the token unless it is cleared explicitly
    r = c.put(f"/api/notifications/channels/{ch['id']}", json={"config": {"topic": "cf-other"}}, headers=H)
    assert r.json()["config"]["token_set"] is True and r.json()["config"]["topic"] == "cf-other"
    r = c.put(f"/api/notifications/channels/{ch['id']}", json={"config": {"token": ""}, "enabled": False}, headers=H)
    assert r.json()["config"]["token_set"] is False and r.json()["enabled"] is False


def test_channels_are_private():
    a, _ = person("pa" + _suffix(), "A")
    b, _ = person("pb" + _suffix(), "B")
    ch = a.post("/api/notifications/channels", json={"kind": "ntfy", "config": {"topic": "mine"}}, headers=H).json()
    assert b.get("/api/notifications").json()["channels"] == []
    assert b.put(f"/api/notifications/channels/{ch['id']}", json={"enabled": False}, headers=H).status_code == 404
    assert b.delete(f"/api/notifications/channels/{ch['id']}", headers=H).status_code == 404
    assert b.post(f"/api/notifications/channels/{ch['id']}/test", headers=H).status_code == 404


def test_test_message_and_errors_are_shown(sent, monkeypatch):
    c, _ = person("tm" + _suffix(), "T")
    ch = c.post("/api/notifications/channels", json={"kind": "ntfy", "config": {"topic": "t1"}}, headers=H).json()
    r = c.post(f"/api/notifications/channels/{ch['id']}/test", headers=H).json()
    assert r == {"ok": True, "error": None} and sent[-1][2]["event"] == "test"

    def boom(*_):
        raise ConnectionError("no route")
    monkeypatch.setattr(notify, "_deliver_hook", boom)
    r = c.post(f"/api/notifications/channels/{ch['id']}/test", headers=H).json()
    assert r["ok"] is False and "no route" in r["error"]
    assert "no route" in c.get("/api/notifications").json()["channels"][0]["last_error"]


def test_a_lasting_problem_is_reported_once(sent):
    db = get_db()
    c, uid = person("once" + _suffix(), "O")
    c.post("/api/notifications/channels", json={"kind": "ntfy", "config": {"topic": "o"}, "events": ["auth"]}, headers=H)
    acc = db.val("SELECT id FROM accounts WHERE user_id=? AND service='apple'", (uid,))
    worker.mark_account(db, acc, "error", "token expired")
    worker.mark_account(db, acc, "error", "token expired")
    assert len(sent) == 1 and sent[0][2]["title"] == "Apple Music: sign in again"
    assert sent[0][2]["url"].endswith("/services?service=apple")
    worker.mark_account(db, acc, "ok", None)          # resolved …
    worker.mark_account(db, acc, "error", "again")    # … so the next failure is news
    assert len(sent) == 2


def test_events_go_only_to_channels_that_want_them(sent):
    db = get_db()
    c, uid = person("ev" + _suffix(), "E")
    c.post("/api/notifications/channels", json={"kind": "ntfy", "config": {"topic": "a"}, "events": ["confirm"]},
           headers=H)
    acc = db.val("SELECT id FROM accounts WHERE user_id=? AND service='ytm'", (uid,))
    worker.mark_account(db, acc, "error", "x")
    assert sent == []


def test_run_results_are_reported(sent):
    db = get_db()
    c, uid = person("run" + _suffix(), "R")
    c.post("/api/notifications/channels", json={"kind": "webhook", "config": {"url": "https://hook.example/x"}},
           headers=H)
    line = fade(c, members=[
        {"service": "ytm", "kind": "playlist", "collection_id": "PL1", "collection_name": "A", "role": "both"},
        {"service": "apple", "kind": "playlist", "collection_id": "p.1", "collection_name": "B", "role": "both"}])
    lid, member = line["id"], line["members"][0]["id"]
    notify.run_finished(db, lid, "needs_confirm", "3 deletions are waiting for your confirmation.", "schedule")
    notify.run_finished(db, lid, "needs_confirm", "3 deletions are waiting for your confirmation.", "schedule")
    assert [m["event"] for _, _, m in sent] == ["confirm"]
    # three failed syncs in a row
    for _ in range(3):
        db.run("INSERT INTO runs(line_id,trigger,started_at,status) VALUES(?,?,?,'error')", (lid, "schedule", time.time()))
    notify.run_finished(db, lid, "error", "HTTP 500", "schedule")
    assert sent[-1][2]["event"] == "errors" and "HTTP 500" in sent[-1][2]["message"]
    # new open songs: reported when the count grows, not when it stays or shrinks
    song = db.run("INSERT INTO songs(title,created_at) VALUES('S',?)", (time.time(),))
    song2 = db.run("INSERT INTO songs(title,created_at) VALUES('T',?)", (time.time(),))
    db.run("INSERT INTO issues(line_id,member_id,song_id,kind,created_at) VALUES(?,?,?,'unmatched',?)",
           (lid, member, song, time.time()))
    n = len(sent)
    notify.run_finished(db, lid, "ok", None, "schedule")
    assert len(sent) == n + 1 and sent[-1][2]["title"].endswith("1 song to check")
    notify.run_finished(db, lid, "ok", None, "schedule")
    assert len(sent) == n + 1
    db.run("INSERT INTO issues(line_id,member_id,song_id,kind,created_at) VALUES(?,?,?,'unmatched',?)",
           (lid, member, song2, time.time()))
    notify.run_finished(db, lid, "ok", None, "schedule")
    assert sent[-1][2]["title"].endswith("1 song to check") and len(sent) == n + 2
    # previews never notify
    notify.run_finished(db, lid, "needs_confirm", "x", "preview")
    assert len(sent) == n + 2


def test_friend_requests_notify(sent):
    sfx = _suffix()
    (a, _), (b, _) = person("fa" + sfx, "Fay"), person("fb" + sfx, "Finn")
    b.post("/api/notifications/channels", json={"kind": "ntfy", "config": {"topic": "f"}, "events": ["social"]},
           headers=H)
    befriend(a, b, "fb" + sfx)
    assert sent and sent[0][2]["title"] == "New friend request" and "Fay" in sent[0][2]["message"]


class _Prov:
    def __init__(self, fail=False, expires=None):
        self.fail, self.expires = fail, expires

    def whoami(self):
        if self.fail:
            raise AuthError("Apple Music sign-in is invalid or expired.")
        return "Me"

    def login_expires_at(self):
        return self.expires


def test_login_check_marks_and_warns(sent, monkeypatch):
    db = get_db()
    c, uid = person("chk" + _suffix(), "C")
    c.post("/api/notifications/channels", json={"kind": "ntfy", "config": {"topic": "c"}}, headers=H)
    acc = db.val("SELECT id FROM accounts WHERE user_id=? AND service='apple'", (uid,))
    soon = time.time() + 3 * 86400
    monkeypatch.setattr(worker, "build_provider", lambda db, a: _Prov(expires=soon))
    assert worker.check_account(db, acc) == "ok"
    row = db.one("SELECT * FROM accounts WHERE id=?", (acc,))
    assert row["checked_at"] and abs(row["expires_at"] - soon) < 1
    assert sent[-1][2]["event"] == "expiring" and "in 3 days" in sent[-1][2]["title"]
    worker.check_account(db, acc)
    assert len(sent) == 1                      # warned once for this end date
    monkeypatch.setattr(worker, "build_provider", lambda db, a: _Prov(fail=True))
    assert worker.check_account(db, acc) == "error"
    assert db.val("SELECT status FROM accounts WHERE id=?", (acc,)) == "error"
    assert sent[-1][2]["event"] == "auth"
    # the overview shows when the login ends and when it was checked
    svc = next(s for s in c.get("/api/services").json() if s["service"] == "apple")
    assert svc["checked_at"] and svc["expires_at"]


def test_unreachable_service_is_tried_again_soon(monkeypatch):
    db = get_db()
    _, uid = person("unr" + _suffix(), "U")
    acc = db.val("SELECT id FROM accounts WHERE user_id=? AND service='ytm'", (uid,))

    def down(db, a):
        raise ConnectionError("down")
    monkeypatch.setattr(worker, "build_provider", down)
    assert worker.check_account(db, acc) == "unreachable"
    row = db.one("SELECT status, checked_at FROM accounts WHERE id=?", (acc,))
    assert row["status"] == "ok"
    assert row["checked_at"] < time.time() - worker.CHECK_EVERY + 3700     # due again in about an hour


def test_webhook_payloads(monkeypatch):
    posted = []

    class R:
        def raise_for_status(self):
            pass
    monkeypatch.setattr(notify.httpx, "post", lambda url, json=None, headers=None, timeout=None: (posted.append((url, json, headers)), R())[1])
    msg = {"event": "auth", "title": "T", "message": "M", "url": "https://x/y", "ts": 1}
    notify._send("webhook", {"url": "https://discord.com/api/webhooks/1/a"}, msg, None)
    notify._send("webhook", {"url": "https://hooks.slack.com/services/a"}, msg, None)
    notify._send("webhook", {"url": "https://ha.local/api/webhook/x"}, msg, None)
    notify._send("ntfy", {"server": "https://ntfy.sh", "topic": "t", "token": "tok"}, msg, None)
    assert posted[0][1]["content"].startswith("**T**")
    assert posted[1][1] == {"text": "*T*\nM\nhttps://x/y"}
    assert posted[2][1]["event"] == "auth" and posted[2][1]["source"] == "crossfade"
    assert posted[3][0] == "https://ntfy.sh/" and posted[3][1]["topic"] == "t" and posted[3][1]["click"] == "https://x/y"
    assert posted[3][2] == {"Authorization": "Bearer tok"}


def test_smtp_settings_are_admin_only_and_keep_the_password():
    from tests.test_sharing import person as p
    db = get_db()
    c, uid = p("adm" + _suffix(), "Admin")
    assert c.get("/api/admin/smtp").status_code == 403
    db.run("UPDATE users SET is_admin=1 WHERE id=?", (uid,))
    r = c.put("/api/admin/smtp", json={"host": "smtp.example.test", "security": "starttls", "username": "u",
                                       "password": "pw", "sender": "Crossfade <cf@example.test>"}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["port"] == 587 and r.json()["password_set"] is True and "pw" not in str(r.json())
    r = c.put("/api/admin/smtp", json={"host": "smtp.example.test", "security": "ssl", "sender": "cf@example.test"},
              headers=H)
    assert r.json()["port"] == 465 and r.json()["password_set"] is True
    assert c.post("/api/notifications/channels", json={"kind": "email", "config": {"to": "me@example.test"}},
                  headers=H).status_code == 200
    db.set_setting("smtp", None)
    db.set_setting("smtp_password", None)
