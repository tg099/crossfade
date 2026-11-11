"""Global song identity: one `songs` row per real song, `song_refs` per service id."""
from __future__ import annotations

import json
import re
import time
import unicodedata

from . import matcher, versions
from .db import Database
from .models import ApiError, AuthError, Track
from .providers.base import Provider

SEARCH_RETRY_DAYS = 7
# results that were not the exact version are searched again when the matcher got better since
MATCHER_CHANGED_AT = 1794408701


class Identity:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------ refs
    @staticmethod
    def _ref_row(t: Track, song_id: int) -> tuple:
        return (t.service, t.id, song_id, t.title, t.artist, t.album, t.duration, t.isrc, t.url,
                None if t.explicit is None else int(t.explicit), t.album_type, t.video_type, t.artwork)

    _INSERT_REF = ("INSERT OR IGNORE INTO song_refs(service,ref_id,song_id,title,artist,album,duration,isrc,url,"
                   "explicit,album_type,video_type,artwork) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)")

    def song_for(self, t: Track) -> int:
        """Song id for a service track; creates the song (or links by ISRC) when unknown."""
        db = self.db
        row = db.one("SELECT song_id, title, artist, album FROM song_refs WHERE service=? AND ref_id=?",
                     (t.service, t.id))
        if row:
            if t.title and (t.title, t.artist) != (row["title"], row["artist"]):
                # the service describes it better now (e.g. YouTube song details instead of the video
                # title); the song row follows when it was named after this ref
                with db.tx() as c:
                    c.execute("UPDATE song_refs SET title=?, artist=?, album=COALESCE(NULLIF(?,''), album) "
                              "WHERE service=? AND ref_id=?", (t.title, t.artist, t.album, t.service, t.id))
                    c.execute("UPDATE songs SET title=?, artist=? WHERE id=? AND title=? AND artist=?",
                              (t.title, t.artist, row["song_id"], row["title"], row["artist"]))
            db.run("UPDATE song_refs SET isrc=COALESCE(isrc, ?), explicit=COALESCE(?, explicit), "
                   "album_type=COALESCE(?, album_type), video_type=COALESCE(?, video_type), "
                   "artwork=COALESCE(?, artwork) WHERE service=? AND ref_id=?",
                   (t.isrc, None if t.explicit is None else int(t.explicit), t.album_type, t.video_type,
                    t.artwork, t.service, t.id))
            if t.isrc:
                db.run("UPDATE songs SET isrc=COALESCE(isrc, ?) WHERE id=?", (t.isrc.upper(), row["song_id"]))
            return row["song_id"]
        song_id = None
        if t.isrc:
            song_id = db.val("SELECT id FROM songs WHERE isrc=? ORDER BY id LIMIT 1", (t.isrc.upper(),))
        with db.tx() as c:
            if song_id is None:
                song_id = c.execute(
                    "INSERT INTO songs(title,artist,album,duration,isrc,created_at) VALUES(?,?,?,?,?,?)",
                    (t.title, t.artist, t.album, t.duration, t.isrc.upper() if t.isrc else None, time.time())
                ).lastrowid
            c.execute(self._INSERT_REF, self._ref_row(t, song_id))
        return song_id

    def link(self, song_id: int, t: Track) -> int | None:
        """Record that `t` is `song_id` on its service. If `t` already belongs to another song,
        that song is merged into `song_id`; its (now deleted) id is returned so callers can remap."""
        row = self.db.one("SELECT song_id FROM song_refs WHERE service=? AND ref_id=?", (t.service, t.id))
        if row and row["song_id"] != song_id:
            self.merge(song_id, row["song_id"])
            return row["song_id"]
        if not row:
            self.db.run(self._INSERT_REF, self._ref_row(t, song_id))
            if t.isrc:
                self.db.run("UPDATE songs SET isrc=COALESCE(isrc, ?) WHERE id=?", (t.isrc.upper(), song_id))
        return None

    def merge(self, keep: int, drop: int) -> int:
        if keep == drop:
            return keep
        with self.db.tx() as c:
            c.execute("UPDATE song_refs SET song_id=? WHERE song_id=?", (keep, drop))
            c.execute("INSERT OR IGNORE INTO line_items(line_id,song_id,added_at) "
                      "SELECT line_id,?,added_at FROM line_items WHERE song_id=?", (keep, drop))
            c.execute("INSERT OR IGNORE INTO member_items(member_id,song_id,ref_id,confirmed,excluded,misses) "
                      "SELECT member_id,?,ref_id,confirmed,excluded,misses FROM member_items WHERE song_id=?",
                      (keep, drop))
            c.execute("UPDATE OR IGNORE issues SET song_id=? WHERE song_id=?", (keep, drop))
            c.execute("UPDATE catalog_items SET song_id=? WHERE song_id=?", (keep, drop))
            # A personal correction wins over an assistant's annotation when identities merge.
            c.execute("""INSERT INTO music_annotations(user_id,song_id,data,origin,updated_at)
                         SELECT user_id,?,data,origin,updated_at FROM music_annotations WHERE song_id=?
                         ON CONFLICT(user_id,song_id) DO UPDATE SET data=excluded.data,
                         origin=excluded.origin, updated_at=excluded.updated_at
                         WHERE music_annotations.origin<>'user' AND excluded.origin='user'""", (keep, drop))
            c.execute("UPDATE changes SET song_id=? WHERE song_id=?", (keep, drop))
            c.execute("DELETE FROM search_cache WHERE song_id IN (?,?)", (keep, drop))
            c.execute("UPDATE songs SET isrc=COALESCE(isrc,(SELECT isrc FROM songs WHERE id=?)) WHERE id=?",
                      (drop, keep))
            c.execute("DELETE FROM songs WHERE id=?", (drop,))
        return keep

    @staticmethod
    def _track(r: dict) -> Track:
        return Track(service=r["service"], id=r["ref_id"], title=r["title"] or "", artist=r["artist"] or "",
                     album=r["album"] or "", duration=r["duration"], isrc=r["isrc"], url=r["url"],
                     explicit=None if r.get("explicit") is None else bool(r["explicit"]),
                     album_type=r.get("album_type"), video_type=r.get("video_type"), artwork=r.get("artwork"))

    def refs(self, song_id: int, service: str) -> list[Track]:
        return [self._track(r) for r in self.db.all(
            "SELECT * FROM song_refs WHERE song_id=? AND service=?", (song_id, service))]

    def describe(self, song_id: int, prefer: Track | None = None) -> Track:
        """The version to look for. `prefer` is the track where the song came from (its version
        counts); otherwise a ref with ISRC that is not a music video."""
        if prefer is not None and not _is_video(prefer):
            return prefer
        s = self.db.one("SELECT * FROM songs WHERE id=?", (song_id,)) or {}
        refs = self.db.all("""SELECT * FROM song_refs WHERE song_id=? ORDER BY
                              (video_type IN ('MUSIC_VIDEO_TYPE_OMV','MUSIC_VIDEO_TYPE_UGC')), (isrc IS NULL), service""",
                           (song_id,))
        if refs:
            t = self._track(refs[0])
            t.isrc = t.isrc or s.get("isrc")
            return t
        return Track(service="", id="", title=s.get("title", ""), artist=s.get("artist", ""),
                     album=s.get("album", ""), duration=s.get("duration"), isrc=s.get("isrc"))

    # ------------------------------------------------------------------ YouTube: video or song
    COUNTERPART_RETRY = 30 * 86400

    def counterpart(self, t: Track, provider: Provider) -> Track | None:
        """The other form of a YouTube track (music video <-> audio track), remembered per video."""
        fn = getattr(provider, "counterpart", None)
        row = self.db.one("SELECT counterpart, checked_at FROM yt_counterparts WHERE video_id=?", (t.id,))
        if row and (row["counterpart"] or time.time() - row["checked_at"] < self.COUNTERPART_RETRY):
            if not row["counterpart"]:
                return None
            known = self.db.one("SELECT * FROM song_refs WHERE service=? AND ref_id=?", (t.service, row["counterpart"]))
            if known:
                return self._track(known)
        if fn is None:
            return None
        cp = fn(t.id)
        self.db.run("INSERT INTO yt_counterparts(video_id,counterpart,checked_at) VALUES(?,?,?) ON CONFLICT(video_id) "
                    "DO UPDATE SET counterpart=excluded.counterpart, checked_at=excluded.checked_at",
                    (t.id, cp.id if cp else "", time.time()))
        if cp:
            self.db.run("INSERT INTO yt_counterparts(video_id,counterpart,checked_at) VALUES(?,?,?) "
                        "ON CONFLICT(video_id) DO NOTHING", (cp.id, t.id, time.time()))
        return cp

    def in_format(self, song_id: int, t: Track, provider: Provider, fmt: str | None) -> tuple[Track, int | None]:
        """`t` as the user wants it on YouTube: the music video ("video") or the audio track ("song").
        Falls back to `t` when the other form does not exist. Returns the track and, like link(),
        a song id that was merged away."""
        if t.service != "ytm" or fmt not in ("song", "video") or _is_video(t) == (fmt == "video"):
            return t, None
        want_video = fmt == "video"
        # as "the video" only an official one: a fan upload linked to the song (lyrics video, live
        # clip, re-upload) is that person's choice, not the song's video for every other collection
        known = [r for r in self.refs(song_id, t.service)
                 if (r.video_type in OFFICIAL_VIDEOS if want_video else not _is_video(r))]
        known.sort(key=lambda r: r.video_type != "MUSIC_VIDEO_TYPE_OMV")
        if known:
            return known[0], None
        try:
            cp = self.counterpart(t, provider)
        except (ApiError, AuthError):
            return t, None
        if not cp or _is_video(cp) != want_video:
            return t, None
        return cp, self.link(song_id, cp)

    # ------------------------------------------------------------------ search
    HELPERS = 2

    def helpers(self, song_id: int, service: str, src: Track) -> list[Track]:
        """The song as other services know it, to search `service` with when the source's own details
        are not enough (a YouTube upload title, no ISRC). Only refs that are the source's version."""
        out: list[tuple[float, Track]] = []
        for r in self.db.all("SELECT * FROM song_refs WHERE song_id=? AND service!=?", (song_id, service)):
            t = self._track(r)
            if (t.service, t.id) == (src.service, src.id) or _is_video(t):
                continue
            a = matcher.assess(src, t)
            if a.score < matcher.ACCEPT or a.hard_mismatch or not (a.exact or matcher.is_video(src)):
                continue
            out.append((a.rank_key + (0.5 if t.isrc else 0), t))
        out.sort(key=lambda x: x[0], reverse=True)
        return [t for _, t in out[:self.HELPERS]]

    def find(self, song_id: int, service: str, provider: Provider, use_cache: bool = True,
             source: Track | None = None) -> Match:
        """Find the song on `service`, version-aware. Does not link; callers decide (policy)."""
        existing = self.refs(song_id, service)
        if existing:
            # several versions may be known on this service: a version chosen by hand (cache)
            # wins, otherwise the one that fits the source best
            chosen = self.db.val("SELECT best_ref FROM search_cache WHERE service=? AND song_id=? AND exact=1",
                                 (service, song_id))
            pick = next((t for t in existing if t.id == chosen), None)
            ref_src = source or self.describe(song_id)
            if pick is None:
                pick = max(existing, key=lambda t: 2.0 if t.id == ref_src.id else matcher.assess(ref_src, t).rank_key)
            a = matcher.assess(ref_src, pick) if pick.id != ref_src.id else None
            exact = a is None or a.exact or pick.id == chosen
            notes = [] if exact or a is None else a.notes
            return Match(pick, 1.0, exact, notes, [dict(pick.to_dict(), score=1.0, exact=exact, notes=notes)])
        src = self.describe(song_id, source)
        helpers = self.helpers(song_id, service, src)
        helper_sig = ",".join(sorted(f"{h.service}:{h.id}" for h in helpers)) or None
        if use_cache:
            c = self.db.one("SELECT * FROM search_cache WHERE service=? AND song_id=?", (service, song_id))
            stale = c and not c["exact"] and (time.time() - c["created_at"] > SEARCH_RETRY_DAYS * 86400
                                              or c["created_at"] < MATCHER_CHANGED_AT
                                              # another service found the song since: search with its details
                                              or (helper_sig and helper_sig != c["helpers"]))
            if c and not stale:
                cands = json.loads(c["candidates"] or "[]")
                best = next((Track.from_dict(x) for x in cands if x["id"] == c["best_ref"]), None)
                return Match(best, c["score"], bool(c["exact"]), json.loads(c["notes"] or "[]"), cands)

        seen: dict[str, Track] = {}
        scored: list[tuple[Track, matcher.Assessment]] = []
        answered = 0
        last_error: ApiError | None = None
        guides: list[Track] = [src]      # what candidates are compared with (the source, then helpers)

        def judge(t: Track) -> matcher.Assessment:
            return max((matcher.assess(g, t) for g in guides), key=lambda a: a.rank_key)

        def collect(res: list[Track]) -> None:
            nonlocal scored
            for t in res:
                seen.setdefault(t.id, t)
            scored = sorted(((t, judge(t)) for t in seen.values()), key=lambda x: x[1].rank_key, reverse=True)

        def certain() -> bool:
            return bool(scored and scored[0][1].score >= matcher.ACCEPT)

        def text_search(queries: list[tuple[str, str]]) -> None:
            nonlocal answered, last_error
            for kind, q in queries:
                if kind == "last" and scored and scored[0][1].score >= matcher.SUGGEST:
                    break                                 # the title alone only when nothing came close
                try:
                    res = provider.by_isrc(q) if kind == "isrc" else provider.search(q, limit=10)
                except AuthError:
                    raise
                except ApiError as e:
                    last_error = e
                    continue
                if kind != "isrc":
                    answered += 1
                collect(res)
                # stop once the *exact* version is found (or something unmistakable), not just something close
                if certain() and (scored[0][1].exact or scored[0][1].score >= 0.95):
                    break

        text_search(search_queries(src))

        # not the exact version yet: the song is known on another service (YouTube title found on
        # Spotify, but not on Apple Music) – search again with that service's details (ISRC, clean
        # title and artist). Candidates are compared with the helper as well as with the source.
        for h in helpers:
            if certain() and scored[0][1].exact:
                break
            guides.append(h)
            asked = {(k, matcher._base(q)) for k, q in search_queries(src)}
            text_search([(k, q) for k, q in search_queries(h) if (k, matcher._base(q)) not in asked])
            collect([])

        # nothing certain yet: look through the artist's own catalog (search engines miss new or
        # oddly titled releases that the artist page lists fine)
        if not certain():
            browse = getattr(provider, "artist_tracks", None)
            for artist in _artists_to_browse(src) if browse else []:
                try:
                    res = browse(artist)
                except AuthError:
                    raise
                except ApiError as e:
                    last_error = e
                    continue
                answered += bool(res)
                collect(res)
                if certain():
                    break

        # still nothing: ask a second search engine with the artist first (Apple's catalog search misses
        # songs the iTunes search finds at once, e.g. "Coyote Kisses Six Shooter")
        alt = getattr(provider, "search_alt", None)
        if alt and not certain():
            for q in dict.fromkeys(q for g in guides for q in alt_queries(g)):
                try:
                    res = alt(q, limit=10)
                except AuthError:
                    raise
                except ApiError as e:
                    last_error = e
                    continue
                answered += 1
                collect(res)
                if certain():
                    break

        # a renamed artist: identical title and length, and no other artist has a song of that title
        # in the results ("B4LLER" by TJ_babybrain on YouTube is "B4LLER" by beastboy elsewhere)
        if scored and not certain():
            t, a = scored[0]
            g = next((g for g in guides if matcher.same_but_artist(g, t)), None)
            # another artist's song of that title and about that length makes it ambiguous ("Hello")
            rivals = [x for x, _ in scored if matcher._base(x.artist) != matcher._base(t.artist)
                      and any(_same_title(g, x) and _near(g, x, 10) for g in guides)]
            if g is not None and not a.hard_mismatch and not rivals:
                scored[0] = (t, matcher.Assessment(matcher.ACCEPT, matcher.ACCEPT, False,
                                                   [matcher.RENAMED] + a.notes))

        # `fits`: may be preselected for a person; `version`: what kind of recording it is ("Live" …)
        cands = [dict(t.to_dict(), score=a.score, exact=a.exact, notes=a.notes, fits=a.fits,
                      version=matcher.version(t).label())
                 for t, a in scored[:6] if a.score >= matcher.SUGGEST]
        best, a = scored[0] if scored else (None, None)
        if a is None or a.score < matcher.SUGGEST:
            best = None
        m = Match(best, a.score if a else 0.0, bool(a and a.exact), a.notes if a else [], cands)
        if not answered and not m.exact and last_error is not None:
            # the service refused every text search (rate limit, outage): this says nothing about the
            # song, so don't remember it as "not found" – the next run searches again
            return m
        self.db.run("INSERT INTO search_cache(service,song_id,best_ref,score,candidates,created_at,exact,notes,helpers) "
                    "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(service,song_id) DO UPDATE SET best_ref=excluded.best_ref, "
                    "score=excluded.score, candidates=excluded.candidates, created_at=excluded.created_at, "
                    "exact=excluded.exact, notes=excluded.notes, helpers=excluded.helpers",
                    (service, song_id, best.id if best else None, m.score, json.dumps(cands), time.time(),
                     int(m.exact), json.dumps(m.notes), helper_sig))
        return m


def _artists_to_browse(src: Track) -> list[str]:
    names: list[str] = [r for r in matcher.remixers(src.title)][:1]    # "(MRJay Remix)" -> MRJay's catalog
    for r in matcher.readings(src)[:2]:
        for a in [r.artist.split(",")[0].strip()] + [x.strip() for x in matcher._ARTIST_SPLIT.split(r.artist)][:1]:
            if a and matcher._base(a) not in {matcher._base(n) for n in names}:
                names.append(a)
    return names[:3]


_SYMBOLS = re.compile(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]")


def _plain(q: str) -> str:
    """Search engines choke on emoji and fancy unicode ("WMNB🍻🥨", "𝘛𝘩𝘦 𝘚𝘩𝘰𝘸")."""
    q = unicodedata.normalize("NFKC", _SYMBOLS.sub(" ", q))
    return re.sub(r"\s+", " ", q).strip()


def alt_queries(src: Track) -> list[str]:
    r = matcher.readings(src)[0]
    title = matcher.norm_title(r.title)
    out = [f"{(matcher.artist_list(r.artist)[:1] or [''])[0]} {title}"]
    out += [f"{x} {title}" for x in matcher.remixers(r.title)[:1]]
    return [_plain(q) for q in out if len(title) >= 2][:2]


def search_queries(src: Track) -> list[tuple[str, str]]:
    """Search terms, most specific first. Videos are searched by their tidied reading and by their
    title as uploaded (it may read "Title - Artist"); the bare title comes last."""
    out: list[tuple[str, str]] = []
    if src.isrc:
        out.append(("isrc", src.isrc))
    for i, r in enumerate(matcher.readings(src)[:2]):
        title = versions.strip_noise(r.title)
        clean = matcher.norm_title(r.title)
        first = r.artist.split(",")[0].strip()
        primary = (matcher.artist_list(r.artist)[:1] or [""])[0]
        out.append(("text", f"{clean} {first}"))
        if i == 0:
            for rx in matcher.remixers(r.title)[:1]:
                out.append(("text", f"{clean} {rx}"))      # the remix is often listed under the remixer
            out.append(("text", f"{title} {r.artist}"))
            if r.album:
                out.append(("text", f"{clean} {first} {matcher.norm_title(r.album)}"))
            if primary:
                out.append(("text", f"{title} {primary}"))
    if matcher.is_video(src) or src.service == "ytm":
        out.append(("text", versions.strip_noise(src.title)))
    clean = matcher.norm_title(matcher.readings(src)[0].title)
    if len(clean) >= 4:
        out.append(("last", clean))
    seen: set[str] = set()
    uniq = []
    for kind, q in out:
        q = q if kind == "isrc" else _plain(q)
        key = kind if kind == "isrc" else "text"
        k = (key, matcher._base(q))
        if q.strip() and k not in seen:
            seen.add(k)
            uniq.append((kind, q.strip()))
    return uniq


def _same_title(src: Track, cand: Track) -> bool:
    return any(matcher._base(matcher.norm_title(s.title)) == matcher._base(matcher.norm_title(c.title))
               for s in matcher.readings(src) for c in matcher.readings(cand))


def _near(a: Track, b: Track, secs: int) -> bool:
    return not (a.duration and b.duration) or abs(a.duration - b.duration) <= secs


def _is_video(t: Track) -> bool:
    return t.video_type in matcher.VIDEO_TYPES


OFFICIAL_VIDEOS = ("MUSIC_VIDEO_TYPE_OMV", "MUSIC_VIDEO_TYPE_OFFICIAL_SOURCE_MUSIC")


class Match:
    def __init__(self, track: Track | None, score: float, exact: bool, notes: list[str], candidates: list[dict]):
        self.track, self.score, self.exact, self.notes, self.candidates = track, score, exact, notes, candidates

    def accepted(self, policy: str) -> bool:
        if not self.track or self.score < matcher.ACCEPT:
            return False
        return self.exact or policy != "exact"

    def note(self) -> str | None:
        if self.exact:
            return None
        if matcher.RENAMED in self.notes:
            return ", ".join(self.notes)
        return "different version: " + ", ".join(self.notes) if self.notes else "similar version"
