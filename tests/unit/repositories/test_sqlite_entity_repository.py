"""Spec #28 §1.2 — entity repository write path round-trip.

Verifies ``insert_mentions``, ``delete_mentions_for_episode``, and
``count_mentions_for_episode`` against a real SQLite DB built by the
podcast repo's migration block. Resolved mentions, query methods,
and entity upsert remain stubs (later Phase 1 sub-tasks).
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from thestill.models.entities import EntityMention, MentionRole, ResolutionStatus
from thestill.repositories.sqlite_entity_repository import SqliteEntityRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository


@pytest.fixture
def tmp_db(tmp_path) -> Path:
    return tmp_path / "thestill.db"


@pytest.fixture
def seeded(tmp_db) -> tuple[Path, str]:
    """Stand up a DB with one podcast + one episode the FKs can point at."""
    SqlitePodcastRepository(db_path=str(tmp_db))
    podcast_id = str(uuid.uuid4())
    episode_id = str(uuid.uuid4())
    conn = sqlite3.connect(str(tmp_db))
    try:
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
            (podcast_id, "https://example.com/feed.xml", "Fixture", "fixture"),
        )
        conn.execute(
            "INSERT INTO episodes (id, podcast_id, external_id, title, audio_url) VALUES (?, ?, ?, ?, ?)",
            (episode_id, podcast_id, "e1", "Fixture Ep", "https://example.com/ep1.mp3"),
        )
        conn.commit()
    finally:
        conn.close()
    return tmp_db, episode_id


def _mention(episode_id: str, segment_id: int, surface_form: str = "Elon Musk") -> EntityMention:
    return EntityMention(
        episode_id=episode_id,
        segment_id=segment_id,
        start_ms=segment_id * 1000,
        end_ms=segment_id * 1000 + 5000,
        speaker="Scott Galloway",
        role=MentionRole.MENTIONED,
        surface_form=surface_form,
        quote_excerpt=f"Excerpt mentioning {surface_form}.",
        confidence=0.91,
        extractor="gliner:test",
    )


class TestInsertMentions:
    def test_round_trip_persists_all_fields(self, seeded):
        tmp_db, episode_id = seeded
        repo = SqliteEntityRepository(db_path=str(tmp_db))
        n = repo.insert_mentions([_mention(episode_id, 1), _mention(episode_id, 2, "Tesla")])
        assert n == 2
        assert repo.count_mentions_for_episode(episode_id) == 2

        conn = sqlite3.connect(str(tmp_db))
        rows = conn.execute(
            """SELECT entity_id, resolution_status, segment_id, surface_form,
                      role, confidence, extractor FROM entity_mentions
               WHERE episode_id = ? ORDER BY segment_id""",
            (episode_id,),
        ).fetchall()
        conn.close()

        assert len(rows) == 2
        assert rows[0] == (None, "pending", 1, "Elon Musk", "mentioned", 0.91, "gliner:test")
        assert rows[1] == (None, "pending", 2, "Tesla", "mentioned", 0.91, "gliner:test")

    def test_empty_input_is_noop(self, seeded):
        tmp_db, _ = seeded
        repo = SqliteEntityRepository(db_path=str(tmp_db))
        assert repo.insert_mentions([]) == 0


class TestDeleteMentions:
    def test_delete_wipes_only_target_episode(self, seeded):
        tmp_db, episode_id = seeded
        repo = SqliteEntityRepository(db_path=str(tmp_db))

        # Seed a second episode + its mentions.
        other_ep = str(uuid.uuid4())
        conn = sqlite3.connect(str(tmp_db))
        try:
            conn.execute(
                "INSERT INTO episodes (id, podcast_id, external_id, title, audio_url) VALUES (?, ?, ?, ?, ?)",
                (
                    other_ep,
                    conn.execute("SELECT podcast_id FROM episodes LIMIT 1").fetchone()[0],
                    "e2",
                    "Other",
                    "https://example.com/ep2.mp3",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        repo.insert_mentions([_mention(episode_id, 1), _mention(episode_id, 2)])
        repo.insert_mentions([_mention(other_ep, 7)])

        deleted = repo.delete_mentions_for_episode(episode_id)
        assert deleted == 2
        assert repo.count_mentions_for_episode(episode_id) == 0
        assert repo.count_mentions_for_episode(other_ep) == 1

    def test_delete_on_empty_is_zero(self, seeded):
        tmp_db, episode_id = seeded
        repo = SqliteEntityRepository(db_path=str(tmp_db))
        assert repo.delete_mentions_for_episode(episode_id) == 0


class TestRoleNullability:
    def test_role_none_round_trips_as_null(self, seeded):
        tmp_db, episode_id = seeded
        repo = SqliteEntityRepository(db_path=str(tmp_db))
        m = _mention(episode_id, 1)
        m.role = None
        repo.insert_mentions([m])
        conn = sqlite3.connect(str(tmp_db))
        row = conn.execute(
            "SELECT role FROM entity_mentions WHERE episode_id = ? LIMIT 1",
            (episode_id,),
        ).fetchone()
        conn.close()
        assert row[0] is None
