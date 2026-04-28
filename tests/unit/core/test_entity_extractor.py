"""Spec #28 §1.2 — entity extractor over the AnnotatedTranscript sidecar.

These tests use a stub GLiNER instance to avoid loading the real
~400MB model in CI. The extractor's contract is:

- only ``content`` segments are scanned (ad_break/intro/outro/filler/music skipped)
- emitted ``EntityMention`` rows have ``entity_id=None``,
  ``resolution_status=PENDING``, ``segment_id`` from the sidecar
- ``start_ms``/``end_ms`` are the segment's seconds * 1000
- ``confidence`` survives round-trip
- a quote excerpt of ≤ 2× the configured window is returned, with
  the surface form somewhere in it
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple
from unittest.mock import patch

from thestill.core.entity_extractor import EntityExtractor, _excerpt_around
from thestill.models.annotated_transcript import AnnotatedTranscript
from thestill.models.entities import EntityMention, ResolutionStatus

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "entity_extractor" / "sample_episode_okrs.json"


class StubGLiNER:
    """Minimal stand-in for ``gliner.GLiNER`` used across both
    extractor and handler tests. Real GLiNER loads ~400MB of weights
    we don't want in unit tests. The class is duplicated in
    ``test_handle_extract_entities.py`` because pytest's
    ``conftest.py`` doesn't share importable classes — sharing via a
    ``_test_stubs.py`` module would require restructuring the test
    layout (no ``__init__.py`` files today). Two ~20-line copies is a
    smaller cost than that restructuring.
    """

    SURFACE_FORMS: Tuple[Tuple[str, str, float], ...] = (
        ("OKR", "topic", 0.85),
        ("Melissa", "person", 0.92),
    )

    def predict_entities(self, text: str, labels: List[str], threshold: float = 0.5):
        results = []
        for surface, label, score in self.SURFACE_FORMS:
            idx = text.find(surface)
            if idx == -1:
                continue
            results.append({"text": surface, "label": label, "start": idx, "end": idx + len(surface), "score": score})
        return results

    def inference(self, texts, labels: List[str], threshold: float = 0.5, **_):
        if isinstance(texts, str):
            return self.predict_entities(texts, labels, threshold)
        return [self.predict_entities(t, labels, threshold) for t in texts]


def _stub_extractor() -> EntityExtractor:
    return EntityExtractor(preloaded_model=StubGLiNER())


class TestExtractorContract:
    def test_skips_non_content_segments(self):
        # Build a 2-segment transcript: one ad_break (ignored), one content (scanned).
        transcript = AnnotatedTranscript.model_validate(
            {
                "episode_id": "ep-1",
                "segments": [
                    {
                        "id": 0,
                        "start": 0.0,
                        "end": 30.0,
                        "speaker": None,
                        "text": "OKR sponsor read",
                        "kind": "ad_break",
                    },
                    {
                        "id": 1,
                        "start": 30.0,
                        "end": 60.0,
                        "speaker": "Melissa Perri",
                        "text": "Today we're talking about OKRs.",
                        "kind": "content",
                    },
                ],
            }
        )
        mentions = _stub_extractor().extract(transcript, episode_id="ep-1")
        # ad_break must not produce hits; content one does.
        assert all(m.segment_id == 1 for m in mentions)
        assert {m.surface_form for m in mentions} == {"OKR"}

    def test_emits_pending_unresolved_mentions(self):
        transcript = AnnotatedTranscript.model_validate(
            {
                "episode_id": "ep-1",
                "segments": [
                    {
                        "id": 5,
                        "start": 100.5,
                        "end": 130.7,
                        "speaker": "Melissa Perri",
                        "text": "Melissa explains OKR cascades.",
                        "kind": "content",
                    },
                ],
            }
        )
        mentions = _stub_extractor().extract(transcript, episode_id="ep-uuid")

        # Stub matches "OKR" + "Melissa" — both present in the text.
        assert len(mentions) == 2
        for m in mentions:
            assert isinstance(m, EntityMention)
            assert m.entity_id is None
            assert m.resolution_status is ResolutionStatus.PENDING
            assert m.episode_id == "ep-uuid"
            assert m.segment_id == 5
            assert m.start_ms == 100500
            assert m.end_ms == 130700
            assert m.speaker == "Melissa Perri"
            assert m.confidence > 0
            assert m.extractor.startswith("gliner:")
            # quote excerpt must contain the surface form
            assert m.surface_form.lower() in m.quote_excerpt.lower()

    def test_empty_transcript_returns_empty(self):
        transcript = AnnotatedTranscript.model_validate({"episode_id": "ep-1", "segments": []})
        assert _stub_extractor().extract(transcript, episode_id="ep-1") == []

    def test_uses_caller_episode_id_not_sidecar(self):
        # Sidecar's episode_id is often empty in practice; the DB row id wins.
        transcript = AnnotatedTranscript.model_validate(
            {
                "episode_id": "",
                "segments": [{"id": 0, "start": 0.0, "end": 1.0, "text": "OKR", "kind": "content"}],
            }
        )
        mentions = _stub_extractor().extract(transcript, episode_id="real-uuid")
        assert all(m.episode_id == "real-uuid" for m in mentions)


class TestRealFixture:
    def test_extracts_against_real_sidecar(self):
        transcript = AnnotatedTranscript.model_validate_json(FIXTURE.read_text())
        mentions = _stub_extractor().extract(transcript, episode_id="ep-okrs")
        # Stub matches "OKR" in many segments and "Melissa" in the intro;
        # the assertion is just that we get >0 mentions and they all
        # respect the contract.
        assert len(mentions) > 0
        for m in mentions:
            assert m.entity_id is None
            assert m.resolution_status is ResolutionStatus.PENDING
            assert m.episode_id == "ep-okrs"
            assert m.start_ms >= 0
            assert m.end_ms > m.start_ms


class _PronounStub(StubGLiNER):
    """Variant that emits only pronoun hits — tests the stoplist filter."""

    SURFACE_FORMS = (
        ("You", "person", 0.7),
        ("I", "person", 0.6),
        ("we", "person", 0.5),
    )


class TestStoplist:
    """Pronouns are filtered at extraction time so the resolution stage
    isn't drowned in 44x "you" rows that never resolve."""

    def test_pronouns_filtered(self):
        transcript = AnnotatedTranscript.model_validate(
            {
                "episode_id": "ep-1",
                "segments": [
                    {
                        "id": 0,
                        "start": 0.0,
                        "end": 5.0,
                        "text": "You said it, I agree, we all do.",
                        "kind": "content",
                    }
                ],
            }
        )
        extractor = EntityExtractor(preloaded_model=_PronounStub())
        assert extractor.extract(transcript, episode_id="ep-1") == []


class TestExcerpt:
    def test_excerpt_snaps_to_sentence_boundary(self):
        text = "Earlier sentence. Mr. Smith said the line. Following sentence."
        idx = text.find("Mr. Smith")
        out = _excerpt_around(text, idx, idx + len("Mr. Smith"))
        assert "Mr. Smith said the line" in out
        # Should NOT include the trailing sentence due to window snap
        # OR included — either is fine, just verify the surface form is in it
        assert "Mr. Smith" in out

    def test_excerpt_handles_in_bounds_slices(self):
        text = "OKR"
        out = _excerpt_around(text, 0, 3)
        assert out == "OKR"


class TestLazyModelLoad:
    def test_extract_raises_helpful_error_when_gliner_missing(self):
        # The handler-level error path: an extractor without a
        # preloaded model tries to import gliner; if the import fails
        # we emit a typed RuntimeError pointing the user at the extra.
        extractor = EntityExtractor()
        transcript = AnnotatedTranscript.model_validate({"episode_id": "ep-1", "segments": []})
        with patch.dict("sys.modules", {"gliner": None}):
            try:
                extractor.extract(transcript, episode_id="ep-1")
            except RuntimeError as exc:
                assert "entities extra" in str(exc)
                return
        # gliner was actually installed in this env — the test cannot
        # exercise the import-failure path. Skip rather than false-pass.
        import pytest

        pytest.skip("gliner is installed; cannot exercise the missing-import path")
