"""Searching one service with what another service knows about the song (ISRC, clean title)."""
from __future__ import annotations

from test_engine import P, env, make_line, sync, track  # noqa: F401 - fixtures

from crossfade.identity import Identity
from crossfade.models import APPLE, LIKED, SPOTIFY, YTM, Track


class TextBlind:
    """Apple Music that finds nothing by text, only by ISRC."""
    def search(self, q, limit=10):
        return []


def _blind(provider):
    provider.search = TextBlind().search


def _ytm_haus() -> Track:
    # a YouTube upload: no ISRC, so Apple can only be found with Spotify's details
    return Track(YTM, "ytm_haus", "Haus am See", "Peter Fox", duration=216)


def test_song_found_on_spotify_helps_finding_it_on_apple(env):
    y, a, s = P(env, YTM), P(env, APPLE), P(env, SPOTIFY)
    _blind(a)
    y.cols[(LIKED, "")] = {"ytm_haus": _ytm_haus()}
    # Apple is asked first: its miss waits for Spotify's result and is searched again with it
    lid = make_line(env, [(YTM, LIKED, "", "send"), (APPLE, LIKED, "", "receive"), (SPOTIFY, LIKED, "", "receive")])
    r = sync(env, lid)
    assert r.status == "ok", r.message
    assert s.keys() == ["haus"] and a.keys() == ["haus"]
    assert r.summary["unmatched"] == 0


def test_without_a_helper_the_miss_stays_an_issue(env):
    y, a = P(env, YTM), P(env, APPLE)
    _blind(a)
    y.cols[(LIKED, "")] = {"ytm_haus": _ytm_haus()}
    lid = make_line(env, [(YTM, LIKED, "", "send"), (APPLE, LIKED, "", "receive")])
    r = sync(env, lid)
    assert r.summary["unmatched"] == 1 and a.keys() == []


def test_cached_miss_is_searched_again_once_another_service_knows_the_song(env):
    db = env[0]
    a = P(env, APPLE)
    _blind(a)
    ident = Identity(db)
    sid = ident.song_for(_ytm_haus())
    assert ident.find(sid, APPLE, a).track is None            # remembered as not found
    ident.link(sid, track(SPOTIFY, "haus"))                   # later Spotify finds it
    m = ident.find(sid, APPLE, a)
    assert m.track is not None and m.track.id == "apple_haus" and m.exact


def test_other_version_on_another_service_is_no_helper(env):
    db = env[0]
    a = P(env, APPLE)
    _blind(a)
    ident = Identity(db)
    sid = ident.song_for(_ytm_haus())
    live = Track(SPOTIFY, "sp_live", "Haus am See - Live", "Peter Fox", duration=240, isrc="DEA620800624")
    ident.link(sid, live)
    assert ident.helpers(sid, APPLE, ident.describe(sid)) == []


# ---------------------------------------------------------------- renamed artist
class Results:
    def __init__(self, *tracks):
        self.tracks = list(tracks)

    def search(self, q, limit=10):
        return self.tracks

    def by_isrc(self, isrc):
        return []


def _video() -> Track:
    return Track(YTM, "xk", "B4LLER", "TJ_babybrain", duration=187, video_type="MUSIC_VIDEO_TYPE_OMV")


def _apple(i, title, artist, secs) -> Track:
    return Track(APPLE, f"a{i}", title, artist, album=f"{title} - Single", duration=secs, isrc=f"X{i}")


def test_renamed_artist_is_accepted_when_the_title_is_unique(env):
    ident = Identity(env[0])
    sid = ident.song_for(_video())
    prov = Results(_apple(1, "B4LLER", "beastboy", 186), _apple(2, "B4ller$", "Trippy DFS", 175),
                   _apple(3, "B4LLER", "beastboy", 186))       # single and album: still one artist
    m = ident.find(sid, APPLE, prov)
    assert m.track.artist == "beastboy" and not m.exact
    assert m.accepted("closest") and not m.accepted("exact")
    assert m.note() == "different artist name"


def test_renamed_artist_rule_needs_a_unique_title(env):
    ident = Identity(env[0])
    sid = ident.song_for(_video())
    prov = Results(_apple(1, "B4LLER", "beastboy", 186), _apple(2, "B4LLER", "Someone Else", 190))
    assert not ident.find(sid, APPLE, prov).accepted("closest")


def test_renamed_artist_rule_needs_the_same_length(env):
    ident = Identity(env[0])
    sid = ident.song_for(_video())
    assert not ident.find(sid, APPLE, Results(_apple(1, "B4LLER", "beastboy", 192))).accepted("closest")


def test_renamed_artist_rule_needs_the_same_version(env):
    ident = Identity(env[0])
    sid = ident.song_for(_video())
    prov = Results(_apple(1, "B4LLER (Live)", "beastboy", 187))
    assert not ident.find(sid, APPLE, prov).accepted("closest")
