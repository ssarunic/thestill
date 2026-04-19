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

"""Byte-identical round-trip tests for :mod:`thestill.models.annotated_transcript`.

These tests are the load-bearing guarantee that
``AnnotatedTranscript.from_raw(t).to_blended_markdown()`` produces output
indistinguishable from ``TranscriptFormatter.format_transcript(t.model_dump())``.
The summariser continues to read the blended Markdown during Phase C/D/E
of spec #18, so any drift here breaks an existing feature silently.

The two renderers must stay in sync across: speaker merging, the 300-second
timecode-interval rule, HH:MM:SS vs MM:SS formatting, text normalisation
(whitespace collapse, punctuation spacing, ellipsis), and speaker-label
fallback for missing speakers. One test per behaviour so drift localises.
"""

from typing import List, Optional

from thestill.core.transcript_formatter import TranscriptFormatter
from thestill.models.annotated_transcript import AnnotatedSegment, AnnotatedTranscript, WordSpan
from thestill.models.transcript import Segment, Transcript, Word


def _segment(
    *,
    seg_id: int,
    start: float,
    end: float,
    text: str,
    speaker: Optional[str],
) -> Segment:
    """Minimal segment factory — no word list needed for render-only tests."""
    return Segment(id=seg_id, start=start, end=end, text=text, speaker=speaker, words=[])


def _transcript(segments: List[Segment]) -> Transcript:
    return Transcript(
        audio_file="test.wav",
        language="en",
        text=" ".join(seg.text for seg in segments),
        segments=segments,
        processing_time=0.0,
        model_used="fixture",
        timestamp=0.0,
    )


def _assert_byte_identical(transcript: Transcript) -> None:
    """Assert that both renderers produce exactly the same bytes."""
    legacy = TranscriptFormatter().format_transcript(transcript.model_dump())
    new = AnnotatedTranscript.from_raw(transcript).to_blended_markdown()
    assert new == legacy, "byte-identical renderer drift:\n" f"legacy:\n{legacy!r}\n" f"new:\n{new!r}"


class TestByteIdenticalRenderParity:
    """``from_raw(t).to_blended_markdown()`` == ``TranscriptFormatter(t).format``."""

    def test_two_speakers_simple(self) -> None:
        transcript = _transcript(
            [
                _segment(seg_id=0, start=0.0, end=1.5, text="Hello world.", speaker="SPEAKER_00"),
                _segment(seg_id=1, start=2.0, end=4.0, text="Good morning.", speaker="SPEAKER_01"),
            ]
        )
        _assert_byte_identical(transcript)

    def test_same_speaker_merges_into_one_block(self) -> None:
        """Consecutive same-speaker segments flatten into one timestamped line."""
        transcript = _transcript(
            [
                _segment(seg_id=0, start=0.0, end=10.0, text="First sentence.", speaker="HOST"),
                _segment(seg_id=1, start=10.5, end=20.0, text="Second sentence.", speaker="HOST"),
                _segment(seg_id=2, start=20.5, end=30.0, text="Third sentence.", speaker="HOST"),
            ]
        )
        _assert_byte_identical(transcript)

    def test_timecode_rolls_over_at_300_second_interval(self) -> None:
        """Same-speaker run over 5 minutes still gets a fresh [MM:SS] stamp."""
        transcript = _transcript(
            [
                _segment(seg_id=0, start=0.0, end=10.0, text="early", speaker="HOST"),
                _segment(seg_id=1, start=50.0, end=60.0, text="middle", speaker="HOST"),
                _segment(seg_id=2, start=400.0, end=410.0, text="after rollover", speaker="HOST"),
            ]
        )
        _assert_byte_identical(transcript)

    def test_hh_mm_ss_formatting_over_one_hour(self) -> None:
        """Segments past 3600s use ``[HH:MM:SS]`` instead of ``[MM:SS]``."""
        transcript = _transcript(
            [
                _segment(seg_id=0, start=0.0, end=1.0, text="start", speaker="A"),
                _segment(seg_id=1, start=4000.0, end=4005.0, text="much later", speaker="B"),
            ]
        )
        _assert_byte_identical(transcript)

    def test_text_normalisation_collapses_whitespace(self) -> None:
        """Multiple spaces, leading/trailing whitespace, punctuation spacing."""
        transcript = _transcript(
            [
                _segment(
                    seg_id=0,
                    start=0.0,
                    end=5.0,
                    text="  hello    world  ,  how   are   you ?  ",
                    speaker="A",
                ),
            ]
        )
        _assert_byte_identical(transcript)

    def test_text_normalisation_collapses_ellipses(self) -> None:
        """Multi-dot ellipses collapse to exactly three dots."""
        transcript = _transcript(
            [
                _segment(seg_id=0, start=0.0, end=2.0, text="well.....", speaker="A"),
            ]
        )
        _assert_byte_identical(transcript)

    def test_missing_speaker_renders_as_literal_none(self) -> None:
        """``speaker=None`` renders as the literal string ``**None:**``.

        The legacy ``TranscriptFormatter`` uses ``dict.get("speaker",
        default)`` which only returns the default when the key is missing
        — not when its value is ``None``. Pydantic always includes the
        key, so ``None`` passes through unchanged and gets formatted by
        f-string interpolation as the literal ``"None"``. This is a mild
        legacy bug, but byte parity with the existing summariser input
        requires us to reproduce it faithfully.
        """
        transcript = _transcript(
            [
                _segment(seg_id=0, start=0.0, end=1.0, text="anonymous", speaker=None),
            ]
        )
        _assert_byte_identical(transcript)

    def test_single_segment_transcript(self) -> None:
        """A one-segment transcript — the degenerate-but-legitimate case."""
        transcript = _transcript(
            [
                _segment(seg_id=0, start=0.0, end=60.0, text="monologue content", speaker="A"),
            ]
        )
        _assert_byte_identical(transcript)

    def test_empty_text_segment_within_run(self) -> None:
        """Empty-text segment in the middle shouldn't break merging."""
        transcript = _transcript(
            [
                _segment(seg_id=0, start=0.0, end=1.0, text="before", speaker="A"),
                _segment(seg_id=1, start=1.1, end=1.2, text="", speaker="A"),
                _segment(seg_id=2, start=1.3, end=2.0, text="after", speaker="A"),
            ]
        )
        _assert_byte_identical(transcript)

    def test_first_segment_starts_after_t_zero(self) -> None:
        """Leading silence / trimmed-intro case: first segment at ``t >= 1s``.

        Representative of most real-world podcasts (Whisper VAD trims
        leading silence; intro music strips the first few seconds). The
        legacy formatter stamps the first block at ``[00:00]`` regardless
        of real start — a quirk, but one the summariser's citations
        already depend on. Byte parity must hold here or cleaned output
        silently drifts from every legacy consumer on every real episode.
        """
        transcript = _transcript(
            [
                _segment(seg_id=0, start=2.0, end=5.0, text="Welcome.", speaker="HOST"),
                _segment(seg_id=1, start=5.5, end=10.0, text="Thanks.", speaker="GUEST"),
            ]
        )
        _assert_byte_identical(transcript)

    def test_first_segment_starts_well_into_episode(self) -> None:
        """Longer leading trim — first segment at ``t = 30s``."""
        transcript = _transcript(
            [
                _segment(seg_id=0, start=30.0, end=40.0, text="First words.", speaker="HOST"),
                _segment(seg_id=1, start=40.5, end=50.0, text="More.", speaker="HOST"),
                _segment(seg_id=2, start=50.5, end=60.0, text="Guest turn.", speaker="GUEST"),
            ]
        )
        _assert_byte_identical(transcript)

    def test_first_segment_long_enough_to_force_timecode_rollover(self) -> None:
        """First segment at ``t = 10s``, same-speaker chain past 300s.

        Tests that the 300s interval check fires at the same segment as
        legacy — legacy leaves ``last_timecode`` at 0 on the first run
        so its check is ``start_time - 0 >= 300``, not
        ``start_time - 10 >= 300``. A subtle drift here would silently
        shift every downstream speaker block.
        """
        transcript = _transcript(
            [
                _segment(seg_id=0, start=10.0, end=20.0, text="early", speaker="HOST"),
                _segment(seg_id=1, start=100.0, end=110.0, text="middle", speaker="HOST"),
                _segment(seg_id=2, start=310.0, end=320.0, text="after rollover", speaker="HOST"),
            ]
        )
        _assert_byte_identical(transcript)


class TestFromRawShape:
    """``from_raw`` faithfully wraps every raw segment 1:1."""

    def test_preserves_segment_count(self) -> None:
        raw = _transcript(
            [
                _segment(seg_id=0, start=0.0, end=1.0, text="a", speaker="A"),
                _segment(seg_id=1, start=1.0, end=2.0, text="b", speaker="B"),
                _segment(seg_id=2, start=2.0, end=3.0, text="c", speaker="A"),
            ]
        )

        annotated = AnnotatedTranscript.from_raw(raw)

        assert len(annotated.segments) == 3

    def test_populates_source_segment_ids(self) -> None:
        raw = _transcript(
            [
                _segment(seg_id=42, start=0.0, end=1.0, text="first", speaker="A"),
                _segment(seg_id=43, start=1.0, end=2.0, text="second", speaker="B"),
            ]
        )

        annotated = AnnotatedTranscript.from_raw(raw)

        assert annotated.segments[0].source_segment_ids == [42]
        assert annotated.segments[1].source_segment_ids == [43]

    def test_populates_word_span_when_words_present(self) -> None:
        raw = Transcript(
            audio_file="x",
            language="en",
            text="hello world",
            segments=[
                Segment(
                    id=5,
                    start=0.0,
                    end=1.5,
                    text="hello world",
                    speaker="A",
                    words=[
                        Word(word="hello", start=0.0, end=0.5),
                        Word(word="world", start=0.6, end=1.5),
                    ],
                )
            ],
            processing_time=0.0,
            model_used="x",
            timestamp=0.0,
        )

        annotated = AnnotatedTranscript.from_raw(raw)

        span = annotated.segments[0].source_word_span
        assert span is not None
        assert span.start_segment_id == 5
        assert span.start_word_index == 0
        assert span.end_segment_id == 5
        assert span.end_word_index == 1

    def test_leaves_word_span_none_when_no_words(self) -> None:
        """A raw segment without word timestamps still produces an entry."""
        raw = _transcript([_segment(seg_id=0, start=0.0, end=1.0, text="orphan", speaker="A")])

        annotated = AnnotatedTranscript.from_raw(raw)

        assert annotated.segments[0].source_word_span is None

    def test_assigns_positional_ids(self) -> None:
        raw = _transcript(
            [
                _segment(seg_id=100, start=0.0, end=1.0, text="a", speaker="A"),
                _segment(seg_id=200, start=1.0, end=2.0, text="b", speaker="B"),
            ]
        )

        annotated = AnnotatedTranscript.from_raw(raw)

        assert [s.id for s in annotated.segments] == [0, 1]
        # Source ids remain as-is, proving positional ids don't overwrite source anchors.
        assert annotated.segments[0].source_segment_ids == [100]
        assert annotated.segments[1].source_segment_ids == [200]


class TestNonContentKinds:
    """Filler segments drop entirely; ad breaks emit the legacy marker."""

    def test_filler_segments_are_dropped_from_output(self) -> None:
        annotated = AnnotatedTranscript(
            episode_id="ep",
            segments=[
                AnnotatedSegment(id=0, start=0.0, end=1.0, speaker="A", text="hello"),
                AnnotatedSegment(id=1, start=1.0, end=2.0, speaker="A", text="um", kind="filler"),
                AnnotatedSegment(id=2, start=2.0, end=3.0, speaker="A", text="world"),
            ],
        )

        rendered = annotated.to_blended_markdown()

        assert "um" not in rendered
        assert "hello world" in rendered  # merged into one speaker block

    def test_ad_break_emits_legacy_marker(self) -> None:
        annotated = AnnotatedTranscript(
            episode_id="ep",
            segments=[
                AnnotatedSegment(id=0, start=0.0, end=10.0, speaker="A", text="content before"),
                AnnotatedSegment(
                    id=1,
                    start=10.0,
                    end=60.0,
                    speaker=None,
                    text="",
                    kind="ad_break",
                    sponsor="Acme",
                ),
                AnnotatedSegment(id=2, start=60.0, end=70.0, speaker="A", text="content after"),
            ],
        )

        rendered = annotated.to_blended_markdown()

        assert "**[00:10] [AD BREAK]** - Acme" in rendered
        assert "content before" in rendered
        assert "content after" in rendered

    def test_ad_break_without_sponsor_omits_the_dash(self) -> None:
        annotated = AnnotatedTranscript(
            episode_id="ep",
            segments=[
                AnnotatedSegment(
                    id=0,
                    start=5.0,
                    end=30.0,
                    speaker=None,
                    text="",
                    kind="ad_break",
                ),
            ],
        )

        rendered = annotated.to_blended_markdown()

        # The legacy format for an anonymous ad break is just the bracketed marker.
        assert "**[00:05] [AD BREAK]**" in rendered
        assert " - " not in rendered


class TestExcludeKindsFilter:
    """``exclude_kinds`` drops tagged segments while preserving the rest.

    The canonical Markdown projection is ads-stripped (``exclude_kinds=
    {"ad_break"}``). Other callers — notably the web viewer with ads
    visible — render from JSON directly and don't touch this renderer,
    but the filter is general enough to accept any kind the schema
    supports.
    """

    def test_exclude_ad_break_drops_marker_and_text(self) -> None:
        annotated = AnnotatedTranscript(
            episode_id="ep",
            segments=[
                AnnotatedSegment(id=0, start=0.0, end=10.0, speaker="A", text="before"),
                AnnotatedSegment(
                    id=1,
                    start=10.0,
                    end=60.0,
                    speaker=None,
                    text="support for the show comes from acme",
                    kind="ad_break",
                    sponsor="Acme",
                ),
                AnnotatedSegment(id=2, start=60.0, end=70.0, speaker="A", text="after"),
            ],
        )

        rendered = annotated.to_blended_markdown(exclude_kinds={"ad_break"})

        assert "[AD BREAK]" not in rendered
        assert "Acme" not in rendered
        assert "support for the show" not in rendered
        assert "before" in rendered
        assert "after" in rendered

    def test_exclude_ad_break_creates_block_boundary(self) -> None:
        """A skipped ad break still breaks the surrounding speaker run —
        the segment after the ad must stamp a fresh timecode rather than
        merging into the pre-ad content block."""
        annotated = AnnotatedTranscript(
            episode_id="ep",
            segments=[
                AnnotatedSegment(id=0, start=0.0, end=10.0, speaker="A", text="before"),
                AnnotatedSegment(
                    id=1,
                    start=10.0,
                    end=60.0,
                    speaker=None,
                    text="ad copy",
                    kind="ad_break",
                ),
                AnnotatedSegment(id=2, start=60.0, end=70.0, speaker="A", text="after"),
            ],
        )

        rendered = annotated.to_blended_markdown(exclude_kinds={"ad_break"})

        assert "[00:00] **A:** before" in rendered
        assert "[01:00] **A:** after" in rendered

    def test_default_exclude_kinds_preserves_legacy_ad_marker(self) -> None:
        """Backward compat: when the caller omits ``exclude_kinds``, ad
        breaks still emit the legacy marker they always did."""
        annotated = AnnotatedTranscript(
            episode_id="ep",
            segments=[
                AnnotatedSegment(
                    id=0,
                    start=10.0,
                    end=60.0,
                    speaker=None,
                    text="ad copy",
                    kind="ad_break",
                    sponsor="Acme",
                ),
            ],
        )

        rendered = annotated.to_blended_markdown()

        assert "**[00:10] [AD BREAK]** - Acme" in rendered

    def test_exclude_multiple_kinds(self) -> None:
        annotated = AnnotatedTranscript(
            episode_id="ep",
            segments=[
                AnnotatedSegment(id=0, start=0.0, end=5.0, speaker="A", text="theme", kind="music"),
                AnnotatedSegment(id=1, start=5.0, end=10.0, speaker="A", text="hello everyone", kind="intro"),
                AnnotatedSegment(id=2, start=10.0, end=20.0, speaker="A", text="main topic"),
            ],
        )

        rendered = annotated.to_blended_markdown(exclude_kinds={"music", "intro"})

        assert "theme" not in rendered
        assert "hello everyone" not in rendered
        assert "main topic" in rendered

    def test_exclude_empty_iterable_is_noop(self) -> None:
        """Passing an empty set must behave identically to not passing the arg."""
        annotated = AnnotatedTranscript(
            episode_id="ep",
            segments=[
                AnnotatedSegment(id=0, start=0.0, end=5.0, speaker="A", text="hello"),
                AnnotatedSegment(
                    id=1,
                    start=5.0,
                    end=10.0,
                    speaker=None,
                    text="",
                    kind="ad_break",
                    sponsor="Acme",
                ),
            ],
        )

        assert annotated.to_blended_markdown(exclude_kinds=set()) == annotated.to_blended_markdown()


class TestExtendedSegmentKinds:
    """``music``/``intro``/``outro`` are accepted kinds that render as
    speaker blocks by default — the UI / summariser feeds decide whether
    to strip them via ``exclude_kinds``."""

    def test_music_segment_renders_as_speaker_block(self) -> None:
        annotated = AnnotatedTranscript(
            episode_id="ep",
            segments=[
                AnnotatedSegment(id=0, start=0.0, end=5.0, speaker="A", text="theme plays", kind="music"),
            ],
        )

        rendered = annotated.to_blended_markdown()

        assert "**A:** theme plays" in rendered

    def test_intro_and_outro_round_trip_through_the_schema(self) -> None:
        """The Pydantic Literal accepts every extended kind the spec declares."""
        annotated = AnnotatedTranscript(
            episode_id="ep",
            segments=[
                AnnotatedSegment(id=0, start=0.0, end=5.0, speaker="A", text="welcome", kind="intro"),
                AnnotatedSegment(id=1, start=5.0, end=10.0, speaker="A", text="main"),
                AnnotatedSegment(id=2, start=10.0, end=15.0, speaker="A", text="thanks for listening", kind="outro"),
            ],
        )

        dumped = annotated.model_dump_json()
        restored = AnnotatedTranscript.model_validate_json(dumped)

        assert [s.kind for s in restored.segments] == ["intro", "content", "outro"]


class TestLeadingNonContentSegments:
    """Regression: leading filler/ad_break must not force the first content
    block to ``[00:00]``. The legacy formatter left ``block_start_time``
    at 0.0 until the first flush, which hid a latent bug that becomes
    reachable once ``filler`` and ``ad_break`` segments can precede
    content. The fix anchors every fresh block to its segment's real
    start time."""

    def test_leading_filler_does_not_zero_the_next_content_timestamp(self) -> None:
        annotated = AnnotatedTranscript(
            episode_id="ep",
            segments=[
                AnnotatedSegment(id=0, start=0.0, end=5.0, speaker="A", text="um", kind="filler"),
                AnnotatedSegment(id=1, start=30.0, end=40.0, speaker="A", text="real content"),
            ],
        )

        rendered = annotated.to_blended_markdown()

        # The filler segment is dropped, but the content segment must
        # stamp at its actual start (30s), not at the [00:00] init value.
        assert "[00:30] **A:** real content" in rendered
        assert "[00:00]" not in rendered

    def test_leading_ad_break_does_not_zero_the_next_content_timestamp(self) -> None:
        annotated = AnnotatedTranscript(
            episode_id="ep",
            segments=[
                AnnotatedSegment(
                    id=0,
                    start=0.0,
                    end=60.0,
                    speaker=None,
                    text="",
                    kind="ad_break",
                    sponsor="Acme",
                ),
                AnnotatedSegment(id=1, start=60.0, end=70.0, speaker="A", text="post-ad content"),
            ],
        )

        rendered = annotated.to_blended_markdown()

        # Ad break stamps at its own start (00:00), content stamps at its
        # real start (01:00) — not re-using the ad break's stamp.
        assert "**[00:00] [AD BREAK]** - Acme" in rendered
        assert "[01:00] **A:** post-ad content" in rendered


class TestPlaybackOffset:
    """The playback offset is applied at render time for all content timecodes."""

    def test_offset_shifts_content_timestamps(self) -> None:
        annotated = AnnotatedTranscript(
            episode_id="ep",
            playback_time_offset_seconds=30.0,
            segments=[
                AnnotatedSegment(id=0, start=0.0, end=5.0, speaker="A", text="first"),
                AnnotatedSegment(id=1, start=10.0, end=15.0, speaker="B", text="second"),
            ],
        )

        rendered = annotated.to_blended_markdown()

        # First segment's timestamp is always 0 in the legacy formatter (it
        # never overwrites block_start_time on the very first segment). The
        # offset only becomes visible on subsequent speaker-block rollovers.
        assert "[00:40]" in rendered  # 10 + 30 offset for the second speaker

    def test_offset_zero_matches_unshifted(self) -> None:
        base = AnnotatedTranscript(
            episode_id="ep",
            segments=[
                AnnotatedSegment(id=0, start=0.0, end=5.0, speaker="A", text="one"),
                AnnotatedSegment(id=1, start=10.0, end=15.0, speaker="B", text="two"),
            ],
        )
        explicit_zero = AnnotatedTranscript(
            episode_id="ep",
            playback_time_offset_seconds=0.0,
            segments=base.segments,
        )

        assert base.to_blended_markdown() == explicit_zero.to_blended_markdown()
