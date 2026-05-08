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

"""Data models for narrated-digest generation (spec #33)."""

import dataclasses
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Literal, Optional, Tuple

ScriptBlockKind = Literal["narration", "quote"]
SpeakerRole = Literal["host", "guest", "unknown"]
NarrationMode = Literal["narrated", "fallback"]


def word_count(text: str) -> int:
    """Whitespace-delimited word count used for WPM-based duration math.

    Defined here so the selector and the renderer agree on the
    duration-vs-word conversion that anchors the time-budget model.
    """
    return len([w for w in re.split(r"\s+", text.strip()) if w])


@dataclass(frozen=True)
class QuoteCandidate:
    """Verbatim quote pulled from a cleaned-transcript JSON sidecar.

    The text is copied directly from ``AnnotatedSegment.text``; the
    pipeline never paraphrases or regenerates quote content (spec #33
    §"Quote integrity"). ``start_seconds`` + ``duration_seconds`` is the
    durable address used by future TTS work to splice in original audio.
    """

    quote_id: str
    episode_id: str
    podcast_title: str
    speaker: str
    speaker_role: SpeakerRole
    text: str
    start_seconds: float
    duration_seconds: float
    score: float = 0.0

    def with_id(self, new_id: str) -> "QuoteCandidate":
        return dataclasses.replace(self, quote_id=new_id)


@dataclass
class ScriptBlock:
    """One ordered block in the narration script.

    Phase 1 narration blocks are static chrome (opener, per-episode
    intro, signoff). Phase 2 replaces them with anchor-voiced prose
    emitted by the script-generation LLM call, with ``<<QUOTE qN>>``
    placeholders threaded between sentences.
    """

    kind: ScriptBlockKind
    section: str
    text: Optional[str] = None
    quote_id: Optional[str] = None
    duration_seconds: float = 0.0


@dataclass
class NarrationStats:
    """Run statistics surfaced to logs and the JSON-script header."""

    target_duration_seconds: int
    actual_duration_seconds: float
    narration_words: int
    quote_seconds: float
    episodes_covered: int
    episodes_in_tail: int
    quote_count: int
    fallback_reason: Optional[str] = None


@dataclass
class NarrationContent:
    """Generated narration: the ordered script + the resolved quote pool.

    ``episode_ids_covered`` lists episodes that contributed at least one
    quote; ``episode_ids_in_tail`` lists episodes routed to the rapid-fire
    tail because no quote could be resolved (no JSON sidecar, no
    speaker mapping, every turn filtered, etc).

    ``mode`` distinguishes a successful narration (``"narrated"``) from
    a link-index fallback (``"fallback"``). The fallback path still
    emits ``markdown`` (the link-index digest) and ``stats`` so the
    surface treats both shapes uniformly; ``stats.fallback_reason``
    explains why narration was abandoned.
    """

    blocks: List[ScriptBlock]
    quotes: List[QuoteCandidate]
    stats: NarrationStats
    episode_ids_covered: List[str]
    episode_ids_in_tail: List[str]
    mode: NarrationMode = "narrated"
    markdown: Optional[str] = None
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    json_script_path: Optional[Path] = None
    markdown_path: Optional[Path] = None


@dataclass(frozen=True)
class EpisodeBrief:
    """Per-episode context fed to the theme clusterer (spec §"Pipeline Stages 1").

    Compact on purpose: only the fields the clusterer needs to spot a
    cross-show theme. Heavier inputs (full summaries) are reserved for
    the script-generation call where they are necessary.
    """

    episode_id: str
    podcast_title: str
    episode_title: str
    guests: Tuple[str, ...] = ()
    topics: Tuple[str, ...] = ()
    sponsors: Tuple[str, ...] = ()
    gist: Optional[str] = None


@dataclass(frozen=True)
class Segment:
    """One theme-grouped segment in the narration plan.

    Produced by the theme-clustering LLM call. ``rank=1`` is the lead
    story. ``angle`` is the concrete framing the anchor will speak to
    ("disagreement on X" / "what changed about Y"), not just the topic.
    """

    theme: str
    angle: str
    episode_ids: Tuple[str, ...]
    rank: int


@dataclass(frozen=True)
class ThemePlan:
    """Output of theme clustering (spec §"Pipeline Stages 1").

    ``segments`` are the lead stories in priority order. ``tail_ids``
    are episodes that did not fold into any segment and will be swept
    into the rapid-fire tail.
    """

    segments: Tuple[Segment, ...]
    tail_ids: Tuple[str, ...]


@dataclass(frozen=True)
class ValidationFailure:
    """A single validation finding from the script-generation contract.

    ``reason`` is a stable token suitable for metrics
    (``"unknown_quote_id"``, ``"verbatim_leak"``, ``"word_budget_high"``,
    ``"word_budget_low"``, ``"empty_blocks"``). ``detail`` is a
    human-readable message included in the regenerate prompt and the
    fallback log.
    """

    reason: str
    detail: str
