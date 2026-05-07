"""Spec #28 §1.5 — entity resolver tests.

Uses a stub ReFinED via ``SimpleNamespace`` to mirror the real
``Span``/``Entity`` shape without loading the multi-GB Wikidata
index. Resolver contract:

- mentions in → ``ResolutionResult`` out, one per mention
- entity_type prefers GLiNER ``surface_label``, falls back to ReFinED
  ``coarse_type``, defaults to ``topic``
- unresolvable mentions still produce a local-slug ``EntityRecord``
  (so future occurrences can merge in)
- ``surface_form == canonical_name`` ⇒ no alias added (avoid
  redundant aliases like ``"OpenAI"`` aliased to itself)
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import List

from thestill.core.entity_resolver import EntityResolver, _build_entity_id, _char_overlap, _pick_best_span
from thestill.models.entities import EntityMention, EntityType, ResolutionStatus


class StubReFinED:
    """Returns a deterministic span list keyed by exact substring match.

    Configure with ``predictions``: a dict of substring → (qid,
    title, coarse_type). When ``process_text(text)`` runs, every
    substring whose key appears in ``text`` gets a span with the
    configured payload.
    """

    def __init__(self, predictions=None):
        self.predictions = predictions or {
            "Elon Musk": ("Q317521", "Elon Musk", "PER"),
            "OpenAI": ("Q21708200", "OpenAI", "ORG"),
            "Tesla": ("Q478214", "Tesla, Inc.", "ORG"),
        }

    def process_text(self, text: str):
        spans = []
        for surface, (qid, title, coarse) in self.predictions.items():
            idx = text.find(surface)
            if idx == -1:
                continue
            spans.append(
                SimpleNamespace(
                    text=surface,
                    coarse_type=coarse,
                    predicted_entity=SimpleNamespace(
                        wikidata_entity_id=qid,
                        wikipedia_entity_title=title,
                        human_readable_name=title,
                        description=None,
                    ),
                )
            )
        return spans


def _mention(
    mention_id: int,
    surface: str,
    *,
    label: str | None = None,
    excerpt: str | None = None,
) -> EntityMention:
    return EntityMention(
        id=mention_id,
        episode_id="ep-1",
        segment_id=0,
        start_ms=0,
        end_ms=1000,
        surface_form=surface,
        surface_label=label,
        quote_excerpt=excerpt or f"Some text mentioning {surface}.",
        confidence=0.9,
        extractor="gliner:test",
    )


def _resolver(predictions=None) -> EntityResolver:
    return EntityResolver(preloaded_model=StubReFinED(predictions))


class TestResolveBasic:
    def test_empty_input_returns_empty(self):
        assert _resolver().resolve([]) == []

    def test_resolved_mention_carries_qid_and_canonical_name(self):
        mentions = [_mention(1, "Elon Musk", label="person")]
        results = _resolver().resolve(mentions)
        assert len(results) == 1
        r = results[0]
        assert r.status == "resolved"
        assert r.mention_id == 1
        assert r.entity.wikidata_qid == "Q317521"
        assert r.entity.canonical_name == "Elon Musk"
        assert r.entity.type is EntityType.PERSON

    def test_unresolvable_mention_produces_local_slug_entity(self):
        # Surface form not in the stub's prediction map.
        mentions = [_mention(2, "GibberishCorp", label="company")]
        results = _resolver().resolve(mentions)
        assert results[0].status == "unresolvable"
        assert results[0].entity.wikidata_qid is None
        assert results[0].entity.id == "company:gibberishcorp"
        assert results[0].entity.type is EntityType.COMPANY

    def test_one_failure_does_not_crash_the_batch(self):
        class _FlakyReFinED(StubReFinED):
            def process_text(self, text):
                if "boom" in text:
                    raise RuntimeError("synthetic failure")
                return super().process_text(text)

        resolver = EntityResolver(preloaded_model=_FlakyReFinED())
        mentions = [
            _mention(1, "Elon Musk", label="person"),
            _mention(2, "boom", label="topic", excerpt="this contains boom on purpose"),
            _mention(3, "OpenAI", label="company"),
        ]
        results = resolver.resolve(mentions)
        statuses = [r.status for r in results]
        # boom mention falls back to unresolvable, the others resolve
        assert statuses == ["resolved", "unresolvable", "resolved"]


class TestEntityTypeInference:
    def test_surface_label_takes_priority(self):
        # ReFinED says ORG, but GLiNER said person; surface_label wins
        results = _resolver({"Apple": ("Q312", "Apple Inc.", "ORG")}).resolve([_mention(1, "Apple", label="person")])
        assert results[0].entity.type is EntityType.PERSON

    def test_coarse_type_fallback(self):
        # No surface_label; coarse_type=ORG → company
        results = _resolver().resolve([_mention(1, "Tesla", label=None)])
        assert results[0].entity.type is EntityType.COMPANY

    def test_unknown_type_defaults_to_topic(self):
        results = _resolver({"Mystery": ("Q9999", "Mystery", "WAT")}).resolve([_mention(1, "Mystery", label=None)])
        assert results[0].entity.type is EntityType.TOPIC


class TestEntityIdBuilding:
    def test_qid_fallback_when_slug_degrades_to_unnamed(self):
        # Pure-symbols surface form transliterates to "unnamed"; we
        # prefer the QID-derived slug instead.
        eid = _build_entity_id(EntityType.PERSON, "🦄💎🎵", qid="Q123")
        assert eid == "person:q123"

    def test_normal_slug_path(self):
        eid = _build_entity_id(EntityType.PERSON, "Elon Musk", qid="Q317521")
        assert eid == "person:elon-musk"

    def test_no_qid_keeps_unnamed_when_unmappable(self):
        # Without a QID we have no fallback, so we accept the literal
        # "unnamed" — the alias-merge job can clean these up later.
        eid = _build_entity_id(EntityType.TOPIC, "🎵🎵", qid=None)
        assert eid == "topic:unnamed"


class TestAliasGeneration:
    def test_surface_form_added_as_alias_when_different_from_canonical(self):
        # ReFinED returns "Tesla, Inc." as the canonical title, but
        # the surface form was just "Tesla". Alias should be set.
        results = _resolver().resolve([_mention(1, "Tesla", label="company")])
        assert "Tesla" in results[0].entity.aliases
        assert results[0].entity.canonical_name == "Tesla, Inc."

    def test_no_alias_when_surface_matches_canonical(self):
        # OpenAI surface == canonical → no redundant alias.
        results = _resolver().resolve([_mention(1, "OpenAI", label="company")])
        assert results[0].entity.aliases == []


class TestSpanPicker:
    def test_exact_match_wins(self):
        spans = [
            SimpleNamespace(text="Apple", predicted_entity=None),
            SimpleNamespace(text="Apple Inc", predicted_entity=None),
        ]
        assert _pick_best_span(spans, "Apple Inc").text == "Apple Inc"

    def test_overlap_fallback(self):
        spans = [
            SimpleNamespace(text="Anthropic AI", predicted_entity=None),
            SimpleNamespace(text="OpenAI", predicted_entity=None),
        ]
        assert _pick_best_span(spans, "Anthropic").text == "Anthropic AI"


class TestCharOverlap:
    def test_identical_strings(self):
        assert _char_overlap("OpenAI", "OpenAI") == 6

    def test_shared_substring(self):
        assert _char_overlap("hello world", "world peace") == 5


class _StubP31Client:
    """Test stub matching the ``_P31Lookup`` protocol the resolver
    expects. Returns the configured P31 list for matching QIDs and
    ``[]`` otherwise.
    """

    def __init__(self, p31_by_qid: dict[str, list[str]] | None = None):
        self.p31_by_qid = p31_by_qid or {}
        self.calls: list[str] = []

    def fetch_p31(self, qid: str) -> list[str]:
        self.calls.append(qid)
        return self.p31_by_qid.get(qid, [])


class TestP31Gating:
    """Spec #28 §5.2 — Wikidata P31 reclassifies the bucket when
    GLiNER/coarse_type disagree with Wikidata's instance-of."""

    def test_country_qid_demotes_company_to_topic(self):
        # ``Israel`` mislabeled as company by GLiNER. Wikidata says Q801
        # is instance-of Q6256 (country). Resolver should demote to topic.
        predictions = {"Israel": ("Q801", "Israel", "ORG")}
        client = _StubP31Client({"Q801": ["Q6256"]})
        resolver = EntityResolver(
            preloaded_model=StubReFinED(predictions),
            wikidata_client=client,
        )
        results = resolver.resolve([_mention(1, "Israel", label="company")])
        assert results[0].entity.type is EntityType.TOPIC
        assert results[0].entity.id.startswith("topic:")
        assert results[0].entity.wikidata_instance_of == ["Q6256"]

    def test_human_qid_keeps_person_when_already_person(self):
        predictions = {"Elon Musk": ("Q317521", "Elon Musk", "PER")}
        client = _StubP31Client({"Q317521": ["Q5"]})
        resolver = EntityResolver(
            preloaded_model=StubReFinED(predictions),
            wikidata_client=client,
        )
        results = resolver.resolve([_mention(1, "Elon Musk", label="person")])
        assert results[0].entity.type is EntityType.PERSON
        assert results[0].entity.wikidata_instance_of == ["Q5"]

    def test_no_p31_signal_falls_back_to_inferred_type(self):
        predictions = {"Mystery": ("Q9999", "Mystery", "WAT")}
        # Empty P31 list — client returned nothing.
        client = _StubP31Client({})
        resolver = EntityResolver(
            preloaded_model=StubReFinED(predictions),
            wikidata_client=client,
        )
        results = resolver.resolve([_mention(1, "Mystery", label="company")])
        # Falls back to GLiNER's surface label.
        assert results[0].entity.type is EntityType.COMPANY
        assert results[0].entity.wikidata_instance_of == []

    def test_disabled_when_client_is_none(self):
        # Default constructor — no Wikidata client — preserves the
        # pre-spec-§5.2 behavior. No reclassification, no P31 cached.
        resolver = EntityResolver(preloaded_model=StubReFinED())
        results = resolver.resolve([_mention(1, "OpenAI", label="company")])
        assert results[0].entity.type is EntityType.COMPANY
        assert results[0].entity.wikidata_instance_of == []

    def test_no_overlap(self):
        assert _char_overlap("apple", "xyz") == 0

    def test_empty_string(self):
        assert _char_overlap("", "anything") == 0
