"""Odd versions: what a person sees picked or preselected must be the song, not something like it."""
from __future__ import annotations

import time

from crossfade import matcher, versions
from crossfade.db import Database
from crossfade.identity import Identity
from crossfade.models import APPLE, SPOTIFY, YTM, Track

UGC, OMV, ATV = "MUSIC_VIDEO_TYPE_UGC", "MUSIC_VIDEO_TYPE_OMV", "MUSIC_VIDEO_TYPE_ATV"


def test_credits_after_a_pipe_go_but_a_series_title_stays_whole():
    assert versions.strip_noise("Big Dawgs | Prod. Kalmi") == "Big Dawgs"
    assert versions.strip_noise("Lieblingslied | KitschKrieg") == "Lieblingslied"
    assert versions.strip_noise("Song | Prod. X | Channel") == "Song"
    whole = versions.strip_noise("Letztes JBB | 8tel-Finale #2 | Tamarillo vs Gary Washington")
    assert "Tamarillo" in whole and whole.startswith("Letztes JBB")


def test_a_battle_upload_is_no_match_for_another_upload_of_the_series():
    """Seen live: "Letztes JBB Appetizer" was preselected for a battle video of the same series."""
    battle = Track(YTM, "v1", "Letztes JBB | 8tel-Finale #2 | Tamarillo vs Gary Washington", "JuliensBlogBattle",
                   duration=348, video_type=UGC)
    appetizer = Track(APPLE, "a1", "Letztes JBB Appetizer", "Tamarillo", album="Letztes JBB Appetizer - Single",
                      duration=148)
    a = matcher.assess(battle, appetizer)
    assert a.score < matcher.SUGGEST and not a.fits


def test_only_a_convincing_suggestion_is_preselected():
    src = Track(SPOTIFY, "s1", "Hello", "Adele", album="25", duration=295)
    same = Track(APPLE, "a1", "Hello", "Adele", album="25", duration=295)
    live = Track(APPLE, "a2", "Hello (Live at the Church Studios)", "Adele", album="Live", duration=310)
    other = Track(APPLE, "a3", "Hello Hello", "Adele", album="Something", duration=295)
    assert matcher.assess(src, same).fits
    assert not matcher.assess(src, live).fits                    # another recording: choosable, not preselected
    assert not matcher.assess(src, other).fits                   # same artist and length, another title


def test_same_title_far_off_in_length_on_another_album_waits_for_a_person():
    src = Track(SPOTIFY, "s1", "Intro", "Casper", album="XOXO", duration=95)
    other = Track(APPLE, "a1", "Intro", "Casper", album="Hinterland", duration=160)
    assert matcher.assess(src, other).score < matcher.ACCEPT
    # the same album in another edit stays the closest version
    edit = Track(APPLE, "a2", "Intro", "Casper", album="XOXO (Deluxe)", duration=160)
    assert matcher.assess(src, edit).score >= matcher.ACCEPT


def test_known_fan_upload_is_not_used_as_the_songs_video(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    ident = Identity(db)
    audio = Track(YTM, "atv1", "Song", "Artist", duration=200, video_type=ATV)
    sid = ident.song_for(audio)
    fan = Track(YTM, "ugc1", "Song (Lyrics)", "Some Channel", duration=201, video_type=UGC)
    ident.link(sid, fan)
    official = Track(YTM, "omv1", "Song (Official Video)", "Artist", duration=230, video_type=OMV)

    class Provider:
        def counterpart(self, video_id):
            return official if video_id == "atv1" else None

    track, _ = ident.in_format(sid, audio, Provider(), "video")
    assert track.id == "omv1"
    # no official video at all: the song itself, not the fan upload
    audio2 = Track(YTM, "atv2", "Other Song", "Artist", duration=180, video_type=ATV)
    sid2 = ident.song_for(audio2)
    ident.link(sid2, Track(YTM, "ugc2", "Other Song (sped up)", "Fan", duration=150, video_type=UGC))
    db.run("INSERT INTO yt_counterparts(video_id,counterpart,checked_at) VALUES('atv2','',?)", (time.time(),))
    track, _ = ident.in_format(sid2, audio2, Provider(), "video")
    assert track.id == "atv2"


def test_odd_uploads_and_other_tracks_of_the_name_never_match_on_their_own():
    """Seen as automatic matches before: an unrecognised label read as "the same version"."""
    src = Track(SPOTIFY, "s1", "Daylight", "David Kushner", album="Daylight", duration=212)
    odd = {
        "Daylight (Spedup)": 160, "Daylight (Bass Boosted)": 212, "Daylight (8D Audio)": 212,
        "Daylight (Hardstyle)": 160, "Daylight (Version française)": 212, "Daylight (Pt. 2)": 212,
        "Daylight (Intro)": 60, "Daylight (Skit)": 50, "Daylight (Reprise)": 120, "Daylight (Mashup)": 212,
        "Daylight (VIP)": 212, "Daylight (Re-Imagined)": 212, "Daylight (Spotify Sessions)": 212,
        "Daylight (Commentary)": 30, "Daylight (Medley)": 300, "Daylight (Official Live Video)": 230,
    }
    for title, secs in odd.items():
        a = matcher.assess(src, Track(APPLE, "a", title, "David Kushner", album="Daylight - Single", duration=secs))
        assert a.score < matcher.ACCEPT and not a.exact and not a.fits, title
    # the plain song, a feature or the album version still match at once
    for title in ("Daylight", "Daylight (feat. Sofia Carson)"):
        assert matcher.assess(src, Track(APPLE, "a", title, "David Kushner", album="Daylight - Single",
                                         duration=212)).exact, title


def test_a_version_named_in_an_uploads_label_is_kept():
    assert versions.strip_noise("Song (Official Live Video)") == "Song (Live)"
    assert versions.strip_noise("Song - Official Live Video") == "Song (Live)"
    assert versions.strip_noise("Song | Live at Wembley") == "Song (Live at Wembley)"
    assert versions.strip_noise("Song (Official Video)") == "Song"
    assert versions.strip_noise("Big Dawgs | Prod. Kalmi") == "Big Dawgs"
    assert versions.version_of("Song (8D Audio)").hard == {"Fan edit"}


def test_part_numbers_are_other_songs_wherever_they_stand():
    assert versions.version_of("Bad Habits, Pt. 2").hard == {"Part 2"}
    assert versions.version_of("Song (Part II)").hard == versions.version_of("Song Pt. 2").hard
    assert versions.version_of("Part Time Lover").hard == set()
    one = Track(SPOTIFY, "s1", "Bad Habits, Pt. 2", "Ed Sheeran", duration=200)
    assert matcher.assess(one, Track(APPLE, "a1", "Bad Habits (Pt. 2)", "Ed Sheeran", duration=200)).exact
    assert matcher.assess(one, Track(APPLE, "a2", "Bad Habits", "Ed Sheeran", duration=200)).hard_mismatch


def test_plain_edition_names_are_no_other_arrangement():
    for title in ("Song (Super Deluxe Version)", "Song (Studio Version)", "Song (Edited Version)"):
        assert versions.version_of(title).hard == set(), title


def test_another_artists_song_of_that_name_is_never_preselected():
    src = Track(SPOTIFY, "s1", "Daylight", "David Kushner", album="Daylight", duration=212)
    cover = Track(APPLE, "a1", "Daylight", "Some Singer", album="Covers Vol. 1", duration=215)
    a = matcher.assess(src, cover)
    assert a.score >= matcher.SUGGEST and not a.fits


def test_same_recording_on_several_releases_takes_the_sources_release():
    """One ISRC on a single, the album and a compilation: each has its own cover."""
    src = Track(SPOTIFY, "s1", "immerjung", "Sampagne", album="immerjung", duration=128, isrc="DEUM72500001")
    single = Track(APPLE, "a1", "immerjung", "Sampagne", album="immerjung - Single", duration=128, isrc="DEUM72500001")
    album = Track(APPLE, "a2", "immerjung", "Sampagne", album="immerjung", duration=128, isrc="DEUM72500001")
    hits = Track(APPLE, "a3", "immerjung", "Sampagne", album="Deutschrap Hits 2025", duration=128,
                 isrc="DEUM72500001", album_type="compilation")
    assert [t.id for t, _ in matcher.rank(src, [hits, single, album])] == ["a2", "a1", "a3"]
