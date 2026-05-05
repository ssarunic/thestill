# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Unit tests for the Parakeet (TDT) transcriber's hypothesis mapping.

The model itself is heavy (NeMo + ~2GB checkpoint), so these tests
exercise ``_format_transcript`` directly with synthetic hypotheses
that mirror NeMo's documented shape. Goal: lock in that native
segment/word timestamps survive the mapping into ``Transcript``.
"""

from types import SimpleNamespace

import pytest

from thestill.core.parakeet_transcriber import ParakeetTranscriber


@pytest.fixture
def transcriber():
    t = ParakeetTranscriber.__new__(ParakeetTranscriber)
    t.model_name = "nvidia/parakeet-tdt-0.6b-v3"
    t.device = "cpu"

    class _SilentConsole:
        def info(self, *_a, **_k):
            pass

        def success(self, *_a, **_k):
            pass

        def error(self, *_a, **_k):
            pass

        def warning(self, *_a, **_k):
            pass

    t.console = _SilentConsole()
    t._model = None
    return t


def test_format_transcript_maps_native_segment_and_word_timestamps(transcriber):
    """A NeMo-shaped hypothesis maps cleanly into Transcript segments + words."""
    hypothesis = SimpleNamespace(
        text="hello world this is a test",
        timestamp={
            "segment": [
                {"segment": "hello world", "start": 0.0, "end": 1.4},
                {"segment": "this is a test", "start": 1.4, "end": 3.2},
            ],
            "word": [
                {"word": "hello", "start": 0.0, "end": 0.6},
                {"word": "world", "start": 0.7, "end": 1.4},
                {"word": "this", "start": 1.4, "end": 1.7},
                {"word": "is", "start": 1.8, "end": 1.9},
                {"word": "a", "start": 2.0, "end": 2.1},
                {"word": "test", "start": 2.5, "end": 3.2},
            ],
        },
    )

    transcript = transcriber._format_transcript(
        hypothesis, processing_time=1.23, audio_path="/tmp/a.wav", language="en"
    )

    assert transcript.text == "hello world this is a test"
    assert transcript.language == "en"
    assert transcript.model_used == "nvidia/parakeet-tdt-0.6b-v3"
    assert len(transcript.segments) == 2

    seg0, seg1 = transcript.segments
    assert seg0.id == 0 and seg0.start == 0.0 and seg0.end == 1.4
    assert seg0.text == "hello world"
    assert [w.word for w in seg0.words] == ["hello", "world"]
    assert seg0.words[0].start == 0.0 and seg0.words[0].end == 0.6

    assert seg1.id == 1 and seg1.start == 1.4 and seg1.end == 3.2
    assert [w.word for w in seg1.words] == ["this", "is", "a", "test"]


def test_format_transcript_handles_dict_shaped_hypothesis(transcriber):
    """Older NeMo versions returned dicts; we accept both surfaces."""
    hypothesis = {
        "text": "one two",
        "timestamp": {
            "segment": [{"segment": "one two", "start": 0.0, "end": 0.8}],
            "word": [
                {"word": "one", "start": 0.0, "end": 0.3},
                {"word": "two", "start": 0.4, "end": 0.8},
            ],
        },
    }

    transcript = transcriber._format_transcript(hypothesis, processing_time=0.5, audio_path="/tmp/b.wav", language="en")

    assert transcript.text == "one two"
    assert len(transcript.segments) == 1
    assert [w.word for w in transcript.segments[0].words] == ["one", "two"]


def test_format_transcript_falls_back_to_stub_when_no_timestamps(transcriber):
    """Missing timestamps -> single zero-length stub so cleanup picks legacy."""
    hypothesis = SimpleNamespace(text="ungrouped text", timestamp={})

    transcript = transcriber._format_transcript(hypothesis, processing_time=0.1, audio_path="/tmp/c.wav", language="en")

    assert len(transcript.segments) == 1
    seg = transcript.segments[0]
    assert seg.start == 0.0 and seg.end == 0.0
    assert seg.text == "ungrouped text"
    assert seg.words == []


def test_format_transcript_buckets_words_outside_window_to_nearest(transcriber):
    """Floating-point edge: a word starting just past a segment end still lands."""
    hypothesis = SimpleNamespace(
        text="alpha beta",
        timestamp={
            "segment": [
                {"segment": "alpha", "start": 0.0, "end": 0.5},
                {"segment": "beta", "start": 0.6, "end": 1.0},
            ],
            "word": [
                {"word": "alpha", "start": 0.0, "end": 0.5},
                # Starts at 0.55 — outside both windows, but closer to seg 1's start (0.6).
                {"word": "beta", "start": 0.55, "end": 1.0},
            ],
        },
    )

    transcript = transcriber._format_transcript(hypothesis, processing_time=0.2, audio_path="/tmp/d.wav", language="en")

    assert [[w.word for w in s.words] for s in transcript.segments] == [["alpha"], ["beta"]]


def test_format_transcript_uses_caller_language(transcriber):
    """Caller-supplied language tag is recorded on the Transcript."""
    hypothesis = SimpleNamespace(
        text="bonjour le monde",
        timestamp={
            "segment": [{"segment": "bonjour le monde", "start": 0.0, "end": 1.0}],
            "word": [
                {"word": "bonjour", "start": 0.0, "end": 0.4},
                {"word": "le", "start": 0.5, "end": 0.6},
                {"word": "monde", "start": 0.7, "end": 1.0},
            ],
        },
    )

    transcript = transcriber._format_transcript(hypothesis, processing_time=0.3, audio_path="/tmp/e.wav", language="fr")

    assert transcript.language == "fr"


def test_format_transcript_prefers_hypothesis_language_when_present(transcriber):
    """If NeMo attached a detected language, it overrides the caller hint."""
    hypothesis = SimpleNamespace(
        text="hola mundo",
        language="es",
        timestamp={
            "segment": [{"segment": "hola mundo", "start": 0.0, "end": 0.8}],
            "word": [
                {"word": "hola", "start": 0.0, "end": 0.3},
                {"word": "mundo", "start": 0.4, "end": 0.8},
            ],
        },
    )

    transcript = transcriber._format_transcript(hypothesis, processing_time=0.3, audio_path="/tmp/f.wav", language="en")

    assert transcript.language == "es"
