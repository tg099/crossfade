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
