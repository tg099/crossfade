from crossfade import matcher
from crossfade.models import APPLE, YTM, Track


def test_real_world_titles():
    y = Track(YTM, "1", "Get Lucky (Radio Edit - feat. Pharrell Williams and Nile Rodgers)",
              "Daft Punk, Pharrell Williams, Nile Rodgers", duration=249)
    good = Track(APPLE, "2", "Get Lucky (feat. Pharrell Williams) [Radio Edit]", "Daft Punk", duration=248)
    remix = Track(APPLE, "4", "Get Lucky (Daft Punk Remix)", "Daft Punk", duration=632)
    other = Track(APPLE, "5", "GET LUCKY!", "YdBB", duration=221)
    assert matcher.score(y, good) >= matcher.ACCEPT
    assert matcher.score(y, remix) < matcher.ACCEPT and matcher.score(y, other) < matcher.ACCEPT


def test_isrc_wins_and_umlauts():
    a = Track(APPLE, "1", "Songs für Liam", "Kraftklub", isrc="DEUM71104128")
    b = Track("spotify", "2", "Songs fur Liam", "KRAFTKLUB", isrc="deum71104128")
    assert matcher.score(a, b) == 1.0
    c = Track(YTM, "3", "Songs für Liam", "Kraftklub - Topic", duration=194)
    assert matcher.score(Track(APPLE, "1", "Songs für Liam", "Kraftklub", duration=194), c) >= matcher.ACCEPT


# ---------------------------------------------------------------- cases that once went unmatched (YTM -> Apple)
UGC, OMV = "MUSIC_VIDEO_TYPE_UGC", "MUSIC_VIDEO_TYPE_OMV"


def _t(title, artist, d, vt=None, svc="apple", album=""):
    return Track(service=svc, id=f"{title}|{artist}|{d}", title=title, artist=artist, album=album, duration=d,
                 video_type=vt)


def test_remix_is_found_under_the_remixer():
    src = _t("EAST COAST BABY (MRJay Remix)", "belis", 209, OMV, "ytm")
    a = matcher.assess(src, _t("East Coast Baby", "MRJay", 211))
    assert a.exact and a.score >= matcher.ACCEPT
    # the original by belis is not that remix
    assert matcher.assess(src, _t("EAST COAST BABY", "Belis", 105)).score < matcher.ACCEPT
    src = _t("Other Position (DarK Boy Remix)", "2Pac & Miyagi", 363, UGC, "ytm")
    assert matcher.assess(src, _t("Other Position", "Dark Boy", 363)).score >= matcher.ACCEPT


def test_two_named_remixes_differ():
    src = _t("East Coast Baby (MRJay Remix)", "belis", 209, OMV, "ytm")
    assert matcher.assess(src, _t("East Coast Baby (Other Guy Remix)", "Belis", 209)).score <= matcher.HARD_CAP


def test_uploader_label_with_identical_length_and_subtitle():
    src = _t("Gimme Gimme Gimme (Club Mix)", "Syzz", 169, UGC, "ytm")
    plain = _t("Gimme! Gimme! Gimme! (A man after midnight)", "Syzz", 169)
    cands = [plain,
             _t("Gimme Gimme Gimme (A man after midnight) [The Rocketman Remix]", "Syzz", 214),
             _t("Gimme Gimme Gimme (A Man After Midnight) [Mixed]", "Syzz", 123, album="BASS WAVES (DJ Mix)"),
             _t("Gimme Gimme Gimme (a man after midnight)", "Syzz", 153)]
    best, score = matcher.rank(src, cands)[0]
    assert best is plain and score >= matcher.ACCEPT
    assert not matcher.assess(src, plain).exact          # reported as "only in the video title"


def test_relabel_only_for_user_uploads():
    # a streaming service's own "Live" label is trusted even at the same length
    assert matcher.assess(_t("Song (Live)", "X", 200, svc="spotify"), _t("Song", "X", 200)).score <= matcher.HARD_CAP


def test_remixers():
    assert matcher.remixers("Song (MRJay Remix)") == ["MRJay"]
    assert matcher.remixers("Song [Tiësto's VIP Mix]") == ["Tiësto"]
    assert matcher.remixers("Song (Radio Edit)") == [] and matcher.remixers("Song (Club Mix)") == []


OSM = "MUSIC_VIDEO_TYPE_OFFICIAL_SOURCE_MUSIC"


def test_official_source_video_length_is_not_a_version():
    # the artist's own channel: a video with an intro, artists in another order
    vid = _t("Barbie Boy", "Ennoia, TiDo, DJ DRECKISCH", 175, OSM, "ytm")
    song = _t("Barbie Boy", "DJ DRECKISCH, Ennoia, TiDo", 160)
    a = matcher.assess(vid, song)
    assert a.exact and a.score >= matcher.ACCEPT
    # … but its title is not torn apart like a fan upload's
    assert matcher.tidy_video("Song - Remix", "X", OSM) == ("Song - Remix", "X")


def test_pair_renamed_channel_upload():
    songs = [_t("B4LLER", "beastboy", 186), _t("TAKE THE LEAD", "beastboy", 170), _t("Hello", "Adele", 295)]
    vids = [_t("B4LLER", "TJ_babybrain", 187, OMV, "ytm"), _t("TAKE THE LEAD", "TJ_babybrain", 173, OMV, "ytm"),
            _t("Hello", "Some Channel", 296, OMV, "ytm"), _t("Hello", "Lionel Richie", 296, OMV, "ytm")]
    pairs = {(a.title, b.artist) for a, b in matcher.pair_renamed(songs, vids)}
    assert pairs == {("B4LLER", "TJ_babybrain"), ("TAKE THE LEAD", "TJ_babybrain")}   # "Hello" is ambiguous
    # two streaming services' own data: a different artist is a different song
    assert matcher.pair_renamed([_t("Oxygen", "OSVISS", 202)], [_t("oxygen", "yerbby", 203, svc="spotify")]) == []
    # another length is another song
    assert matcher.pair_renamed([_t("Chaos", "A", 125)], [_t("Chaos", "B", 141, OMV, "ytm")]) == []
