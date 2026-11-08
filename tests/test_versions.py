"""Version-aware matching with real-world title conventions of the three services."""
from crossfade import matcher
from crossfade.models import APPLE, SPOTIFY, YTM, Track
from crossfade.versions import version_of


def T(title, artist="X", album="", dur=200, explicit=None, svc=APPLE, isrc=None, album_type=None):
    return Track(svc, title + album + str(dur), title, artist, album, dur, isrc, explicit=explicit,
                 album_type=album_type)


def test_detects_markers():
    assert version_of("Hey Jude - Live").hard == {"Live"}
    assert version_of("Smells Like Teen Spirit", album="MTV Unplugged in New York").hard == {"Live", "Acoustic"}
    assert version_of("Violet", album="Live Through This").hard == set()          # studio album
    assert version_of("Shake It Off (Taylor's Version)").hard == {"Re-recording"}
    assert version_of("Lose Yourself (Originally Performed By Eminem) [Instrumental Version]").hard == {
        "Karaoke", "Instrumental"}
    v = version_of("Bohemian Rhapsody - Remastered 2011")
    assert v.soft == {"Remaster"} and v.remaster_year == "2011"
    assert version_of("Blinding Lights (Radio Edit)").soft == {"Radio Edit"}
    assert version_of("Live Forever").hard == set()                               # word in the real title
    assert version_of("Song", album="Greatest Hits").compilation
    assert version_of("Song (Clean)").explicit is False
    assert version_of("Lose Yourself (Reggae)").hard == {"Other arrangement"}
    assert version_of("Despacito (Spanish Version)").hard == {"Other arrangement"}
    assert version_of("Song (Single Version)").hard == set()
    assert version_of("Song (Radio Version)").soft == {"Radio Edit"}


def test_different_recordings_never_auto_match():
    src = T("Lose Yourself", "Eminem", dur=326, explicit=True)
    for cand in (T("Lose Yourself (Originally Performed By Eminem) [Instrumental Version]", "Karaoke Band", dur=326),
                 T("Lose Yourself - Live", "Eminem", dur=330),
                 T("Lose Yourself (Reggae)", "Eminem", dur=326),
                 T("Lose Yourself (Sped Up)", "Eminem", dur=250)):
        a = matcher.assess(src, cand)
        assert a.score < matcher.ACCEPT and not a.exact, cand.title
    taylor = matcher.assess(T("Shake It Off", "Taylor Swift", dur=219), T("Shake It Off (Taylor's Version)", "Taylor Swift", dur=219))
    assert taylor.score < matcher.ACCEPT and taylor.hard_mismatch


def test_remaster_is_close_but_not_exact():
    src = T("Bohemian Rhapsody", "Queen", dur=355)
    a = matcher.assess(src, T("Bohemian Rhapsody (Remastered 2011)", "Queen", dur=355))
    assert a.score >= matcher.ACCEPT and not a.exact and "Remaster" in a.notes
    same = matcher.assess(T("Bohemian Rhapsody - Remastered 2011", "Queen", dur=355, svc=SPOTIFY),
                          T("Bohemian Rhapsody (Remastered 2011)", "Queen", dur=355))
    assert same.exact


def test_radio_edit_vs_album_version_by_duration():
    src = T("Get Lucky", "Daft Punk", dur=369)
    a = matcher.assess(src, T("Get Lucky (Radio Edit)", "Daft Punk", dur=248))
    assert a.score < matcher.ACCEPT and not a.exact


def test_prefers_exact_version_explicit_and_original_album():
    src = T("Lose Yourself", "Eminem", album="8 Mile", dur=326, svc=YTM)        # YTM: explicit unknown
    clean = T("Lose Yourself", "Eminem", album="8 Mile", dur=326, explicit=False)
    explicit = T("Lose Yourself", "Eminem", album="8 Mile", dur=326, explicit=True)
    best = matcher.rank(src, [clean, explicit])[0][0]
    assert best is explicit                                                    # explicit by default
    orig = T("Lose Yourself", "Eminem", album="8 Mile", dur=326, explicit=True)
    comp = T("Lose Yourself", "Eminem", album="Curtain Call: The Hits", dur=326, explicit=True)
    hits = T("Lose Yourself", "Eminem", album="Greatest Hits", dur=326, explicit=True)
    assert matcher.rank(src, [hits, comp, orig])[0][0] is orig
    clean_src = T("Lose Yourself (Clean)", "Eminem", album="8 Mile", dur=326)
    assert matcher.rank(clean_src, [explicit, clean])[0][0] is clean           # the source decides


def test_bracketed_real_titles_survive():
    a = matcher.assess(T("(I Can't Get No) Satisfaction", "The Rolling Stones", dur=223),
                       T("(I Can't Get No) Satisfaction - Mono Version", "The Rolling Stones", dur=223))
    assert a.score >= matcher.ACCEPT and "Mono" in a.notes
    assert matcher.norm_title("Levitating (feat. DaBaby)") == matcher.norm_title("Levitating")


def test_same_isrc_is_always_exact():
    a = matcher.assess(T("Song", isrc="DEUM71104128"), T("Song - Single Version", isrc="deum71104128", dur=150))
    assert a.score == 1.0 and a.exact


def test_named_mixes_are_other_recordings():
    assert "Remix" in version_of("Smells Like Teen Spirit (Devonshire Mix)").hard
    assert "DJ-Mix" in version_of("Lose Yourself (Mixed)").hard   # a cut from a DJ set, not a remix
    assert version_of("Song (Radio Mix)").hard == set()
    assert version_of("Song (Stereo Mix)").hard == set()


def test_unknown_explicitness_is_not_a_different_version():
    a = matcher.assess(T("Shake It Off", "Taylor Swift", dur=219, svc=YTM), T("Shake It Off", "Taylor Swift", dur=219, explicit=False))
    assert a.exact
    b = matcher.assess(T("Lose Yourself", "Eminem", dur=326, explicit=True), T("Lose Yourself", "Eminem", dur=326, explicit=False))
    assert not b.exact and "Clean instead of Explicit" in b.notes
