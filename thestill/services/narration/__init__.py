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

"""Narrated digest generation (spec #33)."""

from .artefacts import read_narration_header
from .markdown_renderer import NarrationMarkdownRenderer
from .models import (
    EpisodeBrief,
    NarrationContent,
    NarrationMode,
    NarrationStats,
    QuoteCandidate,
    ScriptBlock,
    ScriptBlockKind,
    Segment,
    SpeakerRole,
    ThemePlan,
    ValidationFailure,
    word_count,
)
from .narration_generator import NarrationConfig, NarrationGenerator
from .narration_runner import NarrationRun, NarrationRunner, NarrationRunnerError
from .quote_selector import QuoteSelector, QuoteSelectorConfig
from .script_writer import ScriptResult, ScriptWriter
from .theme_clusterer import ThemeClusterer
from .transcript_loader import ResolvedTurn, TranscriptTurnLoader

__all__ = [
    "EpisodeBrief",
    "NarrationConfig",
    "NarrationContent",
    "NarrationGenerator",
    "NarrationMarkdownRenderer",
    "read_narration_header",
    "NarrationMode",
    "NarrationRun",
    "NarrationRunner",
    "NarrationRunnerError",
    "NarrationStats",
    "QuoteCandidate",
    "QuoteSelector",
    "QuoteSelectorConfig",
    "ResolvedTurn",
    "ScriptBlock",
    "ScriptBlockKind",
    "ScriptResult",
    "ScriptWriter",
    "Segment",
    "SpeakerRole",
    "ThemeClusterer",
    "ThemePlan",
    "TranscriptTurnLoader",
    "ValidationFailure",
    "word_count",
]
