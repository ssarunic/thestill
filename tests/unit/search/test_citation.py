"""Spec #28 §4 — citation row builder tests."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from thestill.models.entities import EntityMention, MatchType, MentionRole, ResolutionStatus
from thestill.repositories.sqlite_entity_repository import MentionContext
from thestill.search.base import ResolvedHit
from thestill.search.citation import build_citation_row, build_citation_rows, search_citations


def _ctx(start_ms: int = 2_347_000, end_ms: int = 2_389_000, episode_slug="the-ai-capex-cliff") -> MentionContext:
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
        episode_slug=episode_slug,
        episode_audio_url="https://cdn.example.com/ep-1.mp3",
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
        assert row.web_url == "/podcasts/prof-g-markets/episodes/the-ai-capex-cliff#t=2347"
        assert row.audio_url == "https://cdn.example.com/ep-1.mp3"
        assert row.episode_url is None  # no PUBLIC_BASE_URL

    def test_base_url_makes_the_episode_url_absolute(self):
        row = build_citation_row(_ctx(), base_url="https://thestill.me")
        assert row.episode_url == "https://thestill.me/podcasts/prof-g-markets/episodes/the-ai-capex-cliff#t=2347"

    def test_no_episode_slug_means_no_page_link_rather_than_a_dead_one(self):
        row = build_citation_row(_ctx(episode_slug=None), base_url="https://thestill.me")
        assert row.web_url is None and row.episode_url is None
        assert row.audio_url == "https://cdn.example.com/ep-1.mp3"

    def test_default_score(self):
        assert build_citation_row(_ctx()).score == 1.0

    def test_score_override(self):
        assert build_citation_row(_ctx(), score=0.4).score == 0.4

    def test_zero_start_ms(self):
        # First segment of an episode should produce ?t=0, not crash
        row = build_citation_row(_ctx(start_ms=0, end_ms=5_000))
        assert row.deeplink.endswith("?t=0")
        assert row.web_url.endswith("#t=0")


class TestBuildCitationRows:
    def test_bulk_convert(self):
        rows = build_citation_rows([_ctx(), _ctx(start_ms=0, end_ms=1_000)])
        assert len(rows) == 2
        assert {r.start_ms for r in rows} == {2_347_000, 0}

    def test_base_url_reaches_every_row(self):
        rows = build_citation_rows([_ctx(), _ctx(start_ms=0)], base_url="https://thestill.me")
        assert all(r.episode_url.startswith("https://thestill.me/") for r in rows)


def _hit(episode_id: str, start_ms: int = 61_500) -> ResolvedHit:
    return ResolvedHit(
        episode_id=episode_id,
        podcast_id="pod-1",
        podcast_title="Prof G Markets",
        episode_title="The AI Capex Cliff",
        published_at=None,
        segment_id=3,
        start_ms=start_ms,
        end_ms=start_ms + 4_000,
        speaker=None,
        text="capex",
        score=1.0,
        match_type=MatchType.LEXICAL,
    )


class _Repo:
    """Just ``get_episodes_by_ids``; records each call's ids."""

    def __init__(self, pairs):
        self.pairs = pairs
        self.calls = []

    def get_episodes_by_ids(self, ids):
        self.calls.append(list(ids))
        return {i: self.pairs[i] for i in ids if i in self.pairs}


class TestSearchCitations:
    def _pair(self):
        podcast = SimpleNamespace(slug="prof-g-markets")
        episode = SimpleNamespace(slug="the-ai-capex-cliff", audio_url="https://cdn.example.com/ep-1.mp3")
        return podcast, episode

    def test_links_from_one_batched_lookup(self):
        repo = _Repo({"ep-1": self._pair()})
        rows = search_citations(
            [_hit("ep-1"), _hit("ep-1", 0), _hit("ep-gone")], repository=repo, base_url="https://thestill.me"
        )
        assert repo.calls == [["ep-1", "ep-gone"]]
        assert rows[0]["episode_url"] == "https://thestill.me/podcasts/prof-g-markets/episodes/the-ai-capex-cliff#t=61"
        assert rows[0]["audio_url"] == "https://cdn.example.com/ep-1.mp3"
        assert rows[1]["web_url"].endswith("#t=0")
        assert rows[2]["web_url"] is None and rows[2]["audio_url"] is None

    def test_no_hits_skips_the_lookup(self):
        repo = _Repo({})
        assert search_citations([], repository=repo) == []
        assert repo.calls == []
