"""Spec #28 §4.1 — unit tests for ``search_entities_by_prefix``.

The ⌘K typeahead lives or dies on this query: case-insensitive
substring match across ``canonical_name`` + JSON aliases, ranked by
resolved-mention count, capped per type. Test the contract; the
endpoint test covers the wire shape.
"""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from thestill.models.entities import EntityMention, EntityRecord, EntityType, MentionRole
from thestill.repositories.sqlite_entity_repository import SqliteEntityRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository


@pytest.fixture
def repo_with_entities(tmp_path):
    """DB seeded with five entities + a few resolved mentions so the
    mention-count ranking has something to sort on.
    """
    db_path = tmp_path / "thestill.db"
    SqlitePodcastRepository(db_path=str(db_path))
    podcast_id = str(uuid.uuid4())
    ep1 = str(uuid.uuid4())
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
            (podcast_id, "https://example.com/feed.xml", "Prof G", "prof-g"),
        )
        conn.execute(
            "INSERT INTO episodes (id, podcast_id, external_id, title, audio_url, pub_date) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ep1, podcast_id, "e1", "AI Capex", "https://example.com/ep1.mp3", "2026-04-28T00:00:00"),
        )
        conn.commit()
    finally:
        conn.close()

    repo = SqliteEntityRepository(db_path=str(db_path))
    # Five entities across types; aliases on Musk so we can exercise
    # the alias path. ``Mustafa Suleyman`` shares "Mu…" with Musk so
    # we can prove ranking behaviour.
    for record in [
        EntityRecord(id="person:elon-musk", type=EntityType.PERSON, canonical_name="Elon Musk", aliases=["Musk"]),
        EntityRecord(id="person:mustafa-suleyman", type=EntityType.PERSON, canonical_name="Mustafa Suleyman"),
        EntityRecord(id="company:spacex", type=EntityType.COMPANY, canonical_name="SpaceX"),
        EntityRecord(id="company:tesla", type=EntityType.COMPANY, canonical_name="Tesla"),
        EntityRecord(id="topic:ai-capex", type=EntityType.TOPIC, canonical_name="AI Capex"),
    ]:
        repo.upsert_entity(record)

    # Give Elon Musk three resolved mentions, Tesla two, the others
    # zero — so prefix "mu" should rank Musk above Suleyman, and
    # "te" should surface Tesla cleanly.
    def _mention(surface: str, entity_id: str, segment_id: int) -> EntityMention:
        return EntityMention(
            entity_id=entity_id,
            episode_id=ep1,
            segment_id=segment_id,
            start_ms=segment_id * 1000,
            end_ms=segment_id * 1000 + 500,
            speaker="Scott",
            role=MentionRole.MENTIONED,
            surface_form=surface,
            quote_excerpt=f"… {surface} …",
            confidence=0.95,
            extractor="gliner:test",
        )

    repo.insert_mentions([_mention("Elon Musk", None, i) for i in range(1, 4)])
    repo.insert_mentions([_mention("Tesla", None, i) for i in range(10, 12)])
    pending = repo.list_pending_mentions()
    surface_to_entity = {"Elon Musk": "person:elon-musk", "Tesla": "company:tesla"}
    for m in pending:
        repo.resolve_mention(
            mention_id=m.id,
            entity_id=surface_to_entity[m.surface_form],
            status="resolved",
        )
    return repo


class TestSearchEntitiesByPrefix:
    def test_empty_prefix_returns_empty(self, repo_with_entities):
        assert repo_with_entities.search_entities_by_prefix("") == []
        assert repo_with_entities.search_entities_by_prefix("   ") == []

    def test_canonical_name_match_case_insensitive(self, repo_with_entities):
        hits = repo_with_entities.search_entities_by_prefix("ELON")
        assert len(hits) == 1
        assert hits[0].id == "person:elon-musk"
        assert hits[0].matched_alias is None  # name hit, not alias

    def test_alias_match_returns_alias(self, repo_with_entities):
        # "Musk" hits the alias on Elon Musk's record (canonical name
        # also contains "Musk", so this is a canonical-name hit; the
        # alias-only path needs a substring that lives only in aliases).
        hits = repo_with_entities.search_entities_by_prefix("musk")
        assert len(hits) == 1
        assert hits[0].id == "person:elon-musk"

    def test_alias_only_substring_returns_alias_field(self, repo_with_entities, tmp_path):
        # Add an entity whose canonical_name doesn't contain the
        # alias substring, so we can prove ``matched_alias`` populates.
        repo = repo_with_entities
        repo.upsert_entity(
            EntityRecord(
                id="person:vlad-tenev",
                type=EntityType.PERSON,
                canonical_name="Vladimir Tenev",
                aliases=["Robinhood CEO"],
            )
        )
        hits = repo.search_entities_by_prefix("robinhood")
        assert len(hits) == 1
        assert hits[0].id == "person:vlad-tenev"
        assert hits[0].matched_alias == "Robinhood CEO"

    def test_ranks_by_mention_count(self, repo_with_entities):
        # Both "Elon Musk" and "Mustafa Suleyman" start with "mu"
        # (substring, not strict prefix). Musk has 3 resolved
        # mentions, Suleyman has 0 → Musk ranks first.
        hits = repo_with_entities.search_entities_by_prefix("mu")
        assert [h.id for h in hits[:2]] == ["person:elon-musk", "person:mustafa-suleyman"]
        assert hits[0].mention_count == 3
        assert hits[1].mention_count == 0

    def test_type_filter(self, repo_with_entities):
        hits = repo_with_entities.search_entities_by_prefix("e", types=("company",))
        assert {h.id for h in hits} == {"company:spacex", "company:tesla"}
        assert all(h.type == "company" for h in hits)

    def test_limit_per_type_caps_each_bucket(self, repo_with_entities):
        # "a" matches every entity. With limit_per_type=1, we should
        # see exactly one entity per type that contains it (person,
        # company, topic).
        hits = repo_with_entities.search_entities_by_prefix("a", limit_per_type=1)
        types = [h.type for h in hits]
        assert types.count("person") <= 1
        assert types.count("company") <= 1
        assert types.count("topic") <= 1

    def test_no_match_returns_empty(self, repo_with_entities):
        assert repo_with_entities.search_entities_by_prefix("zzzzz_unlikely") == []
