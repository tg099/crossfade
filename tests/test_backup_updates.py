"""Backups (made, listed, downloaded, restored), the update check, notifications in the user's
language and the first-steps flag."""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("CROSSFADE_DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("CROSSFADE_BASE_URL", "https://music.example.test")

import sqlite3  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402

from crossfade import backup, notify, updates, worker  # noqa: E402
from crossfade.config import config  # noqa: E402
from crossfade.db import MIGRATIONS, Database, get_db  # noqa: E402
from crossfade.security import encrypt, hash_password  # noqa: E402
from tests.test_notify import _suffix, sent  # noqa: E402,F401
from tests.test_sharing import H, person  # noqa: E402


def admin(name: str):
    c, uid = person(name + _suffix(), name.title())
    get_db().run("UPDATE users SET is_admin=1 WHERE id=?", (uid,))
    return c, uid


def fresh_db(tmp: Path, name="live") -> Database:
    (tmp / name).mkdir()
    db = Database(tmp / name / "crossfade.sqlite3")
    db.run("INSERT INTO users(username,display_name,password_hash,is_admin,created_at) VALUES('root','Root',?,1,?)",
           (hash_password("x" * 10), time.time()))
    return db


# ---------------------------------------------------------------------- notifications in the user's language
def test_notifications_follow_the_users_language(sent):  # noqa: F811
    db = get_db()
    c, uid = person("de" + _suffix(), "D")
    c.post("/api/notifications/channels", json={"kind": "ntfy", "config": {"topic": "d"}}, headers=H)
    acc = db.val("SELECT id FROM accounts WHERE user_id=? AND service='apple'", (uid,))
    db.run("UPDATE users SET lang='de' WHERE id=?", (uid,))
    worker.mark_account(db, acc, "error", "token expired")
    assert sent[-1][2]["title"] == "Apple Music: bitte neu anmelden"
    assert sent[-1][2]["message"].endswith("pausieren die Syncs damit. token expired")
    # "system": the browser language they last opened Crossfade in
    db.run("UPDATE users SET lang='system' WHERE id=?", (uid,))
    assert c.get("/api/auth/state", headers={"Accept-Language": "en-US,en;q=0.9,de;q=0.8"}).status_code == 200
    assert notify.language(db, uid) == "en"
    c.get("/api/auth/state", headers={"Accept-Language": "fr-FR, de-AT;q=0.8"})
    assert notify.language(db, uid) == "de"
    ch = c.get("/api/notifications").json()["channels"][0]
    c.post(f"/api/notifications/channels/{ch['id']}/test", headers=H)
    assert sent[-1][2]["title"] == "Test von Crossfade"


def test_every_text_exists_in_both_languages():
    v = {"service": "Spotify", "detail": "x", "days": 3, "fade": "Gym", "n": 2, "name": "Anna", "username": "anna",
         "latest": "1.5.0", "current": "1.4.0", "connection": "Claude", "op": "remove_songs"}
    for key, langs in notify.TEXTS.items():
        assert set(langs) == set(notify.LANGS), key
        for lang in notify.LANGS:
            title, message = notify.text(lang, key, **v)
            assert title and message and "{" not in title + message, (key, lang)
    assert notify.text("de", "issues", fade="Gym", n=1)[0] == "Gym: 1 Song zum Prüfen"
    assert notify.text("de", "expiring", service="TIDAL", days=1)[0] == "TIDAL-Anmeldung läuft morgen ab"
    assert notify.text("en", "auth", service="TIDAL", detail="")[1].endswith("are paused.")
    assert notify.text("de", "assistant_confirm", connection="Claude", op="fade_delete") == (
        "Claude wartet auf dich", "Dein Assistent möchte einen Fade löschen. Bestätige oder verwirf das in Crossfade.")
    assert set(notify.ASSISTANT_DELETES["de"]) == set(notify.ASSISTANT_DELETES["en"])


# ---------------------------------------------------------------------- first steps
def test_first_steps_can_be_put_away():
    c, _ = person("intro" + _suffix(), "I")
    assert c.get("/api/auth/state").json()["user"]["intro_done"] is False
    assert c.put("/api/auth/me", json={"intro_done": True}, headers=H).json()["user"]["intro_done"] is True
    assert c.get("/api/auth/state").json()["user"]["intro_done"] is True


# ---------------------------------------------------------------------- update check
def test_versions_sort_like_releases():
    order = ["1.3.9", "1.4.0-RC.1", "1.4.0-RC.2", "1.4.0-RC.10", "1.4.0", "1.4.1", "2.0.0"]
    assert sorted(order, key=updates.parse) == order
    assert updates.parse("dev") is None and updates.parse("v1.2.3") == updates.parse("1.2.3")
    rel = [{"tag_name": "v1.5.0-RC.1", "prerelease": True}, {"tag_name": "v1.4.2"}, {"tag_name": "v1.4.10"},
           {"tag_name": "v9.0.0", "draft": True}]
    assert updates.newest(rel, "1.4.0")["tag_name"] == "v1.4.10"
    assert updates.newest(rel, "1.4.0-RC.1")["tag_name"] == "v1.5.0-RC.1"


def test_admins_hear_about_a_new_release_once(sent):  # noqa: F811
    db = get_db()
    object.__setattr__(config, "version", "1.4.0")      # the config is frozen
    try:
        a, aid = admin("upd")
        u, _ = person("plain" + _suffix(), "P")
        a.post("/api/notifications/channels", json={"kind": "ntfy", "config": {"topic": "u"}}, headers=H)
        assert u.get("/api/admin/update").status_code == 403
        releases = [{"tag_name": "v1.4.1", "html_url": "https://github.com/tg099/crossfade/releases/tag/v1.4.1"}]
        out = updates.check(db, fetch=lambda: releases)
        assert out["available"] and out["latest"] == "1.4.1"
        mine = [m for ch, _, m in sent if ch["user_id"] == aid]
        assert mine[-1]["event"] == "update" and mine[-1]["url"].endswith("/v1.4.1")
        n = len(sent)
        updates.check(db, fetch=lambda: releases)       # same release: no second message
        assert len(sent) == n
        assert a.get("/api/admin/update").json()["available"] is True
        # GitHub unreachable: what we knew stays, and it asks again within the hour
        def down():
            raise ConnectionError("offline")
        out = updates.check(db, fetch=down)
        assert out["latest"] == "1.4.1" and "offline" in out["error"] and not updates.due(db)
        # once running 1.4.1 the hint is gone
        object.__setattr__(config, "version", "1.4.1")
        assert updates.state(db)["available"] is False
    finally:
        object.__setattr__(config, "version", "dev")
        db.set_setting("update_check", None)


def test_dev_builds_never_ask():
    assert config.version == "dev" and not updates.enabled() and not updates.due(get_db())


# ---------------------------------------------------------------------- backups
def test_backup_kinds_and_retention(tmp_path):
    db = fresh_db(tmp_path)
    for _ in range(3):
        db.backup("manual")
    db.backup("daily")
    db.backup()
    kinds = sorted(b["kind"] for b in backup.listing(db))
    assert kinds == ["daily", "manual", "manual", "manual", "start"]
    backup.prune(backup.folder_of(db.path), "manual", 1)
    assert sorted(b["kind"] for b in backup.listing(db)) == ["daily", "manual", "start"]
    assert not backup.daily_due(db)


def test_restore_brings_back_the_old_state(tmp_path):
    db = fresh_db(tmp_path)
    uid = db.val("SELECT id FROM users")
    db.run("INSERT INTO sessions(token_hash,user_id,created_at,expires_at) VALUES('keep',?,?,?)",
           (uid, time.time(), time.time() + 99))
    old = db.backup("manual")
    db.run("INSERT INTO lines(user_id,name,color,created_at,updated_at) VALUES(?,?,?,?,?)",
           (uid, "Made later", "#000", time.time(), time.time()))
    other = Database(tmp_path / "live" / "crossfade.sqlite3")      # another thread's view of the same file
    assert other.val("SELECT COUNT(*) FROM lines") == 1
    db.run("DELETE FROM sessions")
    db.run("INSERT INTO sessions(token_hash,user_id,created_at,expires_at) VALUES('keep',?,?,?)",
           (uid, time.time(), time.time() + 99))
    info = backup.restore(db, old, keep_session="keep")
    assert info["fades"] == 0 and info["users"] == 1 and info["before"].endswith("-restore.sqlite3")
    assert db.val("SELECT COUNT(*) FROM lines") == 0 and other.val("SELECT COUNT(*) FROM lines") == 0
    assert db.val("SELECT 1 FROM sessions WHERE token_hash='keep'")
    assert db.val("PRAGMA user_version") == len(MIGRATIONS)
    # the restore can be undone with the copy taken before it
    backup.restore(db, backup.path_of(db, info["before"]))
    assert db.val("SELECT name FROM lines") == "Made later"


def test_an_older_backup_is_migrated(tmp_path):
    db = fresh_db(tmp_path)
    f = tmp_path / "old.sqlite3"
    c = sqlite3.connect(f)
    c.executescript("".join(MIGRATIONS[:14]) + "PRAGMA user_version=14;")
    c.execute("INSERT INTO users(username,display_name,password_hash,is_admin,created_at) VALUES('old','Old','x',1,0)")
    c.commit()
    c.close()
    assert backup.inspect(f)["schema"] == 14
    backup.restore(db, f)
    assert db.val("SELECT username FROM users") == "old" and db.val("PRAGMA user_version") == len(MIGRATIONS)
    assert db.val("SELECT intro_done FROM users") == 0


def test_bad_backups_are_refused(tmp_path):
    db = fresh_db(tmp_path)
    junk = tmp_path / "junk.sqlite3"
    junk.write_bytes(b"hello" * 200)
    with pytest.raises(backup.BackupError, match="not an SQLite"):
        backup.inspect(junk)
    newer = tmp_path / "newer.sqlite3"
    newer.write_bytes(db.backup("manual").read_bytes())
    c = sqlite3.connect(newer)
    c.execute(f"PRAGMA user_version={len(MIGRATIONS) + 1}")
    c.commit()
    c.close()
    with pytest.raises(backup.BackupError, match="newer Crossfade"):
        backup.inspect(newer)
    # logins saved with another secret key would be lost
    other_key = Fernet(Fernet.generate_key()).encrypt(b"{}").decode()
    db.run("INSERT INTO accounts(user_id,service,credentials,created_at,updated_at) VALUES(1,'ytm',?,0,0)", (other_key,))
    with pytest.raises(backup.BackupError, match="another secret key"):
        backup.inspect(db.backup("manual"))
    db.run("UPDATE accounts SET credentials=?", (encrypt({"x": 1}),))
    assert backup.inspect(db.backup("manual"))["logins"] == 1
    with pytest.raises(backup.BackupError):
        backup.path_of(db, "../crossfade.sqlite3")


def test_backup_api_for_admins_only():
    a, _ = admin("bk")
    u, _ = person("nobk" + _suffix(), "N")
    assert u.get("/api/admin/backups").status_code == 403
    r = a.post("/api/admin/backups", headers=H)
    assert r.status_code == 200, r.text
    made = next(b for b in r.json()["backups"] if b["kind"] == "manual")
    d = a.get(f"/api/admin/backups/{made['name']}")
    assert d.status_code == 200 and d.content.startswith(b"SQLite format 3")
    assert "attachment" in d.headers["content-disposition"]
    assert a.get("/api/admin/backups/..%2Fcrossfade.sqlite3").status_code == 404
    # an upload is checked before anything is replaced, and never kept
    r = a.post("/api/admin/backup-upload/check", content=d.content, headers=H)
    assert r.status_code == 200 and r.json()["users"] >= 2
    assert a.post("/api/admin/backup-upload/check", content=b"x" * 1000, headers=H).status_code == 400
    assert not list(backup.folder_of(get_db().path).glob(".upload-*"))
    assert a.delete(f"/api/admin/backups/{made['name']}", headers=H).status_code == 200
    assert a.get(f"/api/admin/backups/{made['name']}").status_code == 404


def test_restore_waits_while_something_runs():
    w = worker.get_worker()
    w.jobs[-1] = {"state": "running"}
    try:
        assert not w.begin_restore()
        a, _ = admin("busy")
        name = a.post("/api/admin/backups", headers=H).json()["backups"][0]["name"]
        assert a.post(f"/api/admin/backups/{name}/restore", headers=H).status_code == 409
    finally:
        w.jobs.pop(-1)
    assert w.begin_restore() and not w.start(1) and not w.start_catalog(1, "scan")
    w.end_restore()
