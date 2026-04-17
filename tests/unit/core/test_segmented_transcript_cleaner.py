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

"""Tests for :mod:`thestill.core.segmented_transcript_cleaner` (spec #18 Phase C).

The cleaner talks to an :class:`LLMProvider`; every test in this module
substitutes a fake provider that returns canned patches. Running against
a real LLM happens in integration tests, not here.
"""

import json
from typing import Any, Dict, List, Optional, Type

import pytest
from pydantic import BaseModel

from tests.conftest import MockLLMProvider
from thestill.core.segmented_transcript_cleaner import (
    CleanupPatch,
    CleanupPatchBatch,
    SegmentedTranscriptCleaner,
    _apply_speaker_mapping,
)
from thestill.models.annotated_transcript import AnnotatedSegment, AnnotatedTranscript, WordSpan
from thestill.models.facts import EpisodeFacts, PodcastFacts


class FakeProvider(MockLLMProvider):
    """Thin extension of ``MockLLMProvider`` with scripted patch returns.

    Captures every ``generate_structured_cached`` call so tests can
    assert on the cache hint, the message payload, and the target ids.
    ``patch_factory`` is the one attribute each test overrides to script
    the LLM's per-batch behaviour.
    """

    def __init__(self, *, model_name: str = "gpt-4o", supports_caching: bool = True) -> None:
        super().__init__(model_name=model_name)
        self._supports_caching = supports_caching
        self.calls: List[Dict[str, Any]] = []
        # Tests override this to script the LLM's behaviour per-batch.
        self.patch_factory = lambda target_ids: [  # noqa: E731
            CleanupPatch(id=i, cleaned_text=f"id={i}") for i in target_ids
        ]

    def supports_prompt_caching(self) -> bool:
        return self._supports_caching

    def generate_structured_cached(
        self,
        messages: List[Dict[str, str]],
        response_model: Type[BaseModel],
        *,
        cache_system_message: bool = False,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> BaseModel:
        user_content = next(m["content"] for m in messages if m["role"] == "user")
        payload = json.loads(user_content)
        target_ids = [seg["id"] for seg in payload["target"]]
        patches = self.patch_factory(target_ids)

        self.calls.append(
            {
                "messages": messages,
                "cache_system_message": cache_system_message,
                "temperature": temperature,
                "target_ids": target_ids,
                "payload": payload,
            }
        )

        return response_model(patches=patches)


def _segment(
    *,
    seg_id: int,
    start: float = 0.0,
    end: float = 1.0,
    speaker: Optional[str] = "A",
    text: str = "hello",
    kind: str = "content",
    source_ids: Optional[List[int]] = None,
    word_span: Optional[WordSpan] = None,
) -> AnnotatedSegment:
    return AnnotatedSegment(
        id=seg_id,
        start=start,
        end=end,
        speaker=speaker,
        text=text,
        kind=kind,  # type: ignore[arg-type]
        source_segment_ids=source_ids if source_ids is not None else [seg_id],
        source_word_span=word_span,
    )


def _annotated(segments: List[AnnotatedSegment]) -> AnnotatedTranscript:
    return AnnotatedTranscript(episode_id="ep1", segments=segments)


def _facts() -> EpisodeFacts:
    return EpisodeFacts(episode_title="test episode", speaker_mapping={"A": "Alice"})


class TestCacheHintAndCallShape:
    """The cleaner propagates the cache hint and builds the expected prompt shape."""

    def test_cache_system_message_is_true_on_every_call(self) -> None:
        provider = FakeProvider()
        cleaner = SegmentedTranscriptCleaner(provider)

        cleaner.clean(
            _annotated([_segment(seg_id=0), _segment(seg_id=1)]),
            podcast_facts=None,
            episode_facts=_facts(),
            language="en",
        )

        assert provider.calls, "expected at least one LLM call"
        for call in provider.calls:
            assert call["cache_system_message"] is True

    def test_system_prompt_is_identical_across_calls(self) -> None:
        """Cacheability depends on the system prefix being byte-identical batch to batch."""
        provider = FakeProvider()
        # Force many small batches: 50-char text per segment, budget 50 → 1/batch.
        cleaner = SegmentedTranscriptCleaner(provider, batch_char_budget=50)

        segments = [_segment(seg_id=i, text="x" * 50) for i in range(5)]
        cleaner.clean(_annotated(segments), podcast_facts=None, episode_facts=_facts(), language="en")

        assert len(provider.calls) == 5
        system_prompts = {
            next(m["content"] for m in call["messages"] if m["role"] == "system") for call in provider.calls
        }
        assert len(system_prompts) == 1, "system prompt must be byte-identical across calls for caching"

    def test_user_prompt_has_three_bucket_shape(self) -> None:
        provider = FakeProvider()
        cleaner = SegmentedTranscriptCleaner(provider, batch_char_budget=50)

        cleaner.clean(
            _annotated([_segment(seg_id=i, text="x" * 50) for i in range(3)]),
            podcast_facts=None,
            episode_facts=_facts(),
            language="en",
        )

        payload = provider.calls[0]["payload"]
        assert set(payload.keys()) == {"previous_cleaned", "target", "next_raw"}
        # First batch has empty previous_cleaned and one segment of next_raw.
        assert payload["previous_cleaned"] == []
        assert len(payload["target"]) == 1
        assert len(payload["next_raw"]) >= 1


class TestPatchApplication:
    """Patches mutate only the allowed fields and the source anchors survive."""

    def test_patches_apply_to_correct_segment_ids(self) -> None:
        provider = FakeProvider()
        provider.patch_factory = lambda ids: [
            CleanupPatch(id=ids[0], cleaned_text="cleaned-zero"),
            CleanupPatch(id=ids[1], cleaned_text="cleaned-one"),
        ]
        cleaner = SegmentedTranscriptCleaner(provider)

        result = cleaner.clean(
            _annotated([_segment(seg_id=0, text="raw0"), _segment(seg_id=1, text="raw1")]),
            podcast_facts=None,
            episode_facts=_facts(),
            language="en",
        )

        assert result.segments[0].text == "cleaned-zero"
        assert result.segments[1].text == "cleaned-one"

    def test_filler_patch_produces_empty_text_and_filler_kind(self) -> None:
        provider = FakeProvider()
        provider.patch_factory = lambda ids: [
            CleanupPatch(id=ids[0], cleaned_text="", kind="filler"),
        ]
        cleaner = SegmentedTranscriptCleaner(provider)

        result = cleaner.clean(
            _annotated([_segment(seg_id=0, text="um")]),
            podcast_facts=None,
            episode_facts=_facts(),
            language="en",
        )

        assert result.segments[0].kind == "filler"
        assert result.segments[0].text == ""

    def test_ad_break_patch_carries_sponsor(self) -> None:
        provider = FakeProvider()
        provider.patch_factory = lambda ids: [
            CleanupPatch(id=ids[0], cleaned_text="sponsor content", kind="ad_break", sponsor="Acme"),
        ]
        cleaner = SegmentedTranscriptCleaner(provider)

        result = cleaner.clean(
            _annotated([_segment(seg_id=0, text="support for the show comes from acme")]),
            podcast_facts=None,
            episode_facts=_facts(),
            language="en",
        )

        assert result.segments[0].kind == "ad_break"
        assert result.segments[0].sponsor == "Acme"

    def test_source_segment_ids_and_word_span_are_preserved(self) -> None:
        """The patch invariant: LLM patches must not touch source anchors.

        Enforced by construction via the ``CleanupPatch`` schema — those
        fields aren't declared on the model, so Pydantic validation drops
        any attempt to set them before the patch reaches our apply step.
        This test verifies behaviourally that the anchors survive.
        """
        provider = FakeProvider()
        provider.patch_factory = lambda ids: [
            CleanupPatch(id=ids[0], cleaned_text="cleaned"),
        ]
        cleaner = SegmentedTranscriptCleaner(provider)

        word_span = WordSpan(start_segment_id=42, start_word_index=0, end_segment_id=42, end_word_index=3)
        original = _segment(seg_id=0, text="original", source_ids=[42, 43, 44], word_span=word_span)

        result = cleaner.clean(
            _annotated([original]),
            podcast_facts=None,
            episode_facts=_facts(),
            language="en",
        )

        assert result.segments[0].source_segment_ids == [42, 43, 44]
        assert result.segments[0].source_word_span == word_span

    def test_segments_without_patches_pass_through_unchanged(self) -> None:
        """Missing patches fall back to the original segment, not a crash."""
        provider = FakeProvider()
        provider.patch_factory = lambda ids: []  # LLM returns no patches at all
        cleaner = SegmentedTranscriptCleaner(provider)

        result = cleaner.clean(
            _annotated([_segment(seg_id=0, text="unchanged")]),
            podcast_facts=None,
            episode_facts=_facts(),
            language="en",
        )

        assert result.segments[0].text == "unchanged"
        assert result.segments[0].kind == "content"

    def test_stray_patches_referencing_context_ids_are_ignored(self) -> None:
        """LLM patches for ids outside the target range do no harm."""
        provider = FakeProvider()
        provider.patch_factory = lambda ids: [
            CleanupPatch(id=ids[0], cleaned_text="on target"),
            CleanupPatch(id=999, cleaned_text="stray"),
        ]
        cleaner = SegmentedTranscriptCleaner(provider)

        result = cleaner.clean(
            _annotated([_segment(seg_id=0)]),
            podcast_facts=None,
            episode_facts=_facts(),
            language="en",
        )

        assert len(result.segments) == 1
        assert result.segments[0].text == "on target"


class TestBatchSizing:
    """Batch size adapts to the provider's caching capability."""

    def test_caching_provider_uses_declared_budget(self) -> None:
        provider = FakeProvider(supports_caching=True)
        cleaner = SegmentedTranscriptCleaner(provider, batch_char_budget=100)

        assert cleaner._effective_batch_char_budget == 100

    def test_non_caching_provider_widens_budget(self) -> None:
        """Providers without caching get 3x budget to amortise the repeated prefix."""
        provider = FakeProvider(supports_caching=False)
        cleaner = SegmentedTranscriptCleaner(provider, batch_char_budget=100)

        assert cleaner._effective_batch_char_budget == 300

    def test_always_makes_progress_even_when_segment_exceeds_budget(self) -> None:
        """A segment longer than the budget still gets cleaned one at a time."""
        provider = FakeProvider()
        cleaner = SegmentedTranscriptCleaner(provider, batch_char_budget=10)

        segments = [_segment(seg_id=i, text="x" * 50) for i in range(3)]
        cleaner.clean(_annotated(segments), podcast_facts=None, episode_facts=_facts(), language="en")

        assert len(provider.calls) == 3


class TestConstructorValidation:
    """Constructor rejects invalid parameters up front."""

    def test_negative_k_prev_raises(self) -> None:
        with pytest.raises(ValueError, match="k_prev"):
            SegmentedTranscriptCleaner(FakeProvider(), k_prev=-1)

    def test_negative_k_next_raises(self) -> None:
        with pytest.raises(ValueError, match="k_next"):
            SegmentedTranscriptCleaner(FakeProvider(), k_next=-1)

    def test_zero_batch_char_budget_raises(self) -> None:
        with pytest.raises(ValueError, match="batch_char_budget"):
            SegmentedTranscriptCleaner(FakeProvider(), batch_char_budget=0)


class TestBlendedMarkdownRenderContract:
    """Running the cleaner and rendering must produce legacy-compatible output."""

    def test_render_contains_legacy_speaker_format(self) -> None:
        """Format: ``[MM:SS] **Speaker:** text`` — what the summariser reads."""
        provider = FakeProvider()
        provider.patch_factory = lambda ids: [CleanupPatch(id=0, cleaned_text="hello alice")]
        cleaner = SegmentedTranscriptCleaner(provider)

        result = cleaner.clean(
            _annotated([_segment(seg_id=0, text="raw", speaker="Alice")]),
            podcast_facts=None,
            episode_facts=_facts(),
            language="en",
        )
        rendered = result.to_blended_markdown()

        assert "[00:00] **Alice:** hello alice" in rendered

    def test_filler_segment_is_dropped_from_rendered_output(self) -> None:
        provider = FakeProvider()
        provider.patch_factory = lambda ids: [
            CleanupPatch(id=ids[0], cleaned_text="before"),
            CleanupPatch(id=ids[1], cleaned_text="", kind="filler"),
            CleanupPatch(id=ids[2], cleaned_text="after"),
        ]
        cleaner = SegmentedTranscriptCleaner(provider)

        result = cleaner.clean(
            _annotated(
                [
                    _segment(seg_id=0, text="raw0"),
                    _segment(seg_id=1, text="um"),
                    _segment(seg_id=2, text="raw2"),
                ]
            ),
            podcast_facts=None,
            episode_facts=_facts(),
            language="en",
        )
        rendered = result.to_blended_markdown()

        # Filler text never appears; neighbouring content merges into one block.
        assert "um" not in rendered
        assert "before after" in rendered

    def test_ad_break_renders_with_legacy_marker(self) -> None:
        provider = FakeProvider()
        provider.patch_factory = lambda ids: [
            CleanupPatch(id=ids[0], cleaned_text="promo content", kind="ad_break", sponsor="DX"),
        ]
        cleaner = SegmentedTranscriptCleaner(provider)

        result = cleaner.clean(
            _annotated([_segment(seg_id=0, start=30.0, end=60.0, text="promo content", speaker=None)]),
            podcast_facts=None,
            episode_facts=_facts(),
            language="en",
        )
        rendered = result.to_blended_markdown()

        assert "**[00:30] [AD BREAK]** - DX" in rendered


class TestSpeakerMappingHelper:
    """``_apply_speaker_mapping`` — deterministic substitution of generic
    ``SPEAKER_NN`` labels with real names from ``EpisodeFacts``."""

    def _mk(self, seg_id: int, speaker: Optional[str], text: str = "x") -> AnnotatedSegment:
        return AnnotatedSegment(id=seg_id, start=0.0, end=1.0, speaker=speaker, text=text)

    def test_substitutes_known_speaker_ids(self) -> None:
        segments = [self._mk(0, "SPEAKER_01"), self._mk(1, "SPEAKER_02")]
        mapping = {"SPEAKER_01": "Lenny Rachitsky", "SPEAKER_02": "Claire Vo"}

        out = _apply_speaker_mapping(segments, mapping)

        assert out[0].speaker == "Lenny Rachitsky"
        assert out[1].speaker == "Claire Vo"

    def test_strips_trailing_role_annotations(self) -> None:
        """``"Scott Galloway (Host)"`` renders as ``"Scott Galloway"`` —
        matches the legacy cleaner's stripping behaviour exactly."""
        segments = [self._mk(0, "SPEAKER_00")]
        mapping = {"SPEAKER_00": "Scott Galloway (Host)"}

        out = _apply_speaker_mapping(segments, mapping)

        assert out[0].speaker == "Scott Galloway"

    def test_strips_only_the_last_parenthesised_block(self) -> None:
        """Ensures ``rsplit`` semantics — keep inner parens, strip the
        trailing role annotation only."""
        segments = [self._mk(0, "X")]
        mapping = {"X": "A (B) (Host)"}

        out = _apply_speaker_mapping(segments, mapping)

        assert out[0].speaker == "A (B)"

    def test_leaves_name_alone_when_no_role_annotation(self) -> None:
        segments = [self._mk(0, "X")]
        mapping = {"X": "Plain Name"}

        out = _apply_speaker_mapping(segments, mapping)

        assert out[0].speaker == "Plain Name"

    def test_unmapped_speakers_pass_through_unchanged(self) -> None:
        """A ``SPEAKER_NN`` label that survives to output is a visible
        signal that facts extraction missed that speaker — we do NOT
        silently rename it to something generic like ``"Speaker 3"``."""
        segments = [self._mk(0, "SPEAKER_99")]
        mapping = {"SPEAKER_01": "Lenny"}

        out = _apply_speaker_mapping(segments, mapping)

        assert out[0].speaker == "SPEAKER_99"

    def test_none_speakers_pass_through_unchanged(self) -> None:
        """``speaker=None`` (e.g. on ad_break segments) is legal and
        preserved — we only rewrite known non-``None`` ids."""
        segments = [self._mk(0, None)]
        mapping = {"SPEAKER_01": "Lenny"}

        out = _apply_speaker_mapping(segments, mapping)

        assert out[0].speaker is None

    def test_empty_mapping_is_a_noop(self) -> None:
        segments = [self._mk(0, "SPEAKER_01"), self._mk(1, "SPEAKER_02")]

        out = _apply_speaker_mapping(segments, {})

        assert out[0].speaker == "SPEAKER_01"
        assert out[1].speaker == "SPEAKER_02"

    def test_empty_name_in_mapping_is_skipped(self) -> None:
        """An explicitly-empty mapping value (e.g. user cleared the name
        in the facts file) leaves the speaker id unchanged, just like
        the legacy stage's ``if not speaker_name: continue`` guard."""
        segments = [self._mk(0, "SPEAKER_01"), self._mk(1, "SPEAKER_02")]
        mapping = {"SPEAKER_01": "", "SPEAKER_02": "Claire"}

        out = _apply_speaker_mapping(segments, mapping)

        assert out[0].speaker == "SPEAKER_01"
        assert out[1].speaker == "Claire"

    def test_preserves_all_other_fields(self) -> None:
        segments = [
            AnnotatedSegment(
                id=0,
                start=1.2,
                end=3.4,
                speaker="SPEAKER_01",
                text="content",
                kind="content",
                source_segment_ids=[42, 43],
                source_word_span=WordSpan(start_segment_id=42, start_word_index=0, end_segment_id=43, end_word_index=5),
            )
        ]

        out = _apply_speaker_mapping(segments, {"SPEAKER_01": "Lenny"})

        assert out[0].speaker == "Lenny"
        # Everything else survives untouched — the invariant the
        # patch-apply path also enforces.
        assert out[0].id == 0
        assert out[0].start == 1.2
        assert out[0].end == 3.4
        assert out[0].text == "content"
        assert out[0].source_segment_ids == [42, 43]
        assert out[0].source_word_span is not None
        assert out[0].source_word_span.start_segment_id == 42


class TestSpeakerMappingIntegration:
    """``clean()`` applies the mapping up-front so the LLM sees real
    names in the per-batch payload and the output carries them too."""

    def test_clean_returns_real_names_when_mapping_provided(self) -> None:
        provider = FakeProvider()
        cleaner = SegmentedTranscriptCleaner(provider)

        result = cleaner.clean(
            _annotated(
                [
                    _segment(seg_id=0, speaker="SPEAKER_01", text="raw0"),
                    _segment(seg_id=1, speaker="SPEAKER_02", text="raw1"),
                ]
            ),
            podcast_facts=None,
            episode_facts=EpisodeFacts(
                episode_title="t",
                speaker_mapping={
                    "SPEAKER_01": "Lenny Rachitsky (Host)",
                    "SPEAKER_02": "Claire Vo (Guest)",
                },
            ),
            language="en",
        )

        assert result.segments[0].speaker == "Lenny Rachitsky"
        assert result.segments[1].speaker == "Claire Vo"

    def test_clean_exposes_real_names_to_llm_prompt(self) -> None:
        """The per-batch JSON payload the LLM sees should already have
        real names in every segment's ``speaker`` field — that lets
        the LLM use the speaker identity for entity repair (e.g.
        distinguishing mis-transcribed name mentions in the text)."""
        provider = FakeProvider()
        cleaner = SegmentedTranscriptCleaner(provider)

        cleaner.clean(
            _annotated([_segment(seg_id=0, speaker="SPEAKER_01", text="raw")]),
            podcast_facts=None,
            episode_facts=EpisodeFacts(
                episode_title="t",
                speaker_mapping={"SPEAKER_01": "Lenny Rachitsky (Host)"},
            ),
            language="en",
        )

        payload = provider.calls[0]["payload"]
        assert payload["target"][0]["speaker"] == "Lenny Rachitsky"
        assert "SPEAKER_01" not in payload["target"][0]["speaker"]
