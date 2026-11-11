"""Personal music metadata and bounded library queries for assistants and the UI.

Annotations are estimates supplied by the connected assistant, never invented audio measurements.
A browser correction is authoritative and cannot subsequently be overwritten by an assistant.
"""
from __future__ import annotations

import json
import time
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .db import Database
from .models import SERVICES

MAX_REASON_LENGTH = 1500
SONG_BATCH_SIZE = 400


class Annotation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    song_id: int = Field(gt=0)
    moods: list[str] = Field(default_factory=list, max_length=20)
    genres: list[str] = Field(default_factory=list, max_length=20)
    tags: list[str] = Field(default_factory=list, max_length=30)
    language: str | None = Field(None, max_length=60)
    year: int | None = Field(None, ge=1800, le=2200)
    energy: Literal["low", "medium", "high"] | None = None
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(min_length=1, max_length=MAX_REASON_LENGTH)

    @field_validator("moods", "genres", "tags")
    @classmethod
    def clean_labels(cls, values: list[str]) -> list[str]:
        labels = list(dict.fromkeys(v.strip().casefold() for v in values))
        if any(not v or len(v) > 80 for v in labels):
            raise ValueError("Labels must contain 1–80 characters.")
        return labels

    @field_validator("evidence")
    @classmethod
    def evidence_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Describe the basis for this estimate.")
        return value.strip()


def require_songs(db: Database, user_id: int, ids: list[int]) -> None:
    """Reject the whole selection if any id is not in this user's own catalog."""
    selected = set(ids)
    found = set()
    for start in range(0, len(ids), 400):
        part = ids[start:start + 400]
        found.update(r["song_id"] for r in db.all(
            "SELECT DISTINCT ci.song_id FROM catalog_items ci JOIN catalog_sources cs ON cs.id=ci.source_id "
            "JOIN accounts a ON a.id=cs.account_id WHERE a.user_id=? AND ci.song_id IN ("
            + ",".join("?" * len(part)) + ")", [user_id, *part]))
    if found != selected:
        raise HTTPException(404, "One or more songs are not in your library. Refresh the selection.")


def selected_songs(db: Database, user_id: int, ids: list[int]) -> dict[int, dict]:
    """Load songs and personal annotations in bounded batches; reject a partly foreign selection."""
    unique = list(dict.fromkeys(ids))
    songs = {}
    for start in range(0, len(unique), SONG_BATCH_SIZE):
        part = unique[start:start + SONG_BATCH_SIZE]
        rows = db.all(
            "SELECT s.id,s.title,s.artist,s.album,s.duration,ma.data,ma.origin FROM songs s "
            "LEFT JOIN music_annotations ma ON ma.song_id=s.id AND ma.user_id=? "
            "WHERE s.id IN (" + ",".join("?" * len(part)) + ") AND EXISTS ("
            "SELECT 1 FROM catalog_items ci JOIN catalog_sources cs ON cs.id=ci.source_id "
            "JOIN accounts a ON a.id=cs.account_id WHERE ci.song_id=s.id AND a.user_id=?)",
            [user_id, *part, user_id])
        for row in rows:
            data = row.pop("data")
            row["annotation"] = json.loads(data) if data else None
            songs[row["id"]] = row
    if len(songs) != len(unique):
        raise HTTPException(404, "One or more songs are not in your library. Refresh the selection.")
    return songs


def save_annotations(db: Database, user_id: int, entries: list[Annotation], origin: str = "assistant") -> dict:
    require_songs(db, user_id, [e.song_id for e in entries])
    saved, protected = [], []
    with db.tx() as c:
        for entry in entries:
            old = c.execute("SELECT origin FROM music_annotations WHERE user_id=? AND song_id=?",
                            (user_id, entry.song_id)).fetchone()
            if old and old[0] == "user" and origin != "user":
                protected.append(entry.song_id)
                continue
            c.execute("INSERT INTO music_annotations(user_id,song_id,data,origin,updated_at) VALUES(?,?,?,?,?) "
                      "ON CONFLICT(user_id,song_id) DO UPDATE SET data=excluded.data,origin=excluded.origin,"
                      "updated_at=excluded.updated_at",
                      (user_id, entry.song_id, entry.model_dump_json(exclude={"song_id"}), origin, time.time()))
            saved.append(entry.song_id)
    return {"saved": saved, "protected_corrections": protected}


def labels(db: Database, user_id: int) -> dict:
    """The person's vocabulary: every mood, genre and tag in use, with how many songs carry it.
    Reusing these exact labels keeps playlists reproducible instead of inventing synonyms."""
    out: dict = {}
    for key in ("moods", "genres", "tags"):
        out[key] = db.all(f"""SELECT j.value AS label, COUNT(*) AS songs, SUM(ma.origin='user') AS corrected
                              FROM music_annotations ma, json_each(ma.data, '$.{key}') j
                              WHERE ma.user_id=? GROUP BY j.value ORDER BY songs DESC, j.value LIMIT 300""",
                          (user_id,))
    counts = db.one("SELECT COUNT(*) AS annotated, COALESCE(SUM(origin='user'), 0) AS corrected "
                    "FROM music_annotations WHERE user_id=?", (user_id,))
    return {**out, **counts,
            "unclassified": library(db, user_id, unclassified=True, limit=1)["total"],
            "note": "Labels are lower-case and matched exactly. Corrected songs were labelled by the person."}


def clear_annotations(db: Database, user_id: int, song_ids: list[int]) -> dict:
    """Forget the assistant's estimates for these songs. Personal corrections always stay."""
    ids = list(dict.fromkeys(song_ids))
    cleared = 0
    for start in range(0, len(ids), SONG_BATCH_SIZE):
        part = ids[start:start + SONG_BATCH_SIZE]
        cleared += db.run("DELETE FROM music_annotations WHERE user_id=? AND origin<>'user' AND song_id IN ("
                          + ",".join("?" * len(part)) + ")", [user_id, *part])
    return {"cleared": cleared}


def library(db: Database, user_id: int, query: str = "", source_id: int | None = None,
            missing_on: str | None = None, absent_from: int | None = None, mood: str = "", genre: str = "",
            tag: str = "", unassigned: bool = False, unclassified: bool = False,
            limit: int = 100, offset: int = 0) -> dict:
    if missing_on and missing_on not in SERVICES:
        raise HTTPException(400, "Unknown service.")
    for sid in (source_id, absent_from):
        if sid is not None and not db.val("SELECT 1 FROM catalog_sources cs JOIN accounts a ON a.id=cs.account_id "
                                         "WHERE cs.id=? AND a.user_id=?", (sid, user_id)):
            raise HTTPException(404, "Collection not found.")
    limit, offset = max(1, min(limit, 250)), max(0, offset)
    args: list = [user_id, user_id]
    where = ["EXISTS (SELECT 1 FROM catalog_items ci JOIN catalog_sources cs ON cs.id=ci.source_id "
             "JOIN accounts a ON a.id=cs.account_id WHERE ci.song_id=s.id AND a.user_id=? AND cs.included=1)"]
    if query.strip():
        pattern = "%" + query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(s.title LIKE ? ESCAPE '\\' OR s.artist LIKE ? ESCAPE '\\' OR s.album LIKE ? ESCAPE '\\')")
        args.extend([pattern] * 3)
    if source_id is not None:
        where.append("EXISTS (SELECT 1 FROM catalog_items ci WHERE ci.song_id=s.id AND ci.source_id=?)")
        args.append(source_id)
    if absent_from is not None:
        where.append("NOT EXISTS (SELECT 1 FROM catalog_items ci WHERE ci.song_id=s.id AND ci.source_id=?)")
        args.append(absent_from)
    if missing_on:
        where.append("NOT EXISTS (SELECT 1 FROM catalog_items ci JOIN catalog_sources cs ON cs.id=ci.source_id "
                     "JOIN accounts a ON a.id=cs.account_id WHERE ci.song_id=s.id AND a.user_id=? AND a.service=?)")
        args.extend([user_id, missing_on])
    if unassigned:
        where.append("NOT EXISTS (SELECT 1 FROM catalog_items ci JOIN catalog_sources cs ON cs.id=ci.source_id "
                     "JOIN accounts a ON a.id=cs.account_id WHERE ci.song_id=s.id AND a.user_id=? "
                     "AND cs.kind='playlist' AND cs.editable=1)")
        args.append(user_id)
    if unclassified:
        where.append("ma.song_id IS NULL")
    for key, value in (("moods", mood), ("genres", genre), ("tags", tag)):
        if value.strip():
            where.append(f"EXISTS (SELECT 1 FROM json_each(ma.data, '$.{key}') WHERE value=?)")
            args.append(value.strip().casefold())
    sql = ("FROM songs s LEFT JOIN music_annotations ma ON ma.song_id=s.id AND ma.user_id=? WHERE "
           + " AND ".join(where))
    total = db.val("SELECT COUNT(*) " + sql, args)
    rows = db.all("SELECT s.id,s.title,s.artist,s.album,s.duration,s.isrc,ma.data,ma.origin,ma.updated_at "
                  + sql + " ORDER BY s.artist COLLATE NOCASE,s.title COLLATE NOCASE,s.id LIMIT ? OFFSET ?",
                  [*args, limit, offset])
    by_id = {row["id"]: row for row in rows}
    for row in rows:
        data = row.pop("data")
        row["annotation"] = json.loads(data) if data else None
        row["sources"] = []
    if by_id:
        sources = db.all("SELECT ci.song_id,cs.id,cs.name,cs.kind,a.service,ci.ref_id FROM catalog_items ci "
                         "JOIN catalog_sources cs ON cs.id=ci.source_id JOIN accounts a ON a.id=cs.account_id "
                         "WHERE a.user_id=? AND ci.song_id IN (" + ",".join("?" * len(by_id)) + ") ORDER BY cs.id",
                         [user_id, *by_id])
        for source in sources:
            by_id[source.pop("song_id")]["sources"].append(source)
    return {"songs": rows, "total": total, "offset": offset,
            "next_offset": offset + len(rows) if offset + len(rows) < total else None,
            "scanned_at": db.setting(f"catalog_scanned_{user_id}"),
            "metadata_note": "Annotations are estimates, not audio analysis. User corrections take precedence. "
                             "Unknown mood, genre, language, year and energy must stay unknown."}
