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

"""Narrated digest generation (spec #33).

Phase 1 ships the deterministic backbone: parse cleaned-transcript JSON
sidecars into resolved-speaker turns, score and pick verbatim quotes
under a per-episode cap, and emit a skeleton JSON script ready for
Phase 2's anchor-prose LLM call to fill in narration blocks around the
quote cues.
"""

from .models import (
    NarrationContent,
    NarrationStats,
    QuoteCandidate,
    ScriptBlock,
    ScriptBlockKind,
)
from .narration_generator import NarrationConfig, NarrationGenerator
from .quote_selector import QuoteSelector, QuoteSelectorConfig
from .transcript_loader import ResolvedTurn, TranscriptTurnLoader

__all__ = [
    "NarrationConfig",
    "NarrationContent",
    "NarrationGenerator",
    "NarrationStats",
    "QuoteCandidate",
    "QuoteSelector",
    "QuoteSelectorConfig",
    "ResolvedTurn",
    "ScriptBlock",
    "ScriptBlockKind",
    "TranscriptTurnLoader",
]
