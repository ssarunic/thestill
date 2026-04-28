"""Spec #28 — round-trip tests for the entity-layer Pydantic models.

The models live in ``thestill/models/entities.py`` and back the
SQLite ``entities`` / ``entity_mentions`` tables plus the citation-row
wire shape returned by every search/list MCP tool. These tests pin the
public field set; they do *not* test extraction logic (Phase 1) or
qmd integration (Phase 2).
"""

from datetime import datetime, timezone

from thestill.models.entities import (
    CitationRow,
    EntityExtractionStatus,
    EntityMention,
    EntityRecord,
    EntityType,
    MatchType,
    MentionRole,
    ResolutionStatus,
    SegmentAnchor,
)


class TestEntityRecord:
    def test_minimal_round_trip(self):
        ent = EntityRecord(
            id="person:elon-musk",
            type=EntityType.PERSON,
            canonical_name="Elon Musk",
        )
        dumped = ent.model_dump()
        restored = EntityRecord.model_validate(dumped)
        assert restored.id == "person:elon-musk"
        assert restored.type is EntityType.PERSON
        assert restored.canonical_name == "Elon Musk"
        assert restored.aliases == []
        assert restored.wikidata_qid is None

    def test_full_round_trip(self):
        ent = EntityRecord(
            id="company:spacex",
            type=EntityType.COMPANY,
            canonical_name="SpaceX",
            wikidata_qid="Q193701",
            aliases=["Space Exploration Technologies", "SpaceX Corp"],
            description="Aerospace manufacturer.",
        )
        dumped = ent.model_dump()
        restored = EntityRecord.model_validate(dumped)
        assert restored.aliases == ["Space Exploration Technologies", "SpaceX Corp"]
        assert restored.wikidata_qid == "Q193701"


class TestEntityMention:
    def test_pending_mention_defaults(self):
        m = EntityMention(
            episode_id="ep-1",
            segment_id=42,
            start_ms=2347000,
            end_ms=2389000,
            surface_form="Musk",
            quote_excerpt="Musk said the line will be...",
            confidence=0.91,
            extractor="gliner-v2.5",
        )
        assert m.id is None
        assert m.entity_id is None
        assert m.resolution_status is ResolutionStatus.PENDING
        assert m.role is None

    def test_round_trip_with_resolution(self):
        m = EntityMention(
            id=7,
            entity_id="person:elon-musk",
            resolution_status=ResolutionStatus.RESOLVED,
            episode_id="ep-1",
            segment_id=42,
            start_ms=2347000,
            end_ms=2389000,
            speaker="Scott Galloway",
            role=MentionRole.MENTIONED,
            surface_form="Musk",
            quote_excerpt="Musk said the line will be...",
            sentiment=-0.2,
            confidence=0.91,
            extractor="gliner-v2.5",
            resolved_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
        )
        restored = EntityMention.model_validate(m.model_dump())
        assert restored.role is MentionRole.MENTIONED
        assert restored.resolution_status is ResolutionStatus.RESOLVED
        assert restored.sentiment == -0.2


class TestSegmentAnchor:
    def test_round_trip(self):
        a = SegmentAnchor(
            seg_id=42,
            line_start=7,
            line_end=9,
            byte_start=0,
            byte_end=312,
            start_ms=2347000,
            end_ms=2389000,
        )
        restored = SegmentAnchor.model_validate(a.model_dump())
        assert restored.seg_id == 42
        assert restored.byte_end == 312


class TestCitationRow:
    def test_round_trip(self):
        row = CitationRow(
            episode_id="ep-1",
            podcast_id="pod-1",
            podcast_title="Prof G Markets",
            episode_title="The AI Capex Cliff",
            published_at=datetime(2026, 3, 14, tzinfo=timezone.utc),
            start_ms=2347000,
            end_ms=2389000,
            speaker="Scott Galloway",
            quote="The hyperscalers are spending like...",
            score=0.873,
            match_type=MatchType.LEXICAL,
            deeplink="thestill://episode/ep-1?t=2347",
            web_url="/episodes/ep-1?t=2347",
        )
        restored = CitationRow.model_validate(row.model_dump())
        assert restored.match_type is MatchType.LEXICAL
        assert restored.score == 0.873


class TestEnumStrCompat:
    """Each enum is a ``str``-Enum so tools that compare to plain strings work."""

    def test_resolution_status_string_comparison(self):
        assert ResolutionStatus.PENDING == "pending"

    def test_match_type_string_comparison(self):
        assert MatchType.LEXICAL == "lexical"
        assert MatchType.SEMANTIC == "semantic"
        assert MatchType.ENTITY == "entity"

    def test_entity_extraction_status_values(self):
        # Spec #28 — the on-disk allowed set.
        values = {s.value for s in EntityExtractionStatus}
        assert values == {"pending", "complete", "failed", "skipped_legacy"}
