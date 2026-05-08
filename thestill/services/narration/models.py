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
from typing import List, Literal, Optional

ScriptBlockKind = Literal["narration", "quote"]
SpeakerRole = Literal["host", "guest", "unknown"]


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
    """

    blocks: List[ScriptBlock]
    quotes: List[QuoteCandidate]
    stats: NarrationStats
    episode_ids_covered: List[str]
    episode_ids_in_tail: List[str]
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    json_script_path: Optional[Path] = None
    markdown_path: Optional[Path] = None
