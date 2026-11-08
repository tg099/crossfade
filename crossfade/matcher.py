"""Fuzzy matching of songs across services: title, artist, duration, ISRC – and the *version*
(live, remix, remaster, radio edit, explicit …, see versions.py)."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace

from rapidfuzz import fuzz, process

from . import versions
from .models import Track

ACCEPT = 0.82   # auto-match at or above this score
SUGGEST = 0.50  # keep as suggestion for manual review at or above this score
HARD_CAP = 0.66  # a different recording (live vs studio …) is at most a suggestion

_FEAT = re.compile(r"\b(feat\.?|ft\.?|featuring)\s[^()\[\]]*", re.I)
_TOPIC = re.compile(r"\s-\stopic$", re.I)
_ARTIST_SPLIT = re.compile(r"\s*(?:,|&|\+|/|\bx\b|\band\b|\bund\b|\bfeat\.?|\bft\.?|\bfeaturing\b|\bwith\b)\s*", re.I)
_QUALIFIER = re.compile(
    r"\b(feat|ft|featuring|with|from|aus|explicit|clean|version|edit|mix|remix|remaster(ed)?|live|acoustic|"
    r"instrumental|mono|stereo|deluxe|extended|radio|single|album|bonus|demo|sped|slowed|karaoke|original|"
    r"recorded|taylor|soundtrack|ost|anniversary|edition|expanded|ep|\d{4})\b", re.I)


def _base(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_title(title: str) -> str:
    """The bare song title: version qualifiers in brackets or after ' - ' are removed
    (they are judged separately by versions.compare)."""
    t = versions.strip_noise(title)
    t = re.sub(r"[(\[]([^)\]]*)[)\]]", lambda m: " " if _QUALIFIER.search(m.group(1)) else m.group(0), t)
    t = re.sub(r"\s[-–]\s(.+)$", lambda m: " " if _QUALIFIER.search(m.group(1)) else m.group(0), t)
    t = _FEAT.sub(" ", t)
    return _base(t)


_SEP = re.compile(r"\s+[-–—~]\s+")


def tidy_video(title: str, artist: str, video_type: str | None = None) -> tuple[str, str]:
    """YouTube uploads put the artist into the title: "Artist - Title (Official Video)",
    "Title - Artist (Lyrics)", "A x B ~ Title". Returns (title, artist) with the artist taken out;
    for user uploads / unknown channels the artist from the title wins over the channel name."""
    t, a = _tidy_once(title, artist, video_type)
    if (t, a) != ((title or "").strip(), artist):
        t2, a2 = _tidy_once(t, a, "MUSIC_VIDEO_TYPE_ATV")   # "A, B - A – Title": only strip a repeat
        return t2, a2
    return t, a


def _tidy_once(title: str, artist: str, video_type: str | None) -> tuple[str, str]:
    t = (title or "").strip()
    parts = _SEP.split(t, maxsplit=1)
    if len(parts) != 2:
        return t, artist
    left, right = parts[0].strip(), parts[1].strip()
    known = set(artist_list(artist))

    def same(x: str) -> bool:
        xs = artist_list(versions.strip_noise(x))
        return bool(xs) and bool(known) and (
            any(a in known for a in xs) or fuzz.ratio(" ".join(xs), " ".join(sorted(known))) >= 90)

    if same(left):
        return right, artist if len(artist_list(left)) <= len(known) else left
    if same(versions.strip_noise(right)) and not same(left):
        return left, artist
    if video_type == "MUSIC_VIDEO_TYPE_ATV":
        return t, artist          # real song titles may contain " - "
    if video_type in VIDEO_TYPES or video_type is None:
        return right, left        # channel name is a label/fan channel, the title says who it is
    return t, artist


def readings(t: Track) -> list[Track]:
    """Ways to read a video's title: tidied ("Artist - Title"), the other way round
    ("Title - Artist"), and as it is. Audio tracks have exactly one reading."""
    if t.service != "ytm" or t.video_type == "MUSIC_VIDEO_TYPE_ATV":
        return [t]
    out = [t]
    tt, aa = tidy_video(t.title, t.artist, t.video_type)
    if (tt, aa) != (t.title, t.artist):
        out.insert(0, replace(t, title=tt, artist=aa))
    parts = _SEP.split(versions.strip_noise(t.title), maxsplit=1)
    if len(parts) == 2:
        swapped = replace(t, title=parts[0].strip(), artist=parts[1].strip())
        if all((x.title, x.artist) != (swapped.title, swapped.artist) for x in out):
            out.insert(len(out) - 1, swapped)       # before the reading "as uploaded"
    return out


def artist_list(artist: str) -> list[str]:
    artist = _TOPIC.sub("", artist or "")
    parts = [_base(p) for p in _ARTIST_SPLIT.split(artist)]
    return [p for p in parts if p]


def _title_sim(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return max(fuzz.ratio(a, b), fuzz.token_sort_ratio(a, b)) / 100


_BRACKETS = re.compile(r"\s*[(\[][^)\]]*[)\]]")


def bare_title(title: str) -> str:
    """The title without *any* bracketed part – also subtitles like "(A Man After Midnight)",
    which one service prints and another leaves out."""
    t = _BRACKETS.sub(" ", versions.strip_noise(title))
    t = re.sub(r"\s[-–]\s.+$", " ", t)
    return _base(_FEAT.sub(" ", t))


def _titles_sim(src: str, cand: str) -> float:
    """Title similarity; a match that needs the subtitle dropped counts slightly less."""
    full = _title_sim(norm_title(src), norm_title(cand))
    if full >= 0.95:
        return full
    a, b = bare_title(src), bare_title(cand)
    if len(a) < 3 or len(b) < 3:
        return full
    return max(full, _title_sim(a, b) * 0.96)


# "(MRJay Remix)", "[Tiësto's VIP Mix]", "(Dark Boy Edit)": the named artist made this version and is
# often the credited artist on the other service ("East Coast Baby" by MRJay)
_REMIX_BY = re.compile(r"[(\[]\s*([^()\[\]]+?)(?:['’]s)?\s+(?:remix|rmx|edit|flip|bootleg|rework|vip|"
                       r"(?:vip |club |dub )?mix)\s*[)\]]", re.I)
_NOT_A_NAME = {"radio", "club", "extended", "original", "album", "single", "dub", "vocal", "instrumental", "main",
               "short", "long", "the", "official", "clean", "explicit", "dirty", "mono", "stereo", "new", "remastered",
               "remaster", "extended club", "full", "special", "video", "summer", "christmas", "acoustic", "live",
               "piano", "sped up", "slowed", "techno", "house", "trap", "edm", "dance", "deluxe", "tiktok"}


def remixers(title: str) -> list[str]:
    out = []
    for m in _REMIX_BY.finditer(title or ""):
        name = m.group(1).strip(" -–")
        if name and _base(name) not in _NOT_A_NAME and not re.fullmatch(r"\d{2,4}", name):
            out.append(name)
    return out


def _credited(remixer_titles: Track, other: Track) -> str | None:
    """A remixer named in one title that is the credited artist of the other track."""
    others = artist_list(other.artist)
    for r in remixers(remixer_titles.title):
        rb = _base(r)
        if any(fuzz.ratio(rb, o) >= 90 for o in others):
            return r
    return None


# YouTube uploads (not the auto-generated "Topic" audio) carry whatever label the uploader typed
_LOOSE_LABELS = ("MUSIC_VIDEO_TYPE_UGC", "MUSIC_VIDEO_TYPE_OMV")


def _loose(t: Track) -> bool:
    return t.service == "ytm" and t.video_type in _LOOSE_LABELS


def _artist_sim(a: str, b: str) -> float:
    la, lb = artist_list(a), artist_list(b)
    if not la or not lb:
        return 0.5
    primary = max(fuzz.ratio(la[0], x) for x in lb)
    whole = fuzz.token_set_ratio(" ".join(la), " ".join(lb))
    return max(primary, whole * 0.95) / 100


VIDEO_TYPES = ("MUSIC_VIDEO_TYPE_OMV", "MUSIC_VIDEO_TYPE_UGC")


def is_video(t: Track) -> bool:
    return t.video_type in VIDEO_TYPES


def _duration_sim(a: int | None, b: int | None, video: bool = False) -> float:
    """Same recording: ±2 s. Different edits differ by much more. A music video's length says
    little (intro, skits, outro), so there a difference only counts as 'unknown'."""
    if not a or not b:
        return 0.6
    d = abs(a - b)
    if d <= 2:
        return 1.0
    if video:
        return 0.6
    if d >= 15:
        return 0.0
    return 1 - (d - 2) / 13


def version(t: Track) -> versions.Version:
    return versions.version_of(t.title, t.album, t.explicit, t.album_type)


@dataclass
class Assessment:
    score: float                 # 0..1, shown to people and compared with ACCEPT
    raw: float
    exact: bool                  # same version as far as we can tell
    notes: list[str] = field(default_factory=list)
    hard_mismatch: bool = False

    @property
    def rank_key(self) -> float:
        return self.raw + (0.015 if self.exact else 0)

    def reason(self) -> str:
        return "same version" if self.exact else "different version: " + ", ".join(self.notes)


def assess(src: Track, cand: Track) -> Assessment:
    """Best reading wins: a YouTube video title may name the artist first or last."""
    best: Assessment | None = None
    for s in readings(src):
        for c in readings(cand):
            a = _assess(s, c)
            if best is None or a.rank_key > best.rank_key:
                best = a
    return best  # type: ignore[return-value]


def _assess(src: Track, cand: Track) -> Assessment:
    if src.isrc and cand.isrc and src.isrc.upper() == cand.isrc.upper():
        return Assessment(1.0, 1.1, True, ["gleiche ISRC"])
    t = _titles_sim(src.title, cand.title)
    a = _artist_sim(src.artist, cand.artist)
    vs, vc = version(src), version(cand)
    # "Song (X Remix)" by A  ==  "Song" by X: the remixer is credited as the artist
    if by := _credited(src, cand):
        a = max(a, _artist_sim(by, cand.artist))
        vc.hard.add("Remix")
    elif by := _credited(cand, src):
        a = max(a, _artist_sim(src.artist, by))
        vs.hard.add("Remix")
    video = is_video(src) or is_video(cand)
    d = _duration_sim(src.duration, cand.duration, video)
    s = 0.55 * t + 0.30 * a + 0.15 * d
    cmp = versions.compare(vs, vc)
    s -= cmp.penalty
    # both are remixes – but by the same person? ("Club Mix" vs "The Rocketman Remix" are two remixes)
    if "Remix" in vs.hard and "Remix" in vc.hard:
        # a remix credited as the artist ("East Coast Baby" by MRJay) is by that artist
        rs = [_base(x) for x in remixers(src.title)] or ([_base(by)] if by and _credited(cand, src) else [])
        rc = [_base(x) for x in remixers(cand.title)] or ([_base(by)] if by and _credited(src, cand) else [])
        if rs != rc and not any(fuzz.ratio(x, y) >= 90 for x in rs for y in rc):
            s -= 0.04
            cmp.exact = False
            cmp.notes.append("different remix")
            if rs and rc:                      # two named remixers: certainly two recordings
                cmp.hard_mismatch = True
    # an uploader's label ("Club Mix", "Remix") on a YouTube video, while the other side has no label at
    # all and the length is identical: that is the same recording, only named differently
    relabelled = False
    if cmp.hard_mismatch and src.duration and cand.duration and abs(src.duration - cand.duration) <= 2 \
            and t >= 0.85 and a >= 0.85:
        only_src, only_cand = vs.hard - vc.hard, vc.hard - vs.hard
        if (only_src and not only_cand and not vc.hard and _loose(src)) or \
                (only_cand and not only_src and not vs.hard and _loose(cand)):
            relabelled = True
            s -= 0.05
    if src.album and cand.album and norm_title(src.album) == norm_title(cand.album):
        s += 0.03          # same album (editions like "Deluxe" count as the same)
        if _base(src.album) == _base(cand.album):
            s += 0.01      # … the identical edition a little more
    # clearly different title/artist, or a different recording, never auto-matches
    if t < 0.7 or a < 0.55:
        s = min(s, ACCEPT - 0.01)
    if cmp.hard_mismatch and not relabelled:
        s = min(s, HARD_CAP)
    # a duration far off means another edit even if nobody labelled it
    # "exact" = the same song in the same version – never for another title or artist
    exact = cmp.exact and (d >= 0.8 or video) and t >= 0.85 and a >= 0.7
    notes = list(cmp.notes)
    if relabelled:
        label = " / ".join(sorted(vs.hard ^ vc.hard))      # the note versions.compare wrote for it
        notes = [f"only in the video title: {n}" if n == label else n for n in notes]
    if cmp.exact and d < 0.8 and not video and src.duration and cand.duration:
        notes.append(f"{abs(src.duration - cand.duration)} s difference")
    return Assessment(round(max(0.0, min(1.0, s)), 4), s, exact, notes, cmp.hard_mismatch and not relabelled)


RENAMED = "different artist name"


def same_but_artist(src: Track, cand: Track) -> bool:
    """Identical title, same length (±2 s), same version – only the artist reads differently (an artist
    who renamed, a YouTube channel name). Whether that is enough depends on the other results."""
    if not (src.duration and cand.duration and abs(src.duration - cand.duration) <= 2):
        return False
    for s in readings(src):
        for c in readings(cand):
            if not norm_title(s.title) or _base(norm_title(s.title)) != _base(norm_title(c.title)):
                continue
            cmp = versions.compare(version(s), version(c))
            if cmp.exact and not cmp.hard_mismatch:
                return True
    return False


def score(src: Track, cand: Track) -> float:
    return assess(src, cand).score


def rank(src: Track, candidates: list[Track]) -> list[tuple[Track, float]]:
    """Best first; among (nearly) equal scores the exact version wins."""
    scored = [(c, assess(src, c)) for c in candidates if c.id]
    scored.sort(key=lambda x: x[1].rank_key, reverse=True)
    return [(c, a.score) for c, a in scored]


def pair_lists(left: list[Track], right: list[Track], require_exact: bool = False) -> list[tuple[Track, Track, float]]:
    """Greedy one-to-one pairing of two song lists (e.g. both libraries before the first sync).
    With require_exact only identical versions are paired."""
    if not left or not right:
        return []
    right_titles = [norm_title(t.title) for t in right]
    by_isrc = {t.isrc.upper(): i for i, t in enumerate(right) if t.isrc}
    cands: list[tuple[float, int, int]] = []
    for li, lt in enumerate(left):
        idx: set[int] = set()
        if lt.isrc and lt.isrc.upper() in by_isrc:
            idx.add(by_isrc[lt.isrc.upper()])
        for _, _, ri in process.extract(norm_title(lt.title), right_titles, scorer=fuzz.ratio,
                                        score_cutoff=65, limit=8):
            idx.add(ri)
        for ri in idx:
            a = assess(lt, right[ri])
            if a.score >= ACCEPT and (a.exact or not require_exact):
                cands.append((a.rank_key, li, ri))
    cands.sort(reverse=True)
    used_l: set[int] = set()
    used_r: set[int] = set()
    out = []
    for s, li, ri in cands:
        if li in used_l or ri in used_r:
            continue
        used_l.add(li)
        used_r.add(ri)
        out.append((left[li], right[ri], s))
    return out
