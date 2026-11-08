"""Recognise *which version* of a song a track is (live, remix, remaster 2011, radio edit, explicit …).

Hard markers change the recording itself – a live take is never the studio song – so a mismatch
there never auto-matches. Soft markers are variants of the same recording or edit; a mismatch is
allowed when the line accepts the closest version, and is reported.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# label (shown in the UI) -> regex on normalised "title + album" text
HARD: dict[str, str] = {
    "Live": r"\blive\b|\bunplugged\b|\bconcert\b|\blive (at|in|from|aus)\b",
    "Acoustic": r"\bacoustic\b|\bakustik\b|\bstripped\b",
    # any named mix is another mix of the recording – except the plain radio/album/stereo/mono mix
    "Remix": r"\bremix\b|\brmx\b|\brework\b|\bflip\b"
             r"|\b(?!radio |album |single |stereo |mono |original |main |clean |explicit |dj |continuous )\w{3,} mix\b",
    # a cut out of a DJ set ("[Mixed]") – blended into the neighbouring tracks, not a remix
    "DJ-Mix": r"\bmixed\b|\bdj mix\b|\bcontinuous mix\b|\bmix cut\b",
    "Instrumental": r"\binstrumental\b",
    "Karaoke": r"\bkaraoke\b|\boriginally performed by\b|\bin the style of\b|\bmade famous by\b|\btribute\b",
    "A cappella": r"\ba ?cappella\b|\bacapella\b",
    "Sped up / Slowed": r"\bsped up\b|\bspeed up\b|\bslowed\b|\breverb\b|\bnightcore\b|\b8d\b",
    "Demo": r"\bdemo\b|\bearly version\b|\bwork ?tape\b",
    "Re-recording": r"\btaylor s version\b|\bre ?recorded\b|\bre ?record\b|\bnew version\b|\b20\d\d version\b",
    "Orchestral": r"\borchestral\b|\bsymphonic\b|\bwith .* orchestra\b|\bpiano version\b|\blullaby\b",
    "Cover": r"\bcover\b",
    # genre / language re-arrangements: "(Reggae)", "(Piano Version)", "(Spanish Version)" …
    "Other arrangement": r"\b(reggae|jazz|lo ?fi|bossa( nova)?|swing|chill|trap|techno|house|edm|dance|rock|metal|punk|"
                      r"country|gospel|christmas|xmas|spanish|espanol|english|deutsche?|german|french|francais|"
                      r"italian|italiano|portuguese|japanese|korean|latin|kids|baby)\b( version| mix| edit)?"
                      r"|\b(?!radio |album |single |lp |long |short |extended |original |explicit |clean |mono |stereo "
                      r"|edit |remastered |remaster |taylor s |new |demo |live |acoustic |instrumental |karaoke |orchestral "
                      r"|piano |early |full )\w{3,} version\b",
}
SOFT: dict[str, str] = {
    "Radio Edit": r"\bradio edit\b|\bradio version\b|\bradio mix\b|\bsingle edit\b|\bedit\b",
    "Extended": r"\bextended\b|\b12 ?inch\b|\bfull length\b|\blong version\b",
    "Remaster": r"\bremaster(ed)?\b|\bremastering\b",
    "Mono": r"\bmono\b",
    "Album version": r"\balbum version\b|\blp version\b",
    "Deluxe": r"\bdeluxe\b|\bexpanded\b|\banniversary\b|\bbonus track\b",
    "Clean": r"\bclean\b|\bcensored\b|\bradio friendly\b",
}
COMPILATION = (r"\bgreatest hits\b|\bbest of\b|\bthe very best\b|\bhits 20\d\d\b|\bbravo hits\b|\bnow that s what\b"
               r"|\bthe collection\b|\bessentials\b|\banthology\b|\bthe hits\b|\bkuschelrock\b|\bchartboxx\b|\b\d+ hits\b"
               r"|\bnumber ones\b|\bplatinum collection\b|\bgold\b(?! digger)")

_HARD_RX = {k: re.compile(v) for k, v in HARD.items()}
# in album names only unambiguous phrases count ("Live Through This" is a studio album)
_ALBUM_HARD = {
    "Live": re.compile(r"\blive (at|in|from|aus|im|on)\b|\bunplugged\b|\bin concert\b|\blive album\b|^live$"),
    "Acoustic": re.compile(r"\bacoustic (sessions?|version|album)\b|\bunplugged\b"),
    "Karaoke": re.compile(r"\bkaraoke\b|\btribute to\b|\boriginally performed\b"),
    "Instrumental": re.compile(r"\binstrumentals?\b"),
}
_SOFT_RX = {k: re.compile(v) for k, v in SOFT.items()}
_COMP_RX = re.compile(COMPILATION)
_YEAR_RX = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")


# Words that only describe the *upload* (YouTube videos), never the recording: "(Official Video)",
# "[Visualizer]", "(Lyrics)", "(prod. by X)", "| Prod. Kalmi", "#phonk" … They are removed before
# titles are compared, so "Noch da (Official Version)" is the plain song, not "another version".
_NOISE = re.compile(
    r"\b(official|offiziell(es)?|officiel|video|videoclip|clip|audio|visuali[sz]er|lyrics?|lyric|songtext|"
    r"mv|m/v|hd|hq|4k|uhd|musikvideo|premiere|prod|produced|beat by|beats by|shot by|dir|directed|"
    r"phonk|nightdrive|wave/|out now|teaser|trailer|mood video|full song)\b", re.I)


def strip_noise(title: str) -> str:
    t = title or ""
    t = re.sub(r"[(\[【]([^)\]】]*)[)\]】]", lambda m: " " if _NOISE.search(m.group(1)) else m.group(0), t)
    t = re.sub(r"\s[|｜]\s.*$", " ", t)                       # "Title | Prod. X | Channel"
    t = re.sub(r"\s[-–~]\s*(official|offizielles?)\b.*$", " ", t, flags=re.I)  # "Title - Official Video"
    t = re.sub(r"#\w+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = s.replace("’", "'").replace("'", " ")
    return re.sub(r"[^\w]+", " ", s).strip()


def _brackets(title: str) -> str:
    """Only the qualifying parts of a title: (...), [...] and ' - ...' suffixes."""
    parts = re.findall(r"[(\[]([^)\]]*)[)\]]", title or "")
    m = re.search(r"\s[-–]\s(.+)$", title or "")
    if m:
        parts.append(m.group(1))
    return " ".join(parts)


@dataclass
class Version:
    hard: set[str] = field(default_factory=set)
    soft: set[str] = field(default_factory=set)
    remaster_year: str | None = None
    explicit: bool | None = None
    compilation: bool = False

    def label(self) -> str:
        tags = sorted(self.hard) + sorted(t if t != "Remaster" or not self.remaster_year
                                          else f"Remaster {self.remaster_year}" for t in self.soft)
        return ", ".join(tags) or "Original"


def version_of(title: str, album: str = "", explicit: bool | None = None,
               album_type: str | None = None) -> Version:
    title = strip_noise(title)
    q = _norm(_brackets(title))
    whole = _norm(title)
    alb = _norm(album)
    v = Version(explicit=explicit)
    for k, rx in _HARD_RX.items():
        # hard markers may also appear in the album name (e.g. "MTV Unplugged", "Live in Berlin")
        if rx.search(q) or (k in _ALBUM_HARD and (_ALBUM_HARD[k].search(alb) or rx.search(_norm(_brackets(album))))):
            v.hard.add(k)
    # covers/karaoke often put it into the main title ("Lose Yourself (By Eminem)")
    if re.search(r"\boriginally performed by\b|\bmade famous by\b|\bin the style of\b", whole):
        v.hard.add("Karaoke")
    for k, rx in _SOFT_RX.items():
        if rx.search(q):
            v.soft.add(k)
    if "Remaster" in v.soft:
        y = _YEAR_RX.search(q)
        v.remaster_year = y.group(1) if y else None
    if "Clean" in v.soft and v.explicit is None:
        v.explicit = False
    v.soft.discard("Clean")
    v.compilation = album_type == "compilation" or bool(alb and _COMP_RX.search(alb))
    return v


@dataclass
class Comparison:
    """How well a candidate's version fits the source's version."""
    penalty: float                    # subtracted from the similarity score
    hard_mismatch: bool
    exact: bool                       # same version as far as we can tell
    notes: list[str] = field(default_factory=list)   # human readable differences


EXPLICIT_DEFAULT = True   # if the source doesn't say: prefer the explicit (original) version (tie-break only)


def compare(src: Version, cand: Version) -> Comparison:
    notes: list[str] = []
    penalty = 0.0
    hard_diff = src.hard ^ cand.hard
    if hard_diff:
        notes.append(" / ".join(sorted(hard_diff)))
    soft_diff = src.soft ^ cand.soft
    for t in sorted(soft_diff):
        penalty += 0.04
        notes.append(t if t in cand.soft else f"without {t}")
    if "Remaster" in src.soft and "Remaster" in cand.soft and src.remaster_year and cand.remaster_year \
            and src.remaster_year != cand.remaster_year:
        penalty += 0.02
        notes.append(f"Remaster {cand.remaster_year} instead of {src.remaster_year}")
    explicit_diff = src.explicit is not None and cand.explicit is not None and cand.explicit != src.explicit
    if explicit_diff:
        penalty += 0.06
        notes.append("Clean instead of Explicit" if src.explicit else "Explicit instead of Clean")
    elif src.explicit is None and cand.explicit is not None and cand.explicit != EXPLICIT_DEFAULT:
        penalty += 0.01      # unknown source: the explicit version wins a tie
    if cand.compilation and not src.compilation:
        penalty += 0.03
        notes.append("Compilation")
    exact = not hard_diff and not soft_diff and not explicit_diff and not (cand.compilation and not src.compilation)
    return Comparison(penalty=penalty, hard_mismatch=bool(hard_diff), exact=exact, notes=notes)
