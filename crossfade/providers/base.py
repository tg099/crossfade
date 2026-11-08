"""Common interface every music service implements."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from ..models import ApiError, Collection, NotSupported, Track


class Provider(ABC):
    service: str

    def __init__(self, credentials: dict[str, Any], save_credentials: Callable[[dict], None] | None = None):
        self.credentials = credentials
        self._save = save_credentials or (lambda c: None)

    def persist(self) -> None:
        """Store refreshed tokens."""
        self._save(self.credentials)

    # ------------------------------------------------------------------ account
    @abstractmethod
    def whoami(self) -> str:
        """Display name of the connected account; raises AuthError when not logged in."""

    # ------------------------------------------------------------------ collections
    @abstractmethod
    def collections(self) -> list[Collection]: ...

    @abstractmethod
    def read(self, kind: str, collection_id: str) -> list[Track]: ...

    @abstractmethod
    def add(self, kind: str, collection_id: str, tracks: list[Track]) -> list[str]:
        """Add tracks; returns ids that failed (others succeeded)."""

    @abstractmethod
    def remove(self, kind: str, collection_id: str, tracks: list[Track]) -> list[str]:
        """Remove tracks (as returned by read(), with handles); returns ids that failed."""

    @abstractmethod
    def create_playlist(self, name: str, description: str = "") -> Collection: ...

    def delete_playlist(self, collection_id: str) -> None:
        raise NotSupported("Playlists can't be deleted here.")

    def remove_copies(self, kind: str, collection_id: str, track: Track) -> None:
        """Keep a single entry of a track that a playlist lists several times (`track` comes from
        read(), its further copies in handle['extra']). Most services can only remove a track with
        all its copies, so it is removed and added once more – it moves to the end."""
        if self.remove(kind, collection_id, [track]):
            raise ApiError("couldn't be removed")
        if self.add(kind, collection_id, [track]):
            raise CopiesLost("was removed but couldn't be added back")

    # ------------------------------------------------------------------ catalog
    @abstractmethod
    def search(self, query: str, limit: int = 10) -> list[Track]: ...

    def by_isrc(self, isrc: str) -> list[Track]:
        return []

    def artist_tracks(self, artist: str) -> list[Track]:
        """All songs of the artist with this name (best effort). Used when searching for the
        title fails: the catalog of the right artist almost always contains the song."""
        return []

    @abstractmethod
    def track(self, track_id: str) -> Track | None: ...

    @staticmethod
    def parse_link(text: str) -> str | None:
        """Turn a pasted share link or id into a catalog id."""
        return None


class CopiesLost(ApiError):
    """remove_copies removed every copy and could not add the track back."""


def unique(tracks: list[Track | None]) -> list[Track]:
    """One track per id, in order. Further copies (the same song twice in a playlist) are counted
    in handle['copies'], their own handles kept in handle['extra'] so just those can be removed."""
    seen: dict[str, Track] = {}
    for t in tracks:
        if not t:
            continue
        first = seen.get(t.id)
        if first is None:
            seen[t.id] = t
        else:
            first.handle.setdefault("extra", []).append(dict(t.handle))
            first.handle["copies"] = 1 + len(first.handle["extra"])
    return list(seen.values())


def chunks(items: list, n: int):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def retry(fn: Callable[[], Any], attempts: int = 4, base_delay: float = 1.5,
          retry_on: tuple[type[BaseException], ...] = (Exception,),
          giveup: tuple[type[BaseException], ...] = ()) -> Any:
    last: BaseException | None = None
    for i in range(attempts):
        try:
            return fn()
        except giveup:
            raise
        except retry_on as e:  # noqa: PERF203
            last = e
            time.sleep(base_delay * (i + 1))
    assert last is not None
    raise last
