"""Spec #116 — sync track core: frame parser, track builder, aligner.

The contract test at the bottom is the Phase 1 acceptance: build a track
from one copy, splice other audio into a second copy at two points, and
recover both offsets from the track alone.
"""

from __future__ import annotations

import pytest

from tests.unit.core.sync_track_fixtures import FRAME_SECONDS, chunks, frames, id3, seconds, silence, stream
from thestill.core.sync_track import SyncTrack, SyncTrackError, align, build_sync_track, iter_mp3_frames


def test_parser_yields_every_frame_across_chunk_edges():
    episode = frames(1, 500)
    parsed = list(iter_mp3_frames(chunks(stream(episode), size=1000)))
    assert [f.data for f in parsed] == episode
    assert parsed[0].start_s == 0.0
    assert parsed[-1].start_s == pytest.approx(499 * FRAME_SECONDS)


def test_parser_skips_id3_tags_at_start_and_between_segments():
    first, second = frames(1, 200), frames(2, 200)
    data = stream(id3(), first, id3(1000), second)
    assert [f.data for f in iter_mp3_frames(chunks(data))] == first + second


def test_parser_resyncs_after_junk():
    first, second = frames(1, 200), frames(2, 200)
    # Garbage between two segments, containing bytes that look like headers.
    junk = b"\x00\x01" + bytes([0xFF, 0xFB, 0x90, 0x00, 0x12, 0x34]) * 20
    parsed = [f.data for f in iter_mp3_frames(chunks(stream(first, junk, second)))]
    assert parsed == first + second


def test_build_puts_one_anchor_per_second():
    episode = frames(1, seconds(60))
    track = build_sync_track(chunks(stream(episode)), "gen1")
    assert track.frame_count == len(episode)
    assert track.audio_bytes == sum(len(f) for f in episode)
    assert track.duration_s == pytest.approx(len(episode) * FRAME_SECONDS)
    assert track.sample_rate == 44100
    assert 59 <= len(track.anchors) <= 60
    assert track.unique_ratio > 0.98


def test_build_drops_landmarks_in_repeated_audio():
    # 20 s of content, 10 s of digital silence, 20 s of content.
    data = stream(frames(1, seconds(20)), silence(seconds(10)), frames(2, seconds(20)))
    track = build_sync_track(chunks(data), "gen1")
    in_silence = [a for a in track.anchors if 21_000 <= a.time_ms < 29_000]
    assert in_silence == []
    assert track.anchors_dropped_duplicate >= 8
    assert len(track.anchors) >= 38


def test_build_rejects_non_mp3():
    with pytest.raises(SyncTrackError):
        build_sync_track(chunks(bytes(range(256)) * 400), "gen1")


def test_track_round_trips_through_bytes():
    track = build_sync_track(chunks(stream(frames(1, seconds(30)))), "a1b2c3")
    restored = SyncTrack.from_bytes(track.to_bytes())
    assert restored.generation == "a1b2c3"
    assert restored.anchors == track.anchors
    assert restored.frame_count == track.frame_count
    assert restored.audio_bytes == track.audio_bytes
    assert restored.duration_s == track.duration_s
    # About 10 bytes per second of audio.
    assert len(track.to_bytes()) < 30 * 10 + 64


def test_corrupt_track_is_rejected():
    blob = build_sync_track(chunks(stream(frames(1, seconds(10)))), "g").to_bytes()
    with pytest.raises(SyncTrackError):
        SyncTrack.from_bytes(b"nope" + blob[4:])
    with pytest.raises(SyncTrackError):
        SyncTrack.from_bytes(blob[: len(blob) // 2])


def test_identical_copy_aligns_at_zero():
    data = stream(frames(1, seconds(120)))
    track = build_sync_track(chunks(data), "g")
    result = align(track, chunks(data, size=5000))
    assert len(result.spans) == 1
    assert result.spans[0].offset_s == pytest.approx(0.0, abs=0.01)
    assert result.coverage > 0.95
    assert result.anchors_outlier == 0


def test_contract_recovers_two_splice_offsets():
    """Transcribed copy: A B C. Listener's copy: pre-roll A mid-roll B C."""
    a, b, c = frames(1, seconds(90)), frames(2, seconds(120)), frames(3, seconds(60))
    pre_roll, mid_roll = frames(10, seconds(15)), frames(11, seconds(30))
    track = build_sync_track(chunks(stream(a, b, c)), "g")

    result = align(track, chunks(stream(id3(), pre_roll, a, mid_roll, b, c)))

    assert len(result.spans) == 2
    first, second = result.spans
    assert first.offset_s == pytest.approx(len(pre_roll) * FRAME_SECONDS, abs=0.01)
    assert second.offset_s == pytest.approx((len(pre_roll) + len(mid_roll)) * FRAME_SECONDS, abs=0.01)
    # The break is located to within one landmark interval.
    boundary = len(a) * FRAME_SECONDS
    assert first.src_end_s == pytest.approx(boundary, abs=1.5)
    assert second.src_start_s == pytest.approx(boundary, abs=1.5)
    assert result.coverage > 0.95
    assert result.anchors_outlier == 0
    # A time in each part maps to where that audio really is in the other copy.
    assert result.to_other(30.0) == pytest.approx(30.0 + first.offset_s, abs=0.01)
    assert result.to_other(150.0) == pytest.approx(150.0 + second.offset_s, abs=0.01)


def test_ad_only_in_transcribed_copy_leaves_a_gap_and_negative_offset():
    a, ad, b = frames(1, seconds(60)), frames(9, seconds(30)), frames(2, seconds(60))
    track = build_sync_track(chunks(stream(a, ad, b)), "g")

    result = align(track, chunks(stream(a, b)))

    assert [round(s.offset_s, 2) for s in result.spans] == [0.0, round(-len(ad) * FRAME_SECONDS, 2)]
    assert result.to_other(75.0) is None  # inside the ad that the other copy lacks
    assert 0.6 < result.coverage < 0.85


def test_audio_repeated_in_other_copy_does_not_break_order():
    """A promo from the end of the transcribed copy is also played early in
    the other copy. The early occurrence must not become a backwards span."""
    a, b, promo = frames(1, seconds(60)), frames(2, seconds(60)), frames(3, seconds(20))
    track = build_sync_track(chunks(stream(a, b, promo)), "g")

    result = align(track, chunks(stream(a, promo, b, promo)))

    starts = [s.src_start_s for s in result.spans]
    assert starts == sorted(starts)
    assert all(later.src_start_s > earlier.src_end_s for earlier, later in zip(result.spans, result.spans[1:]))
    assert result.to_other(90.0) == pytest.approx(90.0 + len(promo) * FRAME_SECONDS, abs=0.01)
    assert result.anchors_outlier >= 15  # the early promo's hits were dropped


def test_isolated_hits_are_not_trusted():
    """Three seconds of matching audio is below the run threshold."""
    episode = frames(1, seconds(120))
    track = build_sync_track(chunks(stream(episode)), "g")
    snippet = episode[seconds(50) : seconds(53)]

    result = align(track, chunks(stream(frames(7, seconds(40)), snippet, frames(8, seconds(40)))))

    assert result.spans == []
    assert result.anchors_hit >= 2
    assert result.anchors_inlier == 0


def test_re_encoded_copy_does_not_match():
    track = build_sync_track(chunks(stream(frames(1, seconds(60)))), "g")
    result = align(track, chunks(stream(frames(99, seconds(60)))))
    assert result.spans == []
    assert result.anchors_hit == 0
