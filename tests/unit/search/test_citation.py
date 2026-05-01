"""Spec #28 §4 — citation row builder tests."""

from __future__ import annotations

from datetime import datetime, timezone

from thestill.models.entities import EntityMention, MatchType, MentionRole, ResolutionStatus
from thestill.repositories.sqlite_entity_repository import MentionContext
from thestill.search.citation import build_citation_row, build_citation_rows


def _ctx(start_ms: int = 2_347_000, end_ms: int = 2_389_000) -> MentionContext:
    return MentionContext(
        mention=EntityMention(
            id=42,
            entity_id="person:scott-galloway",
            resolution_status=ResolutionStatus.RESOLVED,
            episode_id="ep-1",
            segment_id=10,
            start_ms=start_ms,
            end_ms=end_ms,
            speaker="Scott Galloway",
            role=MentionRole.HOST,
            surface_form="Scott Galloway",
            quote_excerpt="The hyperscalers are spending like…",
            confidence=0.95,
            extractor="gliner:test",
        ),
        episode_id="ep-1",
        episode_title="The AI Capex Cliff",
        episode_pub_date=datetime(2026, 3, 14, tzinfo=timezone.utc),
        podcast_id="pod-1",
        podcast_title="Prof G Markets",
        podcast_slug="prof-g-markets",
        entity_type="person",
        entity_canonical_name="Scott Galloway",
    )


class TestBuildCitationRow:
    def test_round_trip_carries_all_required_fields(self):
        row = build_citation_row(_ctx())
        assert row.episode_id == "ep-1"
        assert row.podcast_id == "pod-1"
        assert row.podcast_title == "Prof G Markets"
        assert row.episode_title == "The AI Capex Cliff"
        assert row.start_ms == 2347000
        assert row.end_ms == 2389000
        assert row.speaker == "Scott Galloway"
        assert row.match_type is MatchType.ENTITY
        assert row.deeplink == "thestill://episode/ep-1?t=2347"
        assert row.web_url == "/episodes/ep-1?t=2347"

    def test_default_score(self):
        assert build_citation_row(_ctx()).score == 1.0

    def test_score_override(self):
        assert build_citation_row(_ctx(), score=0.4).score == 0.4

    def test_zero_start_ms(self):
        # First segment of an episode should produce ?t=0, not crash
        row = build_citation_row(_ctx(start_ms=0, end_ms=5_000))
        assert row.deeplink.endswith("?t=0")
        assert row.web_url.endswith("?t=0")


class TestBuildCitationRows:
    def test_bulk_convert(self):
        rows = build_citation_rows([_ctx(), _ctx(start_ms=0, end_ms=1_000)])
        assert len(rows) == 2
        assert {r.start_ms for r in rows} == {2_347_000, 0}
