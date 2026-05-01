"""Spec #28 §1.13.7 — mention_overrides + resolution_blacklist persistence."""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from thestill.models.entities import EntityRecord, EntityType
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
    repository = SqliteEntityRepository(db_path=str(db_path))
    repository.upsert_entity(
        EntityRecord(
            id="topic:ai-labs",
            type=EntityType.TOPIC,
            canonical_name="AI labs",
        )
    )
    return repository, episode_id


class TestOverrides:
    def test_global_override_matches_any_episode(self, repo):
        repository, _ = repo
        repository.add_override(
            surface_form="frontier labs",
            episode_id=None,
            kind="force_entity",
            entity_id="topic:ai-labs",
            reason="generic AI lab phrasing",
        )
        match = repository.lookup_override("frontier labs", "ep-1")
        assert match is not None
        assert match["override_kind"] == "force_entity"
        assert match["entity_id"] == "topic:ai-labs"

    def test_episode_scoped_wins_over_global(self, repo):
        repository, episode_id = repo
        repository.add_override(
            surface_form="Adam",
            episode_id=None,
            kind="drop",
            reason="too generic globally",
        )
        repository.add_override(
            surface_form="Adam",
            episode_id=episode_id,
            kind="force_entity",
            entity_id="topic:ai-labs",
            reason="this episode has Adam-the-CEO",
        )
        match = repository.lookup_override("Adam", episode_id)
        assert match["override_kind"] == "force_entity"

    def test_case_insensitive_lookup(self, repo):
        repository, _ = repo
        repository.add_override(surface_form="Frontier Labs", episode_id=None, kind="drop")
        assert repository.lookup_override("FRONTIER LABS", None) is not None
        assert repository.lookup_override("frontier labs", None) is not None

    def test_unknown_surface_returns_none(self, repo):
        repository, _ = repo
        assert repository.lookup_override("never seen", None) is None

    def test_force_entity_requires_entity_id(self, repo):
        repository, _ = repo
        with pytest.raises(ValueError):
            repository.add_override(surface_form="x", episode_id=None, kind="force_entity", entity_id=None)


class TestBlacklist:
    def test_add_and_lookup(self, repo):
        repository, _ = repo
        repository.add_blacklist_entry(surface_form="Vercel", wrong_qid="Q-bad", reason="French village")
        assert repository.is_blacklisted("Vercel", "Q-bad") is True
        assert repository.is_blacklisted("Vercel", "Q-good") is False
        assert repository.is_blacklisted("Some Other", "Q-bad") is False

    def test_case_insensitive_surface(self, repo):
        repository, _ = repo
        repository.add_blacklist_entry(surface_form="Vercel", wrong_qid="Q-bad")
        assert repository.is_blacklisted("vercel", "Q-bad") is True
        assert repository.is_blacklisted("VERCEL", "Q-bad") is True

    def test_duplicate_add_is_noop(self, repo):
        repository, _ = repo
        repository.add_blacklist_entry(surface_form="X", wrong_qid="Q1")
        repository.add_blacklist_entry(surface_form="X", wrong_qid="Q1")
        rows = repository.list_blacklist()
        assert len(rows) == 1
