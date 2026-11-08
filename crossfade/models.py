from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

YTM = "ytm"
APPLE = "apple"
SPOTIFY = "spotify"
SERVICES = (YTM, APPLE, SPOTIFY)
SERVICE_NAMES = {YTM: "YouTube Music", APPLE: "Apple Music", SPOTIFY: "Spotify"}

LIKED = "liked"
LIBRARY = "library"
PLAYLIST = "playlist"


@dataclass
class Track:
    """A song as one service knows it. `id` is that service's catalog id
    (YTM videoId, Apple catalog song id, Spotify track id)."""

    service: str
    id: str
    title: str
    artist: str = ""
    album: str = ""
    duration: int | None = None  # seconds
    isrc: str | None = None
    url: str | None = None
    explicit: bool | None = None
    album_type: str | None = None   # album | single | compilation (when the service says)
    video_type: str | None = None   # YTM: MUSIC_VIDEO_TYPE_ATV (audio) / _OMV (official video) / _UGC
    artwork: str | None = None      # cover image URL (~300 px)
    # Collection-specific handle needed to remove the item again
    # (Apple library id, YTM setVideoId / feedback token, ...). Not persisted.
    handle: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("handle", None)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Track:
        keys = ("service", "id", "title", "artist", "album", "duration", "isrc", "url", "explicit", "album_type",
                "video_type", "artwork")
        return cls(**{k: d.get(k) for k in keys})

    def label(self) -> str:
        return f"{self.artist} – {self.title}" if self.artist else self.title


@dataclass
class Collection:
    kind: str           # liked | library | playlist
    id: str             # '' for liked/library
    name: str
    count: int | None = None
    editable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AuthError(Exception):
    """Credentials are missing, invalid or expired."""


class ApiError(Exception):
    """The remote service returned an unexpected error."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class NotSupported(ApiError):
    """The service cannot do this for this collection."""
