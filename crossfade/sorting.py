"""Sorting songs into playlists inside a fade.

A receiving collection can carry a rule: then it only takes the songs of the fade that fit it,
e.g. "moods: relaxed, calm" or "artists: Adele". A rule marked `rest` takes what fits no other
rule of the fade. A sending collection (typically liked songs) can let go of songs once they are
sorted (`move_sorted`), so it works like an inbox.

Moods, genres, tags, language and energy are personal labels (music.py): set by the person or
their AI assistant. A song without labels is not sorted by a rule that needs them – it waits until
it is labelled, it never lands in the "rest" by accident. Artist rules need no labels.
"""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import matcher
from .db import Database

BATCH = 400
LABEL_KEYS = ("moods", "genres", "tags")


def _clean(values: list[str]) -> list[str]:
    labels = list(dict.fromkeys(v.strip().casefold() for v in values))
    if any(not v or len(v) > 80 for v in labels):
        raise ValueError("Each entry needs 1–80 characters.")
    return labels


class Rule(BaseModel):
    """Every filled list must match (any entry of it); empty lists don't restrict."""
    model_config = ConfigDict(extra="forbid")
    moods: list[str] = Field(default_factory=list, max_length=30)
    genres: list[str] = Field(default_factory=list, max_length=30)
    tags: list[str] = Field(default_factory=list, max_length=30)
    artists: list[str] = Field(default_factory=list, max_length=100)
    languages: list[str] = Field(default_factory=list, max_length=20)
    energy: list[Literal["low", "medium", "high"]] = Field(default_factory=list, max_length=3)
    rest: bool = Field(False, description="Take every song that fits no other rule of the fade.")

    @field_validator("moods", "genres", "tags", "languages")
    @classmethod
    def _labels(cls, values: list[str]) -> list[str]:
        return _clean(values)

    @field_validator("artists")
    @classmethod
    def _artists(cls, values: list[str]) -> list[str]:
        names = list(dict.fromkeys(v.strip() for v in values if v.strip()))
        if any(len(v) > 120 for v in names):
            raise ValueError("Artist names can have at most 120 characters.")
        return names

    @field_validator("energy")
    @classmethod
    def _energy(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def _shape(self):
        lists = (self.moods, self.genres, self.tags, self.artists, self.languages, self.energy)
        if self.rest and any(lists):
            raise ValueError("A rest rule takes what no other rule takes; it has no conditions of its own.")
        if not self.rest and not any(lists):
            raise ValueError("A rule needs at least one condition (or rest).")
        return self

    @property
    def needs_labels(self) -> bool:
        return bool(self.moods or self.genres or self.tags or self.languages or self.energy)


def parse(raw: str | None) -> Rule | None:
    """A stored rule (None: the collection takes every song). Raises ValueError if unreadable."""
    return Rule.model_validate_json(raw) if raw else None


def _artist_keys(artist: str) -> set[str]:
    return {k for k in (*matcher.artist_list(artist), matcher._base(artist)) if k}


def fits(rule: Rule, artist: str, labels: dict | None) -> bool | None:
    """True or False; None while the answer depends on labels the song doesn't have yet."""
    if rule.artists and not {k for a in rule.artists if (k := matcher._base(a))} & _artist_keys(artist):
        return False
    if not rule.needs_labels:
        return True
    if labels is None:
        return None
    for key in LABEL_KEYS:
        wanted = getattr(rule, key)
        if wanted and not set(wanted) & {v.casefold() for v in labels.get(key) or []}:
            return False
    if rule.languages and (labels.get("language") or "").strip().casefold() not in rule.languages:
        return False
    if rule.energy and labels.get("energy") not in rule.energy:
        return False
    return True


class Sorter:
    """Which songs the rule members of one fade take. Labels are those of each member's owner
    (in a shared fade, everyone sorts into their own playlists with their own labels)."""

    def __init__(self, db: Database, members: list[dict]):
        """members: line_members rows with `rule` and the owner's user id as `user_id`."""
        self.db = db
        self.rules: dict[int, tuple[Rule, int]] = {}
        self.closed: set[int] = set()       # a rule that can't be read: sorts nothing rather than everything
        for m in members:
            if not m.get("rule") or m["role"] not in ("both", "receive"):
                continue
            try:
                self.rules[m["id"]] = (parse(m["rule"]), m["user_id"])
            except ValueError:
                self.closed.add(m["id"])
        self._artist: dict[int, str] = {}
        self._labels: dict[tuple[int, int], dict | None] = {}

    @property
    def active(self) -> bool:
        return bool(self.rules or self.closed)

    @property
    def needs_labels(self) -> bool:
        return any(r.needs_labels for r, _ in self.rules.values())

    def load(self, song_ids) -> None:
        """Artists and labels of these songs, in a few queries."""
        ids = [s for s in dict.fromkeys(song_ids) if s not in self._artist]
        owners = {u for _, u in self.rules.values()}
        for start in range(0, len(ids), BATCH):
            part = ids[start:start + BATCH]
            marks = ",".join("?" * len(part))
            for r in self.db.all(f"SELECT id, artist FROM songs WHERE id IN ({marks})", part):
                self._artist[r["id"]] = r["artist"] or ""
            for owner in owners:
                found = {r["song_id"]: json.loads(r["data"]) for r in self.db.all(
                    f"SELECT song_id, data FROM music_annotations WHERE user_id=? AND song_id IN ({marks})",
                    [owner, *part])}
                for sid in part:
                    self._labels[(owner, sid)] = found.get(sid)

    def _fits(self, member_id: int, sid: int) -> bool | None:
        rule, owner = self.rules[member_id]
        if sid not in self._artist:
            self.load([sid])
        return fits(rule, self._artist.get(sid, ""), self._labels.get((owner, sid)))

    def fits(self, member_id: int, sid: int) -> bool | None:
        """Whether this member takes the song: always without a rule, never with an unreadable one."""
        if member_id in self.closed:
            return False
        if member_id not in self.rules:
            return True
        rule, _ = self.rules[member_id]
        if not rule.rest:
            return self._fits(member_id, sid)
        others = [self._fits(mid, sid) for mid, (r, _) in self.rules.items() if not r.rest]
        if any(x is True for x in others):
            return False
        return None if any(x is None for x in others) else True

    def has_rule(self, member_id: int) -> bool:
        return member_id in self.rules or member_id in self.closed

    def waiting(self, song_ids) -> list[int]:
        """Songs some rule can't decide on yet: they need labels first."""
        ids = list(song_ids)
        self.load(ids)
        return [sid for sid in ids if any(self.fits(mid, sid) is None for mid in self.rules)]


def describe(rule: Rule | None) -> str:
    """A short English summary for previews and assistants."""
    if rule is None:
        return "every song"
    if rule.rest:
        return "everything no other rule takes"
    parts = [f"{k}: {', '.join(getattr(rule, k))}" for k in ("moods", "genres", "tags", "artists", "languages", "energy")
             if getattr(rule, k)]
    return "; ".join(parts)
