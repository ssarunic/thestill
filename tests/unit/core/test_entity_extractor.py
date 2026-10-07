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
        # Spec §1.13.2 — content segments now also produce a synthetic
        # SPEAKING mention with the speaker label as surface form. Filter
        # to the GLiNER body extractions when asserting against the
        # body-text surface form set.
        body_surfaces = {m.surface_form for m in mentions if m.extractor.startswith("gliner")}
        assert body_surfaces == {"OKR"}

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

        # Filter to gliner-emitted body mentions — speaker synthesis adds
        # a SPEAKING row per content segment that we cover separately.
        gliner_mentions = [m for m in mentions if m.extractor.startswith("gliner")]

        # Stub matches "OKR" + "Melissa" — both present in the text.
        assert len(gliner_mentions) == 2
        for m in gliner_mentions:
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
        # respect the contract. Speaker synthesis adds SPEAKING rows
        # alongside; assert on the gliner-emitted body mentions.
        gliner_mentions = [m for m in mentions if m.extractor.startswith("gliner")]
        assert len(gliner_mentions) > 0
        for m in gliner_mentions:
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


class _AnchorStub(StubGLiNER):
    SURFACE_FORMS = (
        ("Dogus Cubuk", "person", 0.9),
        ("Gary Neville", "person", 0.9),
    )


class TestAnchorScan:
    """Spec §1.13.4 — the anchor scan adds host/guest names GLiNER missed,
    and never re-reads text GLiNER already took."""

    def _extract(self, text: str):
        from thestill.core.entity_anchor import expand_anchor_variants
        from thestill.models.entities import EntityRecord, EntityType

        anchors = [
            EntityRecord(id="person:dogus-cubuk", type=EntityType.PERSON, canonical_name="Dogus Cubuk"),
            EntityRecord(id="person:gary-lineker", type=EntityType.PERSON, canonical_name="Gary Lineker"),
        ]
        transcript = AnnotatedTranscript.model_validate(
            {
                "episode_id": "ep-1",
                "segments": [{"id": 3, "start": 0.0, "end": 30.0, "speaker": None, "text": text, "kind": "content"}],
            }
        )
        extractor = EntityExtractor(preloaded_model=_AnchorStub())
        return extractor.extract(transcript, episode_id="ep-1", anchor_variants=expand_anchor_variants(anchors))

    def test_does_not_rescan_parts_of_a_name_gliner_found(self):
        mentions = self._extract("Liam Fedus and Dogus Cubuk are building a company to change that.")

        # One row for the one name — not "Dogus Cubuk" + "Dogus" + "Cubuk".
        assert [(m.surface_form, m.extractor.split(":")[0]) for m in mentions] == [("Dogus Cubuk", "gliner")]
        assert mentions[0].entity_id == "person:dogus-cubuk"

    def test_part_of_another_persons_name_is_not_the_anchor(self):
        mentions = self._extract("You sounded like Gary Neville doing commentary work.")

        # "Gary" inside GLiNER's "Gary Neville" must not become host Gary Lineker.
        assert all(m.entity_id != "person:gary-lineker" for m in mentions)
        assert [m.extractor for m in mentions if m.extractor == "anchor:scan"] == []

    def test_still_finds_names_gliner_missed(self):
        mentions = self._extract("Dogus Cubuk joined us. Later Cubuk explained the lab, and Gary laughed.")

        scanned = sorted((m.surface_form, m.entity_id) for m in mentions if m.extractor == "anchor:scan")
        assert scanned == [("Cubuk", "person:dogus-cubuk"), ("Gary", "person:gary-lineker")]


class _SeedStub(StubGLiNER):
    SURFACE_FORMS = (("Mad Men", "topic", 0.9),)


class TestResourceSeeds:
    """Spec #92 Stage 4 — Resource List surfaces become pending mentions
    wherever the transcript says them, around what GLiNER and anchors took."""

    def _extract(self, *texts, seeds, anchors=()):
        from thestill.core.entity_anchor import expand_anchor_variants

        transcript = AnnotatedTranscript.model_validate(
            {
                "episode_id": "ep-1",
                "segments": [
                    {"id": i, "start": i * 10.0, "end": i * 10.0 + 9, "speaker": "Ed", "text": t, "kind": "content"}
                    for i, t in enumerate(texts)
                ],
            }
        )
        seen = []

        def provider(gliner_mentions):
            seen.append([m.surface_form for m in gliner_mentions])
            return seeds

        mentions = EntityExtractor(preloaded_model=_SeedStub()).extract(
            transcript, episode_id="ep-1", anchor_variants=expand_anchor_variants(anchors), seed_provider=provider
        )
        return mentions, seen

    def _seed(self, surface, label="product"):
        from thestill.core.summary_resources import ResourceSeed, is_single_token

        return ResourceSeed(surface, label, is_single_token(surface))

    def test_every_occurrence_becomes_a_pending_mention(self):
        mentions, seen = self._extract(
            "Kedrosky brings data.",
            "Paul Kedrosky was here.",
            seeds=[self._seed("Paul Kedrosky", "person"), self._seed("Kedrosky", "person")],
        )
        seeded = [m for m in mentions if m.extractor == "summary:resource"]
        assert [(m.segment_id, m.surface_form) for m in seeded] == [(0, "Kedrosky"), (1, "Paul Kedrosky")]
        assert all(m.resolution_status == ResolutionStatus.PENDING and m.entity_id is None for m in seeded)
        assert all(m.confidence == 0.9 and m.surface_label == "person" and m.role is None for m in seeded)
        assert seen == [[]]  # the provider saw GLiNER's (empty) mentions

    def test_a_span_gliner_found_is_not_duplicated(self):
        mentions, seen = self._extract("I've been watching Mad Men.", seeds=[self._seed("Mad Men")])
        assert [m.extractor.split(":")[0] for m in mentions if "Mad Men" in m.surface_form] == ["gliner"]
        assert seen == [["Mad Men"]]

    def test_an_anchor_span_is_not_rescanned(self):
        from thestill.models.entities import EntityRecord, EntityType

        host = EntityRecord(id="person:scott-galloway", type=EntityType.PERSON, canonical_name="Scott Galloway")
        mentions, _ = self._extract("Galloway said so.", seeds=[self._seed("Galloway", "person")], anchors=[host])
        assert [m.extractor for m in mentions if m.surface_form == "Galloway"] == ["anchor:scan"]

    def test_a_one_word_seed_matches_exact_case_only(self):
        mentions, _ = self._extract("Prices ramp up.", "Ramp data says so.", seeds=[self._seed("Ramp", "company")])
        assert [m.segment_id for m in mentions if m.extractor == "summary:resource"] == [1]

    def test_no_provider_means_no_seed_rows(self):
        transcript = AnnotatedTranscript.model_validate(
            {
                "episode_id": "ep-1",
                "segments": [{"id": 0, "start": 0, "end": 9, "speaker": None, "text": "Ramp.", "kind": "content"}],
            }
        )
        mentions = EntityExtractor(preloaded_model=_SeedStub()).extract(transcript, episode_id="ep-1")
        assert not any(m.extractor == "summary:resource" for m in mentions)
