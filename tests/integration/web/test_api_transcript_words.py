# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""HTTP-level tests for ``GET /api/podcasts/{slug}/episodes/{slug}/transcript/words``.

The endpoint is the karaoke wipe feature's data source (spec #38). It reads
two sidecars off disk — the segmented ``AnnotatedTranscript`` JSON (for the
segment id ↔ ``source_word_span`` mapping) and the raw ``Transcript`` JSON
(for the actual ``Word`` objects) — then walks the spans and emits per-
cleaned-segment word lists.

These tests fixture up real JSON files in ``tmp_path`` and a real SQLite row,
so we exercise the full read path including ``PathManager`` resolution.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from thestill.models.podcast import Episode, Podcast


def _build_raw_transcript(*, with_word_timestamps: bool = True) -> dict:
    """Minimal raw ``Transcript`` JSON with two segments worth of words.

    When ``with_word_timestamps`` is False, words carry no ``start``/``end``
    fields — modelling a Whisper-CPU run that didn't surface them.
    """

    def word(text: str, start: float, end: float) -> dict:
        if not with_word_timestamps:
            return {"word": text}
        return {"word": text, "start": start, "end": end}

    return {
        "audio_file": "audio.wav",
        "language": "en",
        "text": "Hello world. Goodbye world.",
        "segments": [
            {
                "id": 0,
                "start": 0.0,
                "end": 1.5,
                "text": "Hello world.",
                "speaker": "SPEAKER_00",
                "words": [
                    word("Hello", 0.0, 0.5),
                    word("world.", 0.6, 1.5),
                ],
            },
            {
                "id": 1,
                "start": 2.0,
                "end": 3.8,
                "text": "Goodbye world.",
                "speaker": "SPEAKER_00",
                "words": [
                    word("Goodbye", 2.0, 2.9),
                    word("world.", 3.0, 3.8),
                ],
            },
        ],
        "processing_time": 0.1,
        "model_used": "whisper-test",
        "timestamp": 0.0,
    }


def _build_annotated_transcript(*, episode_id: str) -> dict:
    """Minimal ``AnnotatedTranscript`` JSON pointing at the raw segments above.

    Two cleaned segments, each with a ``source_word_span`` covering one raw
    segment's full word list — the simplest one-to-one mapping.
    """
    return {
        "episode_id": episode_id,
        "playback_time_offset_seconds": 0.0,
        "algorithm_version": "v1",
        "transcript_source_duration_s": None,
        "segments": [
            {
                "id": 0,
                "start": 0.0,
                "end": 1.5,
                "speaker": "SPEAKER_00",
                "text": "Hello world.",
                "kind": "content",
                "sponsor": None,
                "source_segment_ids": [0],
                "source_word_span": {
                    "start_segment_id": 0,
                    "start_word_index": 0,
                    "end_segment_id": 0,
                    "end_word_index": 1,
                },
                "user_segment_id": None,
                "metadata": {},
            },
            {
                "id": 1,
                "start": 2.0,
                "end": 3.8,
                "speaker": "SPEAKER_00",
                "text": "Goodbye world.",
                "kind": "content",
                "sponsor": None,
                "source_segment_ids": [1],
                "source_word_span": {
                    "start_segment_id": 1,
                    "start_word_index": 0,
                    "end_segment_id": 1,
                    "end_word_index": 1,
                },
                "user_segment_id": None,
                "metadata": {},
            },
        ],
    }


def _seed_episode(
    *,
    app_state,
    tmp_path: Path,
    raw_transcript: dict | None,
    annotated_transcript: dict | None,
    playback_offset: float = 0.0,
) -> tuple[str, str]:
    """Insert a podcast + episode row pointing at on-disk transcript fixtures.

    Returns ``(podcast_slug, episode_slug)`` so the test can construct the
    URL it needs to hit.
    """
    now = datetime.now(timezone.utc)
    podcast = Podcast(
        id="11111111-1111-1111-1111-111111111111",
        rss_url="https://example.com/feed.xml",
        title="Test Podcast",
        description="",
        slug="test-podcast",
        created_at=now,
        last_processed=now,
        episodes=[],
    )

    raw_filename = "test-episode_transcript.json" if raw_transcript is not None else None
    annotated_filename = "test-episode_annotated.json" if annotated_transcript is not None else None

    if raw_transcript is not None:
        raw_path = app_state.path_manager.raw_transcripts_dir() / raw_filename
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(json.dumps(raw_transcript), encoding="utf-8")
    if annotated_transcript is not None:
        annotated_path = app_state.path_manager.clean_transcripts_dir() / annotated_filename
        annotated_path.parent.mkdir(parents=True, exist_ok=True)
        annotated_path.write_text(json.dumps(annotated_transcript), encoding="utf-8")

    episode = Episode(
        id="22222222-2222-2222-2222-222222222222",
        external_id="guid-test",
        title="Test Episode",
        description="",
        slug="test-episode",
        pub_date=now,
        audio_url="https://example.com/audio.mp3",
        raw_transcript_path=raw_filename,
        clean_transcript_json_path=annotated_filename,
        playback_time_offset_seconds=playback_offset,
    )
    podcast.episodes = [episode]
    app_state.repository.save(podcast)
    return podcast.slug, episode.slug


def test_happy_path_returns_words_by_segment(client, app_state, tmp_path):
    """Episode with both sidecars present → 200 with one ``SegmentWords`` per
    AnnotatedSegment, words in raw audio seconds, and ``episode_id`` echoed."""
    podcast_slug, episode_slug = _seed_episode(
        app_state=app_state,
        tmp_path=tmp_path,
        raw_transcript=_build_raw_transcript(),
        annotated_transcript=_build_annotated_transcript(episode_id="22222222-2222-2222-2222-222222222222"),
    )
    response = client.get(f"/api/podcasts/{podcast_slug}/episodes/{episode_slug}/transcript/words")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["episode_id"] == "22222222-2222-2222-2222-222222222222"
    assert body["playback_time_offset_seconds"] == 0.0
    assert len(body["segments"]) == 2

    first_seg = body["segments"][0]
    assert first_seg["segment_id"] == 0
    assert [w["w"] for w in first_seg["words"]] == ["Hello", "world."]
    assert first_seg["words"][0]["s"] == 0.0
    assert first_seg["words"][0]["e"] == 0.5

    second_seg = body["segments"][1]
    assert second_seg["segment_id"] == 1
    assert [w["w"] for w in second_seg["words"]] == ["Goodbye", "world."]


def test_offset_metadata_echoed_words_remain_raw(client, app_state, tmp_path):
    """Non-zero playback offset is surfaced in the response envelope but NOT
    applied to per-word ``s``/``e`` — the client folds the offset in."""
    podcast_slug, episode_slug = _seed_episode(
        app_state=app_state,
        tmp_path=tmp_path,
        raw_transcript=_build_raw_transcript(),
        annotated_transcript=_build_annotated_transcript(episode_id="22222222-2222-2222-2222-222222222222"),
        playback_offset=12.5,
    )
    response = client.get(f"/api/podcasts/{podcast_slug}/episodes/{episode_slug}/transcript/words")
    assert response.status_code == 200
    body = response.json()
    assert body["playback_time_offset_seconds"] == 12.5
    # The first raw word starts at 0.0 — confirm the offset was not folded in.
    assert body["segments"][0]["words"][0]["s"] == 0.0


def test_missing_raw_transcript_returns_404(client, app_state, tmp_path):
    """Episode has segmented JSON but no raw transcript on disk."""
    podcast_slug, episode_slug = _seed_episode(
        app_state=app_state,
        tmp_path=tmp_path,
        raw_transcript=None,
        annotated_transcript=_build_annotated_transcript(episode_id="22222222-2222-2222-2222-222222222222"),
    )
    response = client.get(f"/api/podcasts/{podcast_slug}/episodes/{episode_slug}/transcript/words")
    assert response.status_code == 404


def test_missing_segmented_transcript_returns_404(client, app_state, tmp_path):
    """Raw transcript present, segmented JSON path empty on the episode row."""
    podcast_slug, episode_slug = _seed_episode(
        app_state=app_state,
        tmp_path=tmp_path,
        raw_transcript=_build_raw_transcript(),
        annotated_transcript=None,
    )
    response = client.get(f"/api/podcasts/{podcast_slug}/episodes/{episode_slug}/transcript/words")
    assert response.status_code == 404


def test_no_word_timestamps_returns_404(client, app_state, tmp_path):
    """Whisper-CPU-style raw transcript with words but no per-word start/end
    → no resolvable wipe data → 404, frontend falls back to segment highlight."""
    podcast_slug, episode_slug = _seed_episode(
        app_state=app_state,
        tmp_path=tmp_path,
        raw_transcript=_build_raw_transcript(with_word_timestamps=False),
        annotated_transcript=_build_annotated_transcript(episode_id="22222222-2222-2222-2222-222222222222"),
    )
    response = client.get(f"/api/podcasts/{podcast_slug}/episodes/{episode_slug}/transcript/words")
    assert response.status_code == 404


def test_corrupted_source_word_span_returns_500(client, app_state, tmp_path):
    """A ``source_word_span`` pointing at a raw segment that doesn't exist
    is data corruption — the resulting KeyError propagates to the generic
    500 handler rather than silently producing a malformed response."""
    annotated = _build_annotated_transcript(episode_id="22222222-2222-2222-2222-222222222222")
    # Point the first cleaned segment at a non-existent raw segment id.
    annotated["segments"][0]["source_word_span"]["start_segment_id"] = 99
    annotated["segments"][0]["source_word_span"]["end_segment_id"] = 99

    podcast_slug, episode_slug = _seed_episode(
        app_state=app_state,
        tmp_path=tmp_path,
        raw_transcript=_build_raw_transcript(),
        annotated_transcript=annotated,
    )
    response = client.get(f"/api/podcasts/{podcast_slug}/episodes/{episode_slug}/transcript/words")
    assert response.status_code == 500


def test_unknown_episode_returns_404(client, app_state):
    """Slug pair doesn't match any episode row → 404 (Episode not found),
    not a 500 from trying to read sidecars off a missing row."""
    response = client.get("/api/podcasts/nope/episodes/nope/transcript/words")
    assert response.status_code == 404
