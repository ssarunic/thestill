"""Spec #28 §1.8 — repository query path tests.

Covers ``find_mentions``, ``list_mentions_by_speaker``,
``get_mention_for_clip``, ``get_entity_summary``, and
``find_entity_by_name``. Mention insert + resolution paths live in
their own test files; this one is just the read-side queries.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from thestill.models.entities import EntityMention, EntityRecord, EntityType, MentionRole
from thestill.repositories.sqlite_entity_repository import SqliteEntityRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository


@pytest.fixture
def populated_db(tmp_path):
    """DB with one podcast + two episodes + three resolved mentions
    + one cooccurrence pair, ready for read-side queries.
    """
    db_path = tmp_path / "thestill.db"
    SqlitePodcastRepository(db_path=str(db_path))
    podcast_id = str(uuid.uuid4())
    ep1 = str(uuid.uuid4())
    ep2 = str(uuid.uuid4())
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
            (podcast_id, "https://example.com/feed.xml", "Prof G Markets", "prof-g-markets"),
        )
        conn.execute(
            "INSERT INTO episodes (id, podcast_id, external_id, title, audio_url, pub_date) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ep1, podcast_id, "e1", "SpaceX IPO", "https://example.com/ep1.mp3", "2026-04-28T00:00:00"),
        )
        conn.execute(
            "INSERT INTO episodes (id, podcast_id, external_id, title, audio_url, pub_date) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ep2, podcast_id, "e2", "AI Job Crisis", "https://example.com/ep2.mp3", "2026-04-24T00:00:00"),
        )
        conn.commit()
    finally:
        conn.close()

    repo = SqliteEntityRepository(db_path=str(db_path))
    repo.upsert_entity(
        EntityRecord(
            id="person:elon-musk",
            type=EntityType.PERSON,
            canonical_name="Elon Musk",
            wikidata_qid="Q317521",
            aliases=["Musk"],
        )
    )
    repo.upsert_entity(
        EntityRecord(
            id="company:spacex",
            type=EntityType.COMPANY,
            canonical_name="SpaceX",
            wikidata_qid="Q193701",
        )
    )
    repo.upsert_entity(
        EntityRecord(
            id="topic:ai-job-loss",
            type=EntityType.TOPIC,
            canonical_name="AI Job Loss",
        )
    )

    def _mention(episode, surface, entity_id, segment_id, speaker="Scott Galloway"):
        return EntityMention(
            episode_id=episode,
            segment_id=segment_id,
            start_ms=segment_id * 10_000,
            end_ms=segment_id * 10_000 + 8_000,
            speaker=speaker,
            role=MentionRole.MENTIONED,
            surface_form=surface,
            quote_excerpt=f"… {surface} …",
            confidence=0.95,
            extractor="gliner:test",
        )

    repo.insert_mentions(
        [
            _mention(ep1, "Elon Musk", "person:elon-musk", 1),
            _mention(ep1, "SpaceX", "company:spacex", 2),
            _mention(ep2, "AI Job Loss", "topic:ai-job-loss", 1, speaker="Andrew Yang"),
        ]
    )
    pending = repo.list_pending_mentions()
    surface_to_entity = {
        "Elon Musk": "person:elon-musk",
        "SpaceX": "company:spacex",
        "AI Job Loss": "topic:ai-job-loss",
    }
    for m in pending:
        repo.resolve_mention(
            mention_id=m.id,
            entity_id=surface_to_entity[m.surface_form],
            status="resolved",
        )
    repo.rebuild_cooccurrences(episode_ids=None)
    return repo, podcast_id, ep1, ep2


class TestFindMentions:
    def test_find_by_entity_id(self, populated_db):
        repo, _, ep1, _ = populated_db
        results = repo.find_mentions(entity_id="person:elon-musk")
        assert len(results) == 1
        assert results[0].episode_id == ep1
        assert results[0].mention.surface_form == "Elon Musk"
        assert results[0].entity_canonical_name == "Elon Musk"

    def test_find_by_entity_type(self, populated_db):
        repo, _, _, _ = populated_db
        people = repo.find_mentions(entity_type="person")
        assert {r.mention.surface_form for r in people} == {"Elon Musk"}
        topics = repo.find_mentions(entity_type="topic")
        assert {r.mention.surface_form for r in topics} == {"AI Job Loss"}

    def test_find_by_podcast_id(self, populated_db):
        repo, podcast_id, _, _ = populated_db
        results = repo.find_mentions(podcast_id=podcast_id, limit=10)
        assert len(results) == 3

    def test_find_by_role(self, populated_db):
        repo, _, _, _ = populated_db
        results = repo.find_mentions(role="mentioned")
        assert len(results) == 3
        assert all(r.mention.role == MentionRole.MENTIONED for r in results)

    def test_find_by_date_range(self, populated_db):
        repo, _, _, _ = populated_db
        only_april_28 = repo.find_mentions(
            date_range=(
                datetime(2026, 4, 27, tzinfo=timezone.utc),
                datetime(2026, 4, 29, tzinfo=timezone.utc),
            ),
        )
        assert len(only_april_28) == 2
        # All three mentions belong to ep1 (pub_date 2026-04-28)
        assert all(r.episode_id == only_april_28[0].episode_id for r in only_april_28)


class TestListMentionsBySpeaker:
    def test_substring_match_case_insensitive(self, populated_db):
        repo, _, _, _ = populated_db
        results = repo.list_mentions_by_speaker(speaker="galloway")
        assert len(results) == 2
        assert all(r.mention.speaker == "Scott Galloway" for r in results)

    def test_topic_filter(self, populated_db):
        repo, _, _, _ = populated_db
        # Galloway never co-occurred with the AI topic; topic filter
        # should produce zero rows.
        results = repo.list_mentions_by_speaker(speaker="Galloway", topic_entity_id="topic:ai-job-loss")
        assert results == []

        # Andrew Yang is the speaker on the topic-bearing episode.
        results = repo.list_mentions_by_speaker(speaker="Yang", topic_entity_id="topic:ai-job-loss")
        assert len(results) == 1


class TestGetMentionForClip:
    def test_straddling_match(self, populated_db):
        repo, _, ep1, _ = populated_db
        # Segment 1: start=10000 end=18000. Request inside range.
        ctx = repo.get_mention_for_clip(episode_id=ep1, start_ms=12000)
        assert ctx is not None
        assert ctx.mention.surface_form == "Elon Musk"

    def test_nearest_fallback(self, populated_db):
        repo, _, ep1, _ = populated_db
        # Far past any segment — falls back to nearest by abs(distance).
        ctx = repo.get_mention_for_clip(episode_id=ep1, start_ms=999_999)
        assert ctx is not None
        # Closest is segment 2 (start_ms=20000)
        assert ctx.mention.surface_form == "SpaceX"

    def test_no_resolved_mentions_returns_none(self, populated_db):
        repo, _, _, _ = populated_db
        ctx = repo.get_mention_for_clip(episode_id="bogus-ep", start_ms=0)
        assert ctx is None


class TestGetEntitySummary:
    def test_summary_shape(self, populated_db):
        repo, _, _, _ = populated_db
        s = repo.get_entity_summary("person:elon-musk")
        assert s["entity"].id == "person:elon-musk"
        assert s["mention_count"] == 1
        # Co-occurs with SpaceX in ep1
        cooccur_ids = {c["entity"].id for c in s["cooccurring"]}
        assert "company:spacex" in cooccur_ids
        assert len(s["recent_mentions"]) == 1

    def test_unknown_entity_returns_none(self, populated_db):
        repo, _, _, _ = populated_db
        assert repo.get_entity_summary("person:nobody") is None


class TestFindEntityByName:
    def test_exact_id(self, populated_db):
        repo, _, _, _ = populated_db
        e = repo.find_entity_by_name("person:elon-musk")
        assert e and e.canonical_name == "Elon Musk"

    def test_canonical_name_case_insensitive(self, populated_db):
        repo, _, _, _ = populated_db
        e = repo.find_entity_by_name("elon musk")
        assert e and e.id == "person:elon-musk"

    def test_alias_match(self, populated_db):
        repo, _, _, _ = populated_db
        e = repo.find_entity_by_name("Musk")
        assert e and e.id == "person:elon-musk"

    def test_type_filter_disambiguates(self, populated_db):
        repo, _, _, _ = populated_db
        # No company named "Elon Musk" — should not return the person
        # when entity_type is constrained to ``company``.
        assert repo.find_entity_by_name("Elon Musk", entity_type="company") is None

    def test_unknown_name_returns_none(self, populated_db):
        repo, _, _, _ = populated_db
        assert repo.find_entity_by_name("Nobody") is None
