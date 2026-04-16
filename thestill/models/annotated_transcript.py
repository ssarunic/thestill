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

"""Structured annotated-transcript models (spec #18, Phase B).

``AnnotatedTranscript`` is the primary artefact of the new segmented
cleaning pipeline: a list of ``AnnotatedSegment`` rows, each carrying a
stable ``(start, end)`` anchor, a back-reference to the raw JSON segments
it was built from, and — for content segments — a ``source_word_span``
pointing into the raw word stream for future word-level highlighting.

Words are addressed **positionally** via ``(raw_segment_id, word_index)``
because the raw ``Word`` model has no intrinsic id field. The raw
transcript on disk is write-once, so positional addressing is stable.

The blended-Markdown renderer (``to_blended_markdown``) must reproduce
``TranscriptFormatter.format_transcript`` output byte-for-byte when called
on a ``from_raw`` wrapper, so the summariser keeps working unchanged
during the transition. The two private text-formatting helpers below are
deliberate duplicates of the equivalents in ``TranscriptFormatter``; the
byte-identical round-trip test in ``tests/unit/models/`` is the load-bearing
guarantee that the two stay in sync.
"""

import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from thestill.models.transcript import Transcript

SegmentKind = Literal["content", "filler", "ad_break"]

# Timecode granularity used by the legacy blended-Markdown formatter: one
# speaker block gets a fresh [HH:MM:SS] stamp either on speaker change or
# every 300 seconds within a same-speaker run.
_BLENDED_TIMECODE_INTERVAL_SECONDS: float = 300.0


class WordSpan(BaseModel):
    """Positional pointer into the raw transcript's word stream.

    All fields are inclusive. ``(start_segment_id, start_word_index)`` and
    ``(end_segment_id, end_word_index)`` bound a contiguous slice of the
    raw transcript's words. The raw JSON is write-once, so the addresses
    remain valid for the lifetime of the episode.
    """

    start_segment_id: int
    start_word_index: int
    end_segment_id: int
    end_word_index: int


class AnnotatedSegment(BaseModel):
    """One row of the cleaned transcript.

    ``id`` is positional within the enclosing ``AnnotatedTranscript`` — it
    is assigned by the segmenter/cleaner and is what LLM patches reference.
    It is deterministic per ``(input, algorithm_version)`` but NOT stable
    across algorithm changes.

    ``source_segment_ids`` is the durable anchor. These are raw
    ``Segment.id`` values from the immutable raw JSON, so they survive
    re-cleans trivially. Anything that must persist across re-cleans
    (bookmarks, user edits, future eval ground truth) keys off this field.

    ``user_segment_id`` is reserved and unpopulated in spec #18 — the
    editing-UI follow-up spec will assign UUIDs here on first user edit.
    """

    id: int
    start: float
    end: float
    speaker: Optional[str] = None
    text: str
    kind: SegmentKind = "content"
    sponsor: Optional[str] = None
    source_segment_ids: List[int] = Field(default_factory=list)
    source_word_span: Optional[WordSpan] = None
    user_segment_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AnnotatedTranscript(BaseModel):
    """Top-level container for the segmented-cleanup output.

    ``playback_time_offset_seconds`` is a cached copy of the DB-authoritative
    value (see spec #18 §"Audio source"). The service layer overwrites it
    from the DB on every read, so the JSON sidecar is a write-through cache
    rather than an independent source of truth.

    ``algorithm_version`` is bumped whenever the segmenter or cleaner's
    output contract changes in a way that invalidates cached JSON sidecars
    on disk. This lets a future re-clean detect stale artefacts by comparing
    versions; spec #18 itself ships ``v1``.
    """

    episode_id: str
    segments: List[AnnotatedSegment]
    playback_time_offset_seconds: float = 0.0
    algorithm_version: str = "v1"

    @classmethod
    def from_raw(cls, transcript: Transcript, *, episode_id: str = "") -> "AnnotatedTranscript":
        """Wrap a raw ``Transcript`` 1:1 without repair or cleanup.

        Used (a) by the service layer's raw-fallback rendering path and
        (b) by the byte-identical round-trip test. Every raw segment maps
        to exactly one ``AnnotatedSegment`` with ``kind="content"``,
        ``source_segment_ids=[seg.id]``, and a ``source_word_span`` covering
        the segment's full word list when any words are present.
        """
        segments: List[AnnotatedSegment] = []
        for index, raw_segment in enumerate(transcript.segments):
            word_span: Optional[WordSpan] = None
            if raw_segment.words:
                word_span = WordSpan(
                    start_segment_id=raw_segment.id,
                    start_word_index=0,
                    end_segment_id=raw_segment.id,
                    end_word_index=len(raw_segment.words) - 1,
                )
            segments.append(
                AnnotatedSegment(
                    id=index,
                    start=raw_segment.start,
                    end=raw_segment.end,
                    speaker=raw_segment.speaker,
                    text=raw_segment.text,
                    kind="content",
                    source_segment_ids=[raw_segment.id],
                    source_word_span=word_span,
                )
            )
        return cls(episode_id=episode_id, segments=segments)

    def to_blended_markdown(
        self,
        *,
        timecode_interval_seconds: float = _BLENDED_TIMECODE_INTERVAL_SECONDS,
    ) -> str:
        """Render this transcript to the legacy blended-Markdown format.

        Reproduces ``TranscriptFormatter.format_transcript`` byte-for-byte
        when invoked on a ``from_raw`` wrapper — this is the guarantee the
        summariser depends on during the transition (spec #18 §"Scope").

        Rules in addition to the legacy formatter's behaviour:
        - ``kind="filler"`` segments are dropped entirely.
        - ``kind="ad_break"`` segments flush any pending speaker block and
          emit the legacy ``**[TIMESTAMP] [AD BREAK]** - Sponsor`` marker.
        """
        lines: List[str] = []
        current_speaker: Optional[str] = None
        current_text: List[str] = []
        block_start_time: float = 0.0
        last_timecode: float = 0.0

        def flush_block() -> None:
            nonlocal current_text
            if current_text:
                merged = " ".join(current_text)
                timecode = _format_timecode_inline(block_start_time)
                # Render the speaker label as-is — no fallback on None.
                # The legacy ``TranscriptFormatter`` uses
                # ``segment.get("speaker", "SPEAKER_UNKNOWN")`` which only
                # triggers the default when the key is missing, never when
                # the value is ``None``. Pydantic ``model_dump`` always
                # includes the key, so a ``speaker=None`` segment renders
                # as the literal string ``**None:**`` in both paths. Byte
                # parity requires us to reproduce that exactly.
                lines.append(f"{timecode} **{current_speaker}:** {merged}")
                lines.append("")
                current_text = []

        for segment in self.segments:
            if segment.kind == "filler":
                continue

            if segment.kind == "ad_break":
                flush_block()
                ad_time = segment.start + self.playback_time_offset_seconds
                timecode = _format_timecode_inline(ad_time)
                sponsor_suffix = f" - {segment.sponsor}" if segment.sponsor else ""
                lines.append(f"**{timecode} [AD BREAK]**{sponsor_suffix}")
                lines.append("")
                current_speaker = None
                continue

            speaker = segment.speaker
            normalised = _normalise_text(segment.text)
            start_time = segment.start + self.playback_time_offset_seconds

            should_add_timecode = (
                current_speaker != speaker or (start_time - last_timecode) >= timecode_interval_seconds
            )

            if should_add_timecode and current_text:
                flush_block()

            # Anchor the start of any fresh block to the current segment's
            # real start. The legacy ``TranscriptFormatter`` left
            # ``block_start_time`` at its 0.0 initial value until a flush,
            # which meant the first-ever block always stamped at
            # ``[00:00]`` regardless of where speech actually began. That
            # quirk was invisible on real transcripts (first segment
            # normally starts at t=0), but becomes reachable here when a
            # leading filler or ad_break pushes the first content segment
            # forward. Anchoring on every fresh block fixes both cases
            # and keeps byte-identical parity for ``from_raw`` whenever
            # the raw transcript's first segment starts at 0.0 (the
            # universal case in practice).
            if not current_text:
                block_start_time = start_time
                last_timecode = start_time

            current_speaker = speaker
            current_text.append(normalised)

        flush_block()
        return "\n".join(lines)


def _normalise_text(text: str) -> str:
    """Format-only text cleanup matching ``TranscriptFormatter._normalise_text``.

    Duplicated deliberately to avoid a ``models → core`` layer violation.
    The byte-identical round-trip test in ``tests/unit/models/
    test_annotated_transcript.py`` guards against drift between the two
    copies. If a future refactor extracts a shared helper, it is safe to
    swap both call sites at once.

    Note: the legacy formatter's smart-quote replacement is a no-op
    (``"`` → ``"``, ``'`` → ``'``) — presumably someone intended to replace
    curly quotes but the source file ended up with straight ones. We
    reproduce the no-op by not touching smart quotes at all, which keeps
    byte-identical parity on transcripts that do contain curly quotes.
    """
    if not text:
        return ""
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,\.!?;:])", r"\1", text)
    text = re.sub(r"\.{2,}", "...", text)
    return text


def _format_timecode_inline(seconds: float) -> str:
    """Format a timestamp as ``[MM:SS]`` or ``[HH:MM:SS]``.

    Matches ``TranscriptFormatter._format_timecode_inline`` exactly. See
    the note on ``_normalise_text`` for why the duplication exists.
    """
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"[{hours:02d}:{minutes:02d}:{secs:02d}]"
    return f"[{minutes:02d}:{secs:02d}]"
