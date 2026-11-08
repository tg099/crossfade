"""Catalog tests: scan across services, bulk remove and bulk add, with the fake services of test_engine."""
from __future__ import annotations

from pathlib import Path

import pytest
from test_engine import FakeProvider

from crossfade.catalog import Catalog
from crossfade.db import Database
from crossfade.models import APPLE, LIKED, PLAYLIST, SPOTIFY, YTM, Collection


class CatalogFake(FakeProvider):
    def collections(self):
        out = [Collection(LIKED, "", "Likes")]
        out += [Collection(PLAYLIST, cid, f"PL {cid}") for (kind, cid) in self.cols if kind == PLAYLIST]
        return out

    def create_playlist(self, name, description=""):
        self.cols.setdefault((PLAYLIST, "fresh"), {})
        return Collection(PLAYLIST, "fresh", name, 0)


@pytest.fixture
def env(tmp_path: Path):
    db = Database(tmp_path / "c.db")
    uid = db.run("INSERT INTO users(username,display_name,password_hash,created_at) VALUES('t','T','x',0)")
    provs = {}
    for svc in (YTM, APPLE, SPOTIFY):
        aid = db.run("INSERT INTO accounts(user_id,service,created_at,updated_at) VALUES(?,?,0,0)", (uid, svc))
        provs[svc] = (aid, CatalogFake(svc))
    return db, uid, provs


def cat(env) -> Catalog:
    db, uid, provs = env
    return Catalog(db, uid, {aid: p for aid, p in provs.values()})


def song_ids(db, title: str) -> set[int]:
    return {r["id"] for r in db.all("SELECT id FROM songs WHERE title LIKE ?", (title + "%",))}


def where(db, sid: int) -> set[tuple[str, str]]:
    return {(r["service"], r["name"]) for r in db.all(
        """SELECT a.service, s.name FROM catalog_items ci JOIN catalog_sources s ON s.id=ci.source_id
           JOIN accounts a ON a.id=s.account_id WHERE ci.song_id=?""", (sid,))}


def test_scan_merges_same_song_across_services(env):
    db, _, provs = env
    provs[APPLE][1].put(LIKED, "", "lucky", "blind", "hello")
    provs[SPOTIFY][1].put(LIKED, "", "lucky", "haus")
    provs[YTM][1].put(LIKED, "", "lucky", "blind")
    provs[YTM][1].put(PLAYLIST, "gym", "duhast")
    res = cat(env).scan()
    assert res["collections"] == 4
    assert res["songs"] == 5          # lucky, blind, hello, haus, duhast – each once
    (lucky,) = song_ids(db, "Get Lucky")
    assert {s for s, _ in where(db, lucky)} == {YTM, APPLE, SPOTIFY}
    (duhast,) = song_ids(db, "Du hast")
    assert where(db, duhast) == {(YTM, "PL gym")}


def test_rescan_drops_removed_and_keeps_first_seen(env):
    db, _, provs = env
    provs[APPLE][1].put(LIKED, "", "lucky", "blind")
    cat(env).scan()
    first = db.val("SELECT first_seen FROM catalog_items LIMIT 1")
    provs[APPLE][1].drop(LIKED, "", "blind")
    cat(env).scan()
    assert db.val("SELECT COUNT(*) FROM catalog_items") == 1
    assert db.val("SELECT first_seen FROM catalog_items LIMIT 1") == first


def test_excluded_source_is_not_read(env):
    db, _, provs = env
    provs[YTM][1].put(PLAYLIST, "gym", "duhast")
    cat(env).scan()
    db.run("UPDATE catalog_sources SET included=0 WHERE collection_id='gym'")
    provs[YTM][1].put(PLAYLIST, "gym", "haus")
    cat(env).scan()
    assert not song_ids(db, "Haus am See")


def test_bulk_remove_from_chosen_collections(env):
    db, _, provs = env
    provs[APPLE][1].put(LIKED, "", "lucky", "blind")
    provs[SPOTIFY][1].put(LIKED, "", "lucky", "blind")
    c = cat(env)
    c.scan()
    (lucky,) = song_ids(db, "Get Lucky")
    apple_src = db.val("SELECT s.id FROM catalog_sources s JOIN accounts a ON a.id=s.account_id "
                       "WHERE a.service='apple' AND s.kind='liked'")
    res = c.remove({lucky}, [apple_src])
    assert res == {"removed": 1, "failed": 0, "errors": []}
    assert provs[APPLE][1].keys() == ["blind"]          # removed with the handles of a fresh read
    assert provs[SPOTIFY][1].keys() == ["blind", "lucky"]
    assert where(db, lucky) == {(SPOTIFY, "Likes")}


def test_bulk_remove_reports_failures(env):
    db, _, provs = env
    provs[APPLE][1].put(LIKED, "", "lucky")
    provs[APPLE][1].fail_remove = {"apple_lucky"}
    c = cat(env)
    c.scan()
    src = db.val("SELECT id FROM catalog_sources WHERE kind='liked' AND account_id=?", (provs[APPLE][0],))
    res = c.remove(song_ids(db, "Get Lucky"), [src])
    assert res["removed"] == 0 and res["failed"] == 1
    assert db.val("SELECT COUNT(*) FROM catalog_items WHERE source_id=?", (src,)) == 1


def test_bulk_add_searches_target_and_skips_present(env):
    db, _, provs = env
    provs[APPLE][1].put(LIKED, "", "lucky", "hello")
    provs[SPOTIFY][1].put(LIKED, "", "lucky")
    c = cat(env)
    c.scan()
    spot = db.val("SELECT id FROM catalog_sources WHERE kind='liked' AND account_id=?", (provs[SPOTIFY][0],))
    ids = sorted(song_ids(db, "Get Lucky") | song_ids(db, "Hello"))
    res = c.add(ids, source_id=spot)
    assert res["added"] == 1 and res["already"] == 1 and not res["missing"]
    assert provs[SPOTIFY][1].keys() == ["hello", "lucky"]
    (hello,) = song_ids(db, "Hello")
    assert (SPOTIFY, "Likes") in where(db, hello)


def test_bulk_add_into_new_playlist(env):
    db, _, provs = env
    provs[APPLE][1].put(LIKED, "", "haus")
    c = cat(env)
    c.scan()
    res = c.add(sorted(song_ids(db, "Haus am See")), account_id=provs[YTM][0], new_playlist="Summer")
    assert res["added"] == 1
    assert provs[YTM][1].keys(PLAYLIST, "fresh") == ["haus"]
    assert db.val("SELECT name FROM catalog_sources WHERE collection_id='fresh'") == "Summer"
