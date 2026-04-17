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

"""Capability checks for transcripts.

A transcript feeds the new segmented-cleanup path only when it carries real
per-segment timing and at least one word-level timestamp. Providers like
Parakeet currently emit a single stub ``(id=0, start=0, end=0)`` segment
with no word timestamps; those transcripts must fall back to the legacy
blended-Markdown cleanup path.

This module owns the single predicate consulted from every routing site
(observability hook in Phase A, routing switch in Phase C, frontend debug
badges via the API). Having one helper avoids the three-way drift the
earlier plan suffered from.
"""

from typing import Literal, Optional

from thestill.models.transcript import Transcript

DegeneracyReason = Literal[
    "no_segments",
    "zero_length_segments",
    "missing_word_timestamps",
]


def classify_transcript_degeneracy(
    transcript: Transcript,
) -> Optional[DegeneracyReason]:
    """Return the reason a transcript cannot feed segmented cleanup.

    Returns ``None`` when the transcript is usable. Otherwise returns a
    stable string key identifying why — used as the ``reason`` field of the
    ``segmented_cleanup_unavailable`` structured log event.
    """
    if not transcript.segments:
        return "no_segments"
    if not any(s.end > s.start for s in transcript.segments):
        return "zero_length_segments"
    if not any(w.start is not None for s in transcript.segments for w in s.words):
        return "missing_word_timestamps"
    return None


def has_usable_segment_structure(transcript: Transcript) -> bool:
    """Return True iff segmented cleanup can run on this transcript.

    Requires (a) at least one segment whose ``end > start`` (rules out stub
    zero-length segments) and (b) at least one word with a non-``None``
    ``start`` (rules out providers that omit word timestamps). A legitimate
    single-segment transcript with real timestamps passes; a Parakeet stub
    with ``(id=0, start=0, end=0)`` and no words fails.
    """
    return classify_transcript_degeneracy(transcript) is None
