"""Spec #28 §1.13.5 — within-episode coreference pass."""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from thestill.core.entity_coref import resolve_coreferences_for_episode
from thestill.models.entities import (
    EntityMention,
    EntityRecord,
    EntityType,
    MentionRole,
    ResolutionMethod,
    ResolutionStatus,
)
from thestill.repositories.sqlite_entity_repository import SqliteEntityRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository


@pytest.fixture
def repo(tmp_path):
    db_path = tmp_path / "thestill.db"
    SqlitePodcastRepository(db_path=str(db_path))
    podcast_id = str(uuid.uuid4())
    episode_id = str(uuid.uuid4())
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
            (podcast_id, "https://example.com/feed.xml", "Fixture", "fixture"),
        )
        conn.execute(
            "INSERT INTO episodes (id, podcast_id, external_id, title, audio_url, pub_date) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (episode_id, podcast_id, "e1", "Ep", "https://example.com/e1.mp3", "2026-04-28T00:00:00"),
        )
        conn.commit()
    finally:
        conn.close()
    return SqliteEntityRepository(db_path=str(db_path)), episode_id


def _resolved_mention(episode_id, surface, entity_id, *, segment_id=1):
    return EntityMention(
        entity_id=entity_id,
        resolution_status=ResolutionStatus.RESOLVED,
        episode_id=episode_id,
        segment_id=segment_id,
        start_ms=segment_id * 1000,
        end_ms=segment_id * 1000 + 5000,
        speaker="Host",
        role=MentionRole.MENTIONED,
        surface_form=surface,
        surface_label="person",
        quote_excerpt=f"… {surface} …",
        confidence=0.9,
        extractor="gliner:test",
    )


def _unresolvable_mention(episode_id, surface, *, segment_id=2):
    return EntityMention(
        entity_id=None,
        resolution_status=ResolutionStatus.UNRESOLVABLE,
        episode_id=episode_id,
        segment_id=segment_id,
        start_ms=segment_id * 1000,
        end_ms=segment_id * 1000 + 5000,
        speaker="Host",
        role=MentionRole.MENTIONED,
        surface_form=surface,
        surface_label="person",
        quote_excerpt=f"… {surface} said …",
        confidence=0.7,
        extractor="gliner:test",
    )


class TestCorefPass:
    def test_repoints_short_form_to_single_long_form(self, repo):
        repository, episode_id = repo
        # Long-form anchor present — Andrej Karpathy is resolved
        repository.upsert_entity(
            EntityRecord(
                id="person:andrej-karpathy",
                type=EntityType.PERSON,
                canonical_name="Andrej Karpathy",
                wikidata_qid="Q123",
            )
        )
        repository.insert_mentions(
            [
                _resolved_mention(episode_id, "Andrej Karpathy", "person:andrej-karpathy"),
                _unresolvable_mention(episode_id, "Andrej"),
            ]
        )
        decisions = resolve_coreferences_for_episode(repository, episode_id)
        assert len(decisions) == 1
        assert decisions[0].decided_entity_id == "person:andrej-karpathy"
        assert decisions[0].status == ResolutionStatus.RESOLVED
        # Verify the mention was actually updated in the DB
        with sqlite3.connect(repository.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT entity_id, resolution_status, resolution_method "
                "FROM entity_mentions WHERE surface_form = 'Andrej' AND episode_id = ?",
                (episode_id,),
            ).fetchone()
        assert row["entity_id"] == "person:andrej-karpathy"
        assert row["resolution_status"] == "resolved"
        assert row["resolution_method"] == "coref"

    def test_marks_ambiguous_when_two_candidates(self, repo):
        repository, episode_id = repo
        repository.upsert_entity(
            EntityRecord(id="person:andrej-karpathy", type=EntityType.PERSON, canonical_name="Andrej Karpathy")
        )
        repository.upsert_entity(
            EntityRecord(id="person:andrej-sokolov", type=EntityType.PERSON, canonical_name="Andrej Sokolov")
        )
        repository.insert_mentions(
            [
                _resolved_mention(episode_id, "Andrej Karpathy", "person:andrej-karpathy", segment_id=1),
                _resolved_mention(episode_id, "Andrej Sokolov", "person:andrej-sokolov", segment_id=2),
                _unresolvable_mention(episode_id, "Andrej", segment_id=3),
            ]
        )
        decisions = resolve_coreferences_for_episode(repository, episode_id)
        assert len(decisions) == 1
        assert decisions[0].status == ResolutionStatus.AMBIGUOUS
        assert set(decisions[0].candidate_entity_ids) == {
            "person:andrej-karpathy",
            "person:andrej-sokolov",
        }

    def test_no_match_leaves_unresolvable(self, repo):
        repository, episode_id = repo
        repository.upsert_entity(
            EntityRecord(id="person:elon-musk", type=EntityType.PERSON, canonical_name="Elon Musk")
        )
        repository.insert_mentions(
            [
                _resolved_mention(episode_id, "Elon Musk", "person:elon-musk", segment_id=1),
                _unresolvable_mention(episode_id, "Karpathy", segment_id=2),  # no Karpathy in episode
            ]
        )
        decisions = resolve_coreferences_for_episode(repository, episode_id)
        assert decisions == []

    def test_does_not_cross_episodes(self, repo, tmp_path):
        # Even if a long-form anchor exists in a DIFFERENT episode, we
        # don't propagate it. Spec §1.13.5 hard rule.
        repository, episode_id = repo
        other_ep = str(uuid.uuid4())
        with sqlite3.connect(repository.db_path) as conn:
            conn.execute(
                "INSERT INTO episodes (id, podcast_id, external_id, title, audio_url, pub_date) "
                "SELECT ?, podcast_id, 'e2', 'Other', 'https://example.com/e2.mp3', '2026-04-29' "
                "FROM episodes WHERE id = ?",
                (other_ep, episode_id),
            )
            conn.commit()
        repository.upsert_entity(
            EntityRecord(id="person:andrej-karpathy", type=EntityType.PERSON, canonical_name="Andrej Karpathy")
        )
        # Long-form anchor lives in OTHER episode
        repository.insert_mentions(
            [_resolved_mention(other_ep, "Andrej Karpathy", "person:andrej-karpathy", segment_id=10)]
        )
        # Unresolved short-form lives in our test episode
        repository.insert_mentions([_unresolvable_mention(episode_id, "Andrej", segment_id=2)])

        decisions = resolve_coreferences_for_episode(repository, episode_id)
        assert decisions == []
