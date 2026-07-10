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

"""Versioned rubric registry for eval runs.

Each rubric pins the exact system prompt sent to the judge. The prompt's
sha256 is recorded in every run manifest; a unit test pins the hash per
(name, version) so editing a prompt without bumping its version fails CI
instead of silently poisoning comparability across runs.

The ``raw-transcript`` and ``clean-transcript`` v1 prompts are moved
verbatim from the retired ``core/evaluator.py`` — reports produced by the
runner for the unchanged prompts remain comparable with historical intent.
"""

import hashlib
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Tuple, Type

from pydantic import BaseModel

from .models import CleanTranscriptReport, RawTranscriptReport, SummaryReport
from .summary_checks import run_summary_checks

# Artifact kinds. A rubric's ``inputs`` must all be present for an episode
# to be eligible; ``optional_inputs`` are included (and hashed) when
# available but do not gate eligibility.
RAW_TRANSCRIPT = "raw_transcript"
CLEAN_TRANSCRIPT = "clean_transcript"
SUMMARY = "summary"


@dataclass(frozen=True)
class Rubric:
    """One versioned LLM-as-judge rubric the runner can execute."""

    name: str
    version: str
    system_prompt: str
    # Keys expected under the judge report's ``scores`` object.
    dimensions: Tuple[str, ...]
    inputs: Tuple[str, ...]
    optional_inputs: Tuple[str, ...] = ()
    report_model: Type[BaseModel] = BaseModel
    # Builds the user message from loaded artifact texts (keyed by kind).
    render_user_message: Callable[[Dict[str, str]], str] = field(default=lambda artifacts: "")
    # Cheap exact checks computed in Python, not delegated to the judge
    # (summary rubric only). Receives (artifact_texts, duration_seconds);
    # returns a JSON-serializable dict with a top-level ``ok`` bool.
    deterministic_checks: Optional[Callable[[Dict[str, str], Optional[int]], dict]] = None

    @property
    def prompt_sha256(self) -> str:
        return hashlib.sha256(self.system_prompt.encode("utf-8")).hexdigest()


_RAW_TRANSCRIPT_PROMPT_V1 = """You are an evaluator of raw transcripts produced by automatic speech recognition (ASR) systems.
Analyse the transcript and return a structured JSON report following the schema below.

Schema:
{
  "accuracy": {
    "name_errors": {"count": "integer", "examples": ["string"]},
    "entity_errors": {"count": "integer", "examples": ["string"]},
    "word_errors": {"count": "integer", "examples": ["string"]},
    "faithfulness_issues": {"count": "integer", "examples": ["string"]}
  },
  "completeness": {
    "missing_sections": {"count": "integer", "examples": ["string"]}
  },
  "structure": {
    "ads_detected": "boolean",
    "intro_detected": "boolean",
    "outro_detected": "boolean",
    "speaker_turns_marked": "boolean",
    "timestamps_present": "boolean"
  },
  "scores": {
    "accuracy": "integer (0-10)",
    "completeness": "integer (0-10)",
    "entity_handling": "integer (0-10)",
    "structural_clarity": "integer (0-10)"
  },
  "summary": {
    "strengths": ["string"],
    "weaknesses": ["string"],
    "verdict": "string"
  }
}

Return ONLY valid JSON following this exact schema. Do not include any explanatory text before or after the JSON."""


_CLEAN_TRANSCRIPT_PROMPT_V1 = """You are an evaluator of processed transcripts enhanced by an LLM.
Analyse the transcript and return a structured JSON report following the schema below.

Schema:
{
  "fidelity": {
    "meaning_preserved": "boolean",
    "invented_content": {"count": "integer", "examples": ["string"]},
    "name_entity_corrections": {"count": "integer", "examples": ["string"]}
  },
  "formatting": {
    "speaker_labels_clear": "boolean",
    "ads_marked": "boolean",
    "intro_marked": "boolean",
    "outro_marked": "boolean",
    "headings_present": "boolean",
    "timestamps_consistent": "boolean"
  },
  "enhancements": {
    "notable_quotes": {"count": "integer", "examples": ["string"]},
    "social_snippets": {"count": "integer", "examples": ["string"]},
    "markdown_used": "boolean"
  },
  "scores": {
    "fidelity": "integer (0-10)",
    "formatting_clarity": "integer (0-10)",
    "readability": "integer (0-10)",
    "enhancements_value": "integer (0-10)"
  },
  "summary": {
    "strengths": ["string"],
    "weaknesses": ["string"],
    "verdict": "string"
  }
}

Return ONLY valid JSON following this exact schema. Do not include any explanatory text before or after the JSON."""


_SUMMARY_PROMPT_V1 = """You are an evaluator of podcast episode summaries. You are given the summary and the cleaned transcript it was produced from. The transcript is the ground truth: judge the summary strictly against it, not against your own knowledge.

Analyse the summary and return a structured JSON report following the schema below.

Definitions:
- coverage: are the transcript's major topics and conclusions represented in the summary? List substantial topics the summary missed.
- faithfulness: does the summary state anything the transcript does not support? invented_claims are claims with no basis in the transcript; distorted_claims are claims present in the transcript but materially misrepresented (wrong number, inverted conclusion, lost caveat).
- attribution: are claims and quotes credited to the correct speaker?
- insight_value: does the summary surface the non-obvious (key takeaways, tensions, surprises) rather than merely paraphrasing chronology?

Schema:
{
  "coverage": {
    "missing_topics": {"count": "integer", "examples": ["string"]}
  },
  "faithfulness": {
    "invented_claims": {"count": "integer", "examples": ["string"]},
    "distorted_claims": {"count": "integer", "examples": ["string"]}
  },
  "attribution": {
    "misattributions": {"count": "integer", "examples": ["string"]}
  },
  "scores": {
    "coverage": "integer (0-10)",
    "faithfulness": "integer (0-10)",
    "attribution": "integer (0-10)",
    "insight_value": "integer (0-10)"
  },
  "summary": {
    "strengths": ["string"],
    "weaknesses": ["string"],
    "verdict": "string"
  }
}

Return ONLY valid JSON following this exact schema. Do not include any explanatory text before or after the JSON."""


def _render_raw_transcript_message(artifacts: Dict[str, str]) -> str:
    return f"Evaluate this transcript:\n\n{artifacts[RAW_TRANSCRIPT]}"


def _render_clean_transcript_message(artifacts: Dict[str, str]) -> str:
    message = f"Evaluate this processed transcript:\n\n{artifacts[CLEAN_TRANSCRIPT]}"
    original = artifacts.get(RAW_TRANSCRIPT)
    if original:
        message += f"\n\nOriginal transcript for comparison:\n\n{original}"
    return message


def _render_summary_message(artifacts: Dict[str, str]) -> str:
    return (
        f"Evaluate this episode summary:\n\n{artifacts[SUMMARY]}"
        f"\n\nCleaned transcript (ground truth):\n\n{artifacts[CLEAN_TRANSCRIPT]}"
    )


RUBRICS: Dict[str, Rubric] = {
    "raw-transcript": Rubric(
        name="raw-transcript",
        version="1",
        system_prompt=_RAW_TRANSCRIPT_PROMPT_V1,
        dimensions=("accuracy", "completeness", "entity_handling", "structural_clarity"),
        inputs=(RAW_TRANSCRIPT,),
        report_model=RawTranscriptReport,
        render_user_message=_render_raw_transcript_message,
    ),
    "clean-transcript": Rubric(
        name="clean-transcript",
        version="1",
        system_prompt=_CLEAN_TRANSCRIPT_PROMPT_V1,
        dimensions=("fidelity", "formatting_clarity", "readability", "enhancements_value"),
        inputs=(CLEAN_TRANSCRIPT,),
        optional_inputs=(RAW_TRANSCRIPT,),
        report_model=CleanTranscriptReport,
        render_user_message=_render_clean_transcript_message,
    ),
    "summary": Rubric(
        name="summary",
        version="1",
        system_prompt=_SUMMARY_PROMPT_V1,
        dimensions=("coverage", "faithfulness", "attribution", "insight_value"),
        inputs=(SUMMARY, CLEAN_TRANSCRIPT),
        report_model=SummaryReport,
        render_user_message=_render_summary_message,
        deterministic_checks=run_summary_checks,
    ),
}


def get_rubric(name: str) -> Rubric:
    """Look up a rubric by name, raising a helpful error for unknown names."""
    try:
        return RUBRICS[name]
    except KeyError:
        known = ", ".join(sorted(RUBRICS))
        raise ValueError(f"unknown rubric {name!r}; available: {known}") from None
