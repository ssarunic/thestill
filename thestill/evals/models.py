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

"""Pydantic models for eval runs: manifest schema and judge report schemas.

The manifest is the provenance record for a run — judge, rubric version +
prompt hash, artifact content hashes, per-item status. The report models
are the FM-7 validation boundary for judge output: sanitized text ->
``json.loads`` -> report model. An invalid report is never persisted as a
success.
"""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

MANIFEST_SCHEMA_VERSION = 1
ITEM_REPORT_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


class ArtifactRef(BaseModel):
    """One judged input file: its repo-relative path and content hash.

    The hash is the honest identity of what was judged — episode paths stay
    stable while the bytes behind them change across pipeline re-runs.
    """

    path: str
    sha256: str


class JudgeInfo(BaseModel):
    """The judge configuration a run was executed with."""

    provider: str
    model: str
    temperature: Optional[float] = None
    # False = judge fell back to the pipeline LLM (no EVAL_JUDGE_* pin and
    # no --judge-* flag). Such runs are allowed but visibly second-class.
    pinned: bool
    samples: int = 1


class RubricInfo(BaseModel):
    """Which rubric (and exactly which prompt text) produced the reports."""

    name: str
    version: str
    prompt_sha256: str


class ManifestItem(BaseModel):
    """Per-episode outcome inside a run."""

    podcast_slug: str
    episode_slug: str
    external_id: Optional[str] = None
    artifacts: Dict[str, ArtifactRef] = Field(default_factory=dict)
    status: Literal["ok", "failed"]
    error: Optional[str] = None
    report_file: Optional[str] = None
    # Mean score per rubric dimension (mean over --samples judgements;
    # with samples=1 this is just the single score). Kept in the manifest
    # so list/show/compare never have to open N item files.
    scores: Optional[Dict[str, float]] = None
    # Sample standard deviation per dimension; only present when samples > 1.
    scores_std: Optional[Dict[str, float]] = None
    # Result of the rubric's deterministic checks (summary rubric only);
    # None when the rubric has no checks. Details live in the item report.
    checks_ok: Optional[bool] = None
    # FM-4: degraded evidence is labelled, never silent.
    transcript_truncated: bool = False
    duration_s: Optional[float] = None


class RunManifest(BaseModel):
    """The provenance record of one eval run — written once, never edited."""

    schema_version: int = MANIFEST_SCHEMA_VERSION
    run_id: str
    label: Optional[str] = None
    note: Optional[str] = None
    created_at: str  # UTC ISO-8601 with +00:00 offset (FM-3)
    git_commit: Optional[str] = None
    rubric: RubricInfo
    judge: JudgeInfo
    items: List[ManifestItem] = Field(default_factory=list)
    counts: Dict[str, int] = Field(default_factory=lambda: {"ok": 0, "failed": 0})


class DimensionStats(BaseModel):
    """Aggregate stats for one score dimension across a run's ok items."""

    mean: float
    median: float
    min: float
    max: float
    n: int


class RunSummary(BaseModel):
    """Aggregate stats written as ``summary.json`` after all items finish."""

    run_id: str
    dimensions: Dict[str, DimensionStats] = Field(default_factory=dict)
    counts: Dict[str, int] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Judge report models (FM-7 validation boundary)
#
# ``extra="ignore"`` on validation: the judge may add fields, and the raw
# parsed dict (not the model dump) is what gets persisted in the item
# report — validation guarantees required shape without discarding extra
# evidence the judge volunteered.
# ---------------------------------------------------------------------------


class CountedExamples(BaseModel):
    model_config = ConfigDict(extra="ignore")

    count: int = Field(ge=0)
    examples: List[str] = Field(default_factory=list)


class ReportSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    strengths: List[str] = Field(default_factory=list)
    weaknesses: List[str] = Field(default_factory=list)
    verdict: str


def _score_field() -> Field:  # type: ignore[valid-type]
    return Field(ge=0, le=10)


class _RawAccuracy(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name_errors: CountedExamples
    entity_errors: CountedExamples
    word_errors: CountedExamples
    faithfulness_issues: CountedExamples


class _RawCompleteness(BaseModel):
    model_config = ConfigDict(extra="ignore")

    missing_sections: CountedExamples


class _RawStructure(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ads_detected: bool
    intro_detected: bool
    outro_detected: bool
    speaker_turns_marked: bool
    timestamps_present: bool


class _RawScores(BaseModel):
    model_config = ConfigDict(extra="ignore")

    accuracy: float = _score_field()
    completeness: float = _score_field()
    entity_handling: float = _score_field()
    structural_clarity: float = _score_field()


class RawTranscriptReport(BaseModel):
    """Judge report for the ``raw-transcript`` rubric."""

    model_config = ConfigDict(extra="ignore")

    accuracy: _RawAccuracy
    completeness: _RawCompleteness
    structure: _RawStructure
    scores: _RawScores
    summary: ReportSummary


class _CleanFidelity(BaseModel):
    model_config = ConfigDict(extra="ignore")

    meaning_preserved: bool
    invented_content: CountedExamples
    name_entity_corrections: CountedExamples


class _CleanFormatting(BaseModel):
    model_config = ConfigDict(extra="ignore")

    speaker_labels_clear: bool
    ads_marked: bool
    intro_marked: bool
    outro_marked: bool
    headings_present: bool
    timestamps_consistent: bool


class _CleanEnhancements(BaseModel):
    model_config = ConfigDict(extra="ignore")

    notable_quotes: CountedExamples
    social_snippets: CountedExamples
    markdown_used: bool


class _CleanScores(BaseModel):
    model_config = ConfigDict(extra="ignore")

    fidelity: float = _score_field()
    formatting_clarity: float = _score_field()
    readability: float = _score_field()
    enhancements_value: float = _score_field()


class CleanTranscriptReport(BaseModel):
    """Judge report for the ``clean-transcript`` rubric."""

    model_config = ConfigDict(extra="ignore")

    fidelity: _CleanFidelity
    formatting: _CleanFormatting
    enhancements: _CleanEnhancements
    scores: _CleanScores
    summary: ReportSummary


class _SummaryCoverage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    missing_topics: CountedExamples


class _SummaryFaithfulness(BaseModel):
    model_config = ConfigDict(extra="ignore")

    invented_claims: CountedExamples
    distorted_claims: CountedExamples


class _SummaryAttribution(BaseModel):
    model_config = ConfigDict(extra="ignore")

    misattributions: CountedExamples


class _SummaryScores(BaseModel):
    model_config = ConfigDict(extra="ignore")

    coverage: float = _score_field()
    faithfulness: float = _score_field()
    attribution: float = _score_field()
    insight_value: float = _score_field()


class SummaryReport(BaseModel):
    """Judge report for the ``summary`` rubric."""

    model_config = ConfigDict(extra="ignore")

    coverage: _SummaryCoverage
    faithfulness: _SummaryFaithfulness
    attribution: _SummaryAttribution
    scores: _SummaryScores
    summary: ReportSummary
