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

"""Routing matrix tests for :class:`TranscriptCleaningProcessor` (spec #18 Phase C).

These tests verify the flag-driven routing that selects between the
legacy blended cleanup and the segmented cleanup, plus the optional
shadow pipeline. The actual cleaning work is stubbed — this suite is
about which pipeline ran, what got written to disk, and how degenerate
transcripts force the legacy fallback regardless of flags.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

import pytest
from pydantic import BaseModel

from tests.conftest import MockLLMProvider
from thestill.core import transcript_cleaner as transcript_cleaner_module
from thestill.core.transcript_cleaning_processor import (
    TranscriptCleaningProcessor,
    _resolve_primary_pipeline,
    _resolve_shadow_pipeline,
    _write_shadow_output,
)
from thestill.models.annotated_transcript import AnnotatedSegment, AnnotatedTranscript, WordSpan
from thestill.models.facts import EpisodeFacts, PodcastFacts
from thestill.utils.path_manager import PathManager


class DummyProvider(MockLLMProvider):
    """LLMProvider stub that raises on any call — both Pass-2 cleaners and
    facts extraction are monkeypatched in the fixtures below, so any real
    invocation here indicates an un-stubbed path and should fail loudly.
    """

    def chat_completion(self, messages, temperature=None, max_tokens=None, response_format=None):  # type: ignore[override]
        raise AssertionError("chat_completion should have been stubbed")

    def generate_structured(self, messages, response_model, temperature=None, max_tokens=None):  # type: ignore[override]
        raise AssertionError("generate_structured should have been stubbed")


# ---------------------------------------------------------------------------
# Module-level helper tests — the flag parsers and shadow writer are
# individually testable so failures localise sharply.
# ---------------------------------------------------------------------------


class TestResolvePrimaryPipeline:
    """``_resolve_primary_pipeline`` consults flag + force-fallback in order."""

    def test_default_is_segmented_when_flag_unset(self) -> None:
        assert _resolve_primary_pipeline(env_value=None, force_legacy=False) == "segmented"

    def test_legacy_flag_selects_legacy(self) -> None:
        assert _resolve_primary_pipeline(env_value="legacy", force_legacy=False) == "legacy"

    def test_segmented_flag_selects_segmented(self) -> None:
        assert _resolve_primary_pipeline(env_value="segmented", force_legacy=False) == "segmented"

    def test_force_legacy_wins_over_segmented_flag(self) -> None:
        assert _resolve_primary_pipeline(env_value="segmented", force_legacy=True) == "legacy"

    def test_unknown_value_defaults_to_segmented_and_warns(self) -> None:
        assert _resolve_primary_pipeline(env_value="bogus", force_legacy=False) == "segmented"

    def test_whitespace_and_case_insensitive(self) -> None:
        assert _resolve_primary_pipeline(env_value="  LEGACY  ", force_legacy=False) == "legacy"


class TestResolveShadowPipeline:
    """``_resolve_shadow_pipeline`` returns the opposite-pipeline name or None."""

    @pytest.mark.parametrize("flag_value", ["1", "true", "yes", "on", "y", "t"])
    def test_truthy_with_segmented_primary_returns_legacy(self, flag_value: str) -> None:
        assert _resolve_shadow_pipeline(env_value=flag_value, primary="segmented", force_disable=False) == "legacy"

    @pytest.mark.parametrize("flag_value", ["1", "true", "yes", "on", "y", "t"])
    def test_truthy_with_legacy_primary_returns_segmented(self, flag_value: str) -> None:
        assert _resolve_shadow_pipeline(env_value=flag_value, primary="legacy", force_disable=False) == "segmented"

    @pytest.mark.parametrize("flag_value", ["0", "false", "no", "off", "n", "f"])
    def test_falsy_values_return_none(self, flag_value: str) -> None:
        assert _resolve_shadow_pipeline(env_value=flag_value, primary="segmented", force_disable=False) is None

    def test_unset_defaults_to_opposite(self) -> None:
        """Phase C ships shadow-on by default; Phase F flips to off."""
        assert _resolve_shadow_pipeline(env_value=None, primary="segmented", force_disable=False) == "legacy"

    def test_force_disable_wins(self) -> None:
        assert _resolve_shadow_pipeline(env_value="true", primary="segmented", force_disable=True) is None


class TestWriteShadowOutput:
    """``_write_shadow_output`` names the file after the shadow pipeline."""

    def test_writes_legacy_shadow_next_to_primary(self, tmp_path: Path) -> None:
        primary = tmp_path / "slug" / "ep_hash_cleaned.md"
        primary.parent.mkdir(parents=True)
        primary.write_text("primary")

        shadow_path = _write_shadow_output(
            primary_output_path=str(primary), pipeline="legacy", content="shadow content"
        )

        written = Path(shadow_path)
        assert written.exists()
        assert written.name == "ep_hash_cleaned.shadow_legacy.md"
        assert written.parent.name == "debug"
        assert written.read_text() == "shadow content"

    def test_writes_segmented_shadow_next_to_primary(self, tmp_path: Path) -> None:
        primary = tmp_path / "slug" / "ep_hash_cleaned.md"
        primary.parent.mkdir(parents=True)
        primary.write_text("primary")

        shadow_path = _write_shadow_output(
            primary_output_path=str(primary), pipeline="segmented", content="shadow content"
        )

        assert Path(shadow_path).name == "ep_hash_cleaned.shadow_segmented.md"


# ---------------------------------------------------------------------------
# End-to-end routing matrix — exercises TranscriptCleaningProcessor with
# the real flag parsing but stubs the LLM-dependent pieces so the tests
# are fast and deterministic.
# ---------------------------------------------------------------------------


def _raw_transcript_dict(*, degenerate: bool = False) -> Dict[str, Any]:
    """Build a minimal raw-transcript dict suitable for ``Transcript.model_validate``.

    Passing ``degenerate=True`` produces a Parakeet-style stub that fails
    the capability check.
    """
    if degenerate:
        return {
            "audio_file": "x.wav",
            "language": "en",
            "text": "whole thing",
            "segments": [{"id": 0, "start": 0.0, "end": 0.0, "text": "whole thing", "speaker": None, "words": []}],
            "processing_time": 0.0,
            "model_used": "parakeet",
            "timestamp": 0.0,
        }
    return {
        "audio_file": "x.wav",
        "language": "en",
        "text": "hello there",
        "segments": [
            {
                "id": 0,
                "start": 0.0,
                "end": 1.5,
                "text": "hello there",
                "speaker": "A",
                "words": [
                    {"word": "hello", "start": 0.0, "end": 0.5},
                    {"word": "there", "start": 0.6, "end": 1.5},
                ],
            }
        ],
        "processing_time": 0.0,
        "model_used": "fixture",
        "timestamp": 0.0,
    }


@pytest.fixture
def stubbed_processor(monkeypatch: pytest.MonkeyPatch) -> TranscriptCleaningProcessor:
    """Build a processor whose LLM-touching pieces are all stubbed.

    Replaces the legacy and segmented Pass-2 entry points with stubs
    that return identifiable strings / transcripts so the tests can see
    which pipeline ran. Also short-circuits the FactsManager so Pass 1
    never tries to call the LLM.
    """
    provider = DummyProvider()
    processor = TranscriptCleaningProcessor(provider)

    # Pass 1: bypass by pre-populating facts. FactsManager.load_* returns
    # pre-built facts → Pass 1 is skipped entirely.
    from thestill.core import facts_manager as facts_manager_module

    monkeypatch.setattr(
        facts_manager_module.FactsManager,
        "load_podcast_facts",
        lambda self, slug: PodcastFacts(podcast_title="Test Pod"),
    )
    monkeypatch.setattr(
        facts_manager_module.FactsManager,
        "load_episode_facts",
        lambda self, podcast_slug, episode_slug: EpisodeFacts(
            episode_title="Test Episode", speaker_mapping={"A": "Alice"}
        ),
    )

    # Legacy Pass 2 stub — return an identifiable string.
    monkeypatch.setattr(
        transcript_cleaner_module.TranscriptCleaner,
        "clean_transcript",
        lambda self, **kwargs: "LEGACY_OUTPUT_MARKER",
    )

    # Segmented Pass 2 stub — return a recognisable AnnotatedTranscript.
    from thestill.core import segmented_transcript_cleaner as seg_cleaner_module

    def _fake_segmented_clean(self, annotated, podcast_facts, episode_facts, *, language):  # noqa: ANN001
        # Pass through the raw segments but stamp the text so the render
        # carries a marker the test can assert on.
        marked_segments = [
            AnnotatedSegment(
                id=seg.id,
                start=seg.start,
                end=seg.end,
                speaker=seg.speaker,
                text="SEGMENTED_OUTPUT_MARKER",
                kind="content",
                source_segment_ids=seg.source_segment_ids,
                source_word_span=seg.source_word_span,
            )
            for seg in annotated.segments
        ]
        return AnnotatedTranscript(
            episode_id=annotated.episode_id,
            segments=marked_segments,
            playback_time_offset_seconds=annotated.playback_time_offset_seconds,
            algorithm_version=annotated.algorithm_version,
        )

    monkeypatch.setattr(
        seg_cleaner_module.SegmentedTranscriptCleaner,
        "clean",
        _fake_segmented_clean,
    )

    return processor


def _run_clean(
    processor: TranscriptCleaningProcessor,
    tmp_path: Path,
    *,
    degenerate: bool = False,
) -> Dict[str, Any]:
    """Helper: invoke ``clean_transcript`` with minimal plumbing."""
    podcast_slug = "test-pod"
    output_path = tmp_path / "clean_transcripts" / podcast_slug / "ep_hash_cleaned.md"
    return processor.clean_transcript(
        transcript_data=_raw_transcript_dict(degenerate=degenerate),
        podcast_title="Test Pod",
        episode_title="Test Episode",
        podcast_slug=podcast_slug,
        episode_slug="test-episode",
        output_path=str(output_path),
        path_manager=PathManager(storage_path=str(tmp_path)),
        save_prompts=False,
        language="en",
    )


class TestRoutingMatrix:
    """The four (pipeline × shadow) combinations plus the degenerate override."""

    def test_segmented_primary_with_legacy_shadow(
        self,
        stubbed_processor: TranscriptCleaningProcessor,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("THESTILL_CLEANUP_PIPELINE", "segmented")
        monkeypatch.setenv("THESTILL_LEGACY_CLEANUP_SHADOW", "1")

        result = _run_clean(stubbed_processor, tmp_path)

        assert result["primary_pipeline"] == "segmented"
        assert result["shadow_pipeline"] == "legacy"
        assert "SEGMENTED_OUTPUT_MARKER" in result["cleaned_markdown"]
        assert result["cleaned_json_path"] is not None
        assert Path(result["cleaned_json_path"]).exists()
        assert result["shadow_path"] is not None
        assert Path(result["shadow_path"]).read_text() == "LEGACY_OUTPUT_MARKER"
        assert "shadow_legacy.md" in result["shadow_path"]

    def test_legacy_primary_with_segmented_shadow(
        self,
        stubbed_processor: TranscriptCleaningProcessor,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("THESTILL_CLEANUP_PIPELINE", "legacy")
        monkeypatch.setenv("THESTILL_LEGACY_CLEANUP_SHADOW", "1")

        result = _run_clean(stubbed_processor, tmp_path)

        assert result["primary_pipeline"] == "legacy"
        assert result["shadow_pipeline"] == "segmented"
        assert result["cleaned_markdown"] == "LEGACY_OUTPUT_MARKER"
        # Legacy primary → no JSON sidecar (that's segmented-specific).
        assert result["cleaned_json_path"] is None
        assert result["shadow_path"] is not None
        assert "SEGMENTED_OUTPUT_MARKER" in Path(result["shadow_path"]).read_text()
        assert "shadow_segmented.md" in result["shadow_path"]

    def test_segmented_primary_shadow_off(
        self,
        stubbed_processor: TranscriptCleaningProcessor,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("THESTILL_CLEANUP_PIPELINE", "segmented")
        monkeypatch.setenv("THESTILL_LEGACY_CLEANUP_SHADOW", "0")

        result = _run_clean(stubbed_processor, tmp_path)

        assert result["primary_pipeline"] == "segmented"
        assert result["shadow_pipeline"] is None
        assert result["shadow_path"] is None
        assert result["cleaned_json_path"] is not None
        # No shadow files created.
        debug_dir = tmp_path / "clean_transcripts" / "test-pod" / "debug"
        if debug_dir.exists():
            assert not any(f.name.startswith("ep_hash_cleaned.shadow_") for f in debug_dir.iterdir())

    def test_legacy_primary_shadow_off(
        self,
        stubbed_processor: TranscriptCleaningProcessor,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("THESTILL_CLEANUP_PIPELINE", "legacy")
        monkeypatch.setenv("THESTILL_LEGACY_CLEANUP_SHADOW", "0")

        result = _run_clean(stubbed_processor, tmp_path)

        assert result["primary_pipeline"] == "legacy"
        assert result["shadow_pipeline"] is None
        assert result["cleaned_markdown"] == "LEGACY_OUTPUT_MARKER"
        assert result["cleaned_json_path"] is None
        assert result["shadow_path"] is None


class TestDegenerateInputForceRoutesToLegacy:
    """Degenerate transcripts always take the legacy path, regardless of flags."""

    def test_parakeet_stub_with_segmented_flag_still_legacy(
        self,
        stubbed_processor: TranscriptCleaningProcessor,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("THESTILL_CLEANUP_PIPELINE", "segmented")
        monkeypatch.setenv("THESTILL_LEGACY_CLEANUP_SHADOW", "1")

        result = _run_clean(stubbed_processor, tmp_path, degenerate=True)

        assert result["primary_pipeline"] == "legacy"
        assert result["cleaned_markdown"] == "LEGACY_OUTPUT_MARKER"
        assert result["cleaned_json_path"] is None
        assert result["shadow_pipeline"] is None, "shadow must not run on degenerate input"
        assert result["shadow_path"] is None

    def test_parakeet_stub_with_legacy_flag_is_still_legacy(
        self,
        stubbed_processor: TranscriptCleaningProcessor,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("THESTILL_CLEANUP_PIPELINE", "legacy")
        monkeypatch.setenv("THESTILL_LEGACY_CLEANUP_SHADOW", "1")

        result = _run_clean(stubbed_processor, tmp_path, degenerate=True)

        assert result["primary_pipeline"] == "legacy"
        # Even with shadow enabled, a degenerate input suppresses it —
        # running the segmented cleaner would raise DegenerateTranscriptError.
        assert result["shadow_pipeline"] is None
        assert result["shadow_path"] is None


class TestJsonSidecarContents:
    """The segmented-primary path writes a parseable AnnotatedTranscript JSON."""

    def test_json_sidecar_is_valid_annotated_transcript(
        self,
        stubbed_processor: TranscriptCleaningProcessor,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("THESTILL_CLEANUP_PIPELINE", "segmented")
        monkeypatch.setenv("THESTILL_LEGACY_CLEANUP_SHADOW", "0")

        result = _run_clean(stubbed_processor, tmp_path)

        json_path = Path(result["cleaned_json_path"])
        data = json.loads(json_path.read_text())
        # Round-trip through the Pydantic model to catch any drift.
        parsed = AnnotatedTranscript.model_validate(data)
        assert parsed.episode_id == "ep1" or parsed.episode_id == ""  # segmenter may stamp either
        assert len(parsed.segments) >= 1
        assert parsed.segments[0].text == "SEGMENTED_OUTPUT_MARKER"
        assert parsed.segments[0].source_segment_ids == [0]
