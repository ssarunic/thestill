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

"""Deterministic pre-segmentation for segmented transcript cleanup.

``TranscriptSegmenter`` is the diarisation-repair phase of spec #18. It
consumes a raw ``Transcript`` and emits an ``AnnotatedTranscript`` that
still maps back to the raw segment/word stream but has been cleaned up in
two ways:

1. **Pause-guarded same-speaker merge.** Consecutive segments with the
   same speaker label are merged into one super-segment when the
   inter-segment silence is below ``pause_ceiling_seconds`` (default 3.0).
   Silence above the ceiling signals a likely topic shift and preserves
   the boundary.

2. **Word-stream paragraph re-chunk.** Super-segments whose word count
   exceeds ``max_words_per_segment`` are split at natural boundaries
   inside the word stream — preferring inter-word silences above
   ``paragraph_gap_seconds`` (default 0.5), then sentence-ending
   punctuation, and falling back to a hard cut at the target size.

No LLM, no network. Deterministic for a given input and parameter set.

The segmenter refuses transcripts that cannot feed segmented cleanup
(``has_usable_segment_structure`` returns False). The routing decision
lives one layer up in ``TranscriptCleaningProcessor``; by the time
``repair()`` is called the processor must already have decided the
transcript is structurally usable. A degenerate input here is a bug, not
a run-time condition, and ``repair()`` raises ``DegenerateTranscriptError``
to make it loud.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from thestill.models.annotated_transcript import AnnotatedSegment, AnnotatedTranscript, WordSpan
from thestill.models.transcript import Segment, Transcript, Word
from thestill.utils.transcript_capabilities import classify_transcript_degeneracy, has_usable_segment_structure


class DegenerateTranscriptError(ValueError):
    """Raised when the segmenter is handed a transcript it cannot process.

    Carries the ``DegeneracyReason`` string so callers can log or render
    it. The processor's routing layer should have caught this before
    dispatching to the segmenter; reaching this exception means a routing
    bug, not a runtime input error.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"transcript cannot feed segmented cleanup: reason={reason}")
        self.reason = reason


@dataclass
class _WordRef:
    """One word, flattened with its raw-JSON coordinates.

    Used as the unit during paragraph re-chunking: a merged super-segment
    is represented as a list of ``_WordRef`` and split points are found
    inside that list rather than on the original segment boundaries.
    """

    raw_segment_id: int
    word_index: int
    word: str
    start: Optional[float]
    end: Optional[float]


class TranscriptSegmenter:
    """Deterministic diarisation repair and paragraph re-chunking."""

    def __init__(
        self,
        *,
        pause_ceiling_seconds: float = 3.0,
        max_words_per_segment: int = 80,
        paragraph_gap_seconds: float = 0.5,
    ) -> None:
        """
        Args:
            pause_ceiling_seconds: Maximum inter-segment silence within
                which two same-speaker raw segments are merged. Above this
                the boundary is preserved even when the speaker matches.
                Tuning is Open Question #6 in spec #18.
            max_words_per_segment: Target upper bound for word count per
                cleaned segment. Super-segments longer than this get split
                at the best available paragraph/sentence boundary. Must
                be at least 1.
            paragraph_gap_seconds: Inter-word silence above this threshold
                is treated as a strong paragraph boundary during splitting.

        Raises:
            ValueError: when any argument is outside its valid range.
        """
        if max_words_per_segment < 1:
            raise ValueError(f"max_words_per_segment must be >= 1, got {max_words_per_segment}")
        if pause_ceiling_seconds < 0:
            raise ValueError(f"pause_ceiling_seconds must be >= 0, got {pause_ceiling_seconds}")
        if paragraph_gap_seconds < 0:
            raise ValueError(f"paragraph_gap_seconds must be >= 0, got {paragraph_gap_seconds}")

        self.pause_ceiling_seconds = pause_ceiling_seconds
        self.max_words_per_segment = max_words_per_segment
        self.paragraph_gap_seconds = paragraph_gap_seconds

    def repair(
        self,
        transcript: Transcript,
        *,
        episode_id: str = "",
    ) -> AnnotatedTranscript:
        """Return a segment-repaired ``AnnotatedTranscript``.

        Raises ``DegenerateTranscriptError`` when the input lacks the
        per-segment timing or word timestamps needed for segmented
        cleanup. That is a caller-contract violation — the routing layer
        above must filter these cases first.
        """
        if not has_usable_segment_structure(transcript):
            reason = classify_transcript_degeneracy(transcript) or "unknown"
            raise DegenerateTranscriptError(reason)

        merged_runs = self._merge_same_speaker_runs(transcript.segments)
        annotated: List[AnnotatedSegment] = []
        for run in merged_runs:
            annotated.extend(self._split_run(run))

        # Assign positional ids in final order.
        for index, segment in enumerate(annotated):
            segment.id = index

        return AnnotatedTranscript(episode_id=episode_id, segments=annotated)

    def _merge_same_speaker_runs(
        self,
        segments: List[Segment],
    ) -> List[List[Segment]]:
        """Group raw segments into same-speaker runs under the pause ceiling.

        Breaks a run when (a) the speaker changes or (b) the inter-segment
        silence ``next.start - prev.end`` reaches ``pause_ceiling_seconds``.
        Returns a list of runs, each a list of contiguous raw segments.
        """
        runs: List[List[Segment]] = []
        current: List[Segment] = []
        for segment in segments:
            if not current:
                current = [segment]
                continue

            previous = current[-1]
            gap = segment.start - previous.end
            same_speaker = segment.speaker == previous.speaker
            within_pause = gap < self.pause_ceiling_seconds

            if same_speaker and within_pause:
                current.append(segment)
            else:
                runs.append(current)
                current = [segment]

        if current:
            runs.append(current)
        return runs

    def _split_run(self, run: List[Segment]) -> List[AnnotatedSegment]:
        """Split a same-speaker run into one or more ``AnnotatedSegment``s.

        Short runs become a single segment. Long runs are split at natural
        boundaries in the word stream. A run with no words (shouldn't
        happen when the capability check passed, but possible for a single
        malformed raw segment inside an otherwise-healthy transcript)
        passes through as one segment using its raw-segment text.
        """
        speaker = run[0].speaker
        words = _flatten_words(run)

        if not words:
            return [self._annotate_from_segment_only(run, speaker)]

        # For fallback bounds we look up raw segment start/end by id. A
        # chunk's fallback narrows to exactly the segments that
        # contributed words *to that chunk* rather than the whole run,
        # so an untimed chunk can't inflate its anchor to span the
        # entire run.
        segment_by_id: Dict[int, Segment] = {s.id: s for s in run}

        word_count = len(words)
        if word_count <= self.max_words_per_segment:
            return [self._annotate_from_words(words, speaker, segment_by_id)]

        split_points = self._find_split_points(words)
        chunks = _slice_words_at(words, split_points)
        return [self._annotate_from_words(chunk, speaker, segment_by_id) for chunk in chunks]

    def _find_split_points(self, words: List[_WordRef]) -> List[int]:
        """Return zero-based indices at which to cut ``words`` into chunks.

        Algorithm, in priority order:

        1. Find all **paragraph gaps**: inter-word silences ``>=
           paragraph_gap_seconds``. Keep the gaps whose resulting chunk
           sizes stay under ``max_words_per_segment``.
        2. If no paragraph gaps are usable, fall back to **sentence
           boundaries** (word ending with ``.``, ``!``, or ``?``).
        3. If neither produces short-enough chunks, force a **hard cut**
           every ``max_words_per_segment`` words.

        The return list is indices at which a *new* chunk starts (so the
        first chunk is ``words[0:split_points[0]]``, the second is
        ``words[split_points[0]:split_points[1]]``, etc.). The list is
        always non-empty — a one-chunk run returns ``[]``.
        """
        paragraph_candidates = _paragraph_gap_indices(words, self.paragraph_gap_seconds)
        balanced = _select_balanced_splits(paragraph_candidates, len(words), self.max_words_per_segment)
        if balanced:
            return balanced

        sentence_candidates = _sentence_boundary_indices(words)
        balanced = _select_balanced_splits(sentence_candidates, len(words), self.max_words_per_segment)
        if balanced:
            return balanced

        return _hard_cuts(len(words), self.max_words_per_segment)

    def _annotate_from_words(
        self,
        words: List[_WordRef],
        speaker: Optional[str],
        segment_by_id: Dict[int, Segment],
    ) -> AnnotatedSegment:
        """Build an ``AnnotatedSegment`` for a contiguous slice of word refs.

        Chunk bounds prefer word-level timing, walk inward when edge
        words lack timestamps, and fall back to the bounds of the raw
        segments that actually *contributed words to this chunk* as a
        last resort. Narrowing the fallback like this — rather than
        using run-wide bounds — prevents a split chunk with no usable
        edge timing from inheriting the entire run's time range, which
        would inflate the segment anchor and break the player's seek
        contract.
        """
        first = words[0]
        last = words[-1]

        # Walk inward from each edge until a word with a usable timestamp
        # is found. This keeps chunk bounds tight when the middle of the
        # chunk has good timing and only the ends don't.
        start_candidate = next((w.start for w in words if w.start is not None), None)
        end_candidate = next((w.end for w in reversed(words) if w.end is not None), None)

        source_ids: List[int] = []
        seen = set()
        for w in words:
            if w.raw_segment_id not in seen:
                seen.add(w.raw_segment_id)
                source_ids.append(w.raw_segment_id)

        contributing_segs = [segment_by_id[sid] for sid in source_ids if sid in segment_by_id]
        fallback_start = min(s.start for s in contributing_segs) if contributing_segs else 0.0
        fallback_end = max(s.end for s in contributing_segs) if contributing_segs else fallback_start

        start = start_candidate if start_candidate is not None else fallback_start
        end = end_candidate if end_candidate is not None else fallback_end
        text = " ".join(w.word for w in words).strip()

        return AnnotatedSegment(
            id=0,  # positional id assigned later in repair()
            start=start,
            end=end,
            speaker=speaker,
            text=text,
            kind="content",
            source_segment_ids=source_ids,
            source_word_span=WordSpan(
                start_segment_id=first.raw_segment_id,
                start_word_index=first.word_index,
                end_segment_id=last.raw_segment_id,
                end_word_index=last.word_index,
            ),
        )

    def _annotate_from_segment_only(
        self,
        run: List[Segment],
        speaker: Optional[str],
    ) -> AnnotatedSegment:
        """Fallback: build a segment for a run that carries no word refs.

        Happens when a raw segment has an empty ``words`` list (provider
        oddity on specific segments even when the transcript as a whole
        carries word timestamps). We use segment-level ``start``/``end``
        and the original text, and we do not emit a ``source_word_span``.
        """
        start = run[0].start
        end = run[-1].end
        text = " ".join(seg.text.strip() for seg in run if seg.text).strip()
        return AnnotatedSegment(
            id=0,  # positional id assigned later in repair()
            start=start,
            end=end,
            speaker=speaker,
            text=text,
            kind="content",
            source_segment_ids=[seg.id for seg in run],
            source_word_span=None,
        )


# ---------------------------------------------------------------------------
# Module-level helpers. Kept outside the class so they are easy to unit-test
# in isolation and so the class body stays focused on orchestration.
# ---------------------------------------------------------------------------


def _flatten_words(run: List[Segment]) -> List[_WordRef]:
    """Flatten a list of raw segments into a positional word stream."""
    out: List[_WordRef] = []
    for segment in run:
        for word_index, word in enumerate(segment.words):
            out.append(
                _WordRef(
                    raw_segment_id=segment.id,
                    word_index=word_index,
                    word=word.word,
                    start=word.start,
                    end=word.end,
                )
            )
    return out


def _paragraph_gap_indices(words: List[_WordRef], threshold: float) -> List[int]:
    """Return indices of words whose preceding inter-word gap >= threshold.

    An index ``i`` in the result means "start a new chunk at ``words[i]``".
    Index 0 is never a split point (it's the run's start).
    """
    gaps: List[Tuple[int, float]] = []
    for i in range(1, len(words)):
        prev = words[i - 1]
        curr = words[i]
        if prev.end is None or curr.start is None:
            continue
        gap = curr.start - prev.end
        if gap >= threshold:
            gaps.append((i, gap))
    # Sort by gap descending so that _select_balanced_splits prefers the
    # longest pauses when it greedily picks from candidates.
    gaps.sort(key=lambda item: item[1], reverse=True)
    return [i for i, _ in gaps]


def _sentence_boundary_indices(words: List[_WordRef]) -> List[int]:
    """Return indices of words that follow a sentence-ending word.

    Sentence-ending = a word whose text ends with ``.``, ``!``, or ``?``.
    The returned indices are candidate positions to START a new chunk.
    """
    candidates: List[int] = []
    for i in range(1, len(words)):
        prev_word_text = words[i - 1].word.rstrip()
        if prev_word_text.endswith((".", "!", "?")):
            candidates.append(i)
    return candidates


def _select_balanced_splits(
    candidates: List[int],
    total: int,
    max_chunk: int,
) -> List[int]:
    """Greedy: pick the earliest candidate that keeps each chunk <= max.

    Walks the candidates in order and picks the one that comes latest
    without exceeding ``max_chunk`` from the previous split point. This
    produces chunks that are each near the target size rather than wildly
    uneven ones. Returns an empty list if no candidate set satisfies the
    constraint (i.e. every chunk would still be too large).
    """
    if not candidates:
        return []

    sorted_candidates = sorted(set(candidates))
    chosen: List[int] = []
    last_start = 0
    i = 0
    n = len(sorted_candidates)

    while last_start + max_chunk < total:
        # Find the latest candidate within [last_start + 1, last_start + max_chunk].
        best: Optional[int] = None
        while i < n and sorted_candidates[i] <= last_start + max_chunk:
            if sorted_candidates[i] > last_start:
                best = sorted_candidates[i]
            i += 1
        if best is None:
            # No candidate within range — this candidate set cannot keep
            # chunks under the target size. Signal failure to the caller
            # so it can fall back to the next strategy.
            return []
        chosen.append(best)
        last_start = best

    return chosen


def _hard_cuts(total: int, max_chunk: int) -> List[int]:
    """Return hard-cut split points every ``max_chunk`` words.

    Used as the final fallback when neither paragraph gaps nor sentence
    boundaries produced a usable split. Splits are exact: ``[max_chunk,
    2 * max_chunk, ...]`` up to but not including ``total``.
    """
    return list(range(max_chunk, total, max_chunk))


def _slice_words_at(words: List[_WordRef], split_points: List[int]) -> List[List[_WordRef]]:
    """Carve ``words`` into contiguous chunks at ``split_points``.

    ``split_points`` is a list of start-of-next-chunk indices, as returned
    by the helpers above. An empty list means "one chunk, the whole list".
    """
    if not split_points:
        return [words]

    chunks: List[List[_WordRef]] = []
    last = 0
    for point in split_points:
        chunks.append(words[last:point])
        last = point
    chunks.append(words[last:])
    return [chunk for chunk in chunks if chunk]
