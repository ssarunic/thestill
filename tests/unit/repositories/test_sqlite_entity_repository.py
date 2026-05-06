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


class TestUpsertEntityAtomicity:
    """Spec #28 §1.5/§1.6 — upsert_entity must survive concurrent
    cascade-deletes from the inline alias-merge step.

    The pre-fix implementation did SELECT-then-INSERT-or-UPDATE; if
    another worker deleted the row between the SELECT and the INSERT,
    the second worker would INSERT a fresh row — fine on its own, but
    a subsequent ``resolve_mention`` could FK-fail if the entity got
    deleted again before the mention update committed. The fix uses a
    single atomic ``INSERT ... ON CONFLICT DO UPDATE``.
    """

    def test_insert_creates_a_new_row(self, seeded):
        tmp_db, _ = seeded
        repo = SqliteEntityRepository(db_path=str(tmp_db))
        from thestill.models.entities import EntityRecord, EntityType

        ent = EntityRecord(
            id="person:elon-musk",
            type=EntityType.PERSON,
            canonical_name="Elon Musk",
            wikidata_qid="Q317521",
            aliases=["Musk", "@elonmusk"],
        )
        returned_id = repo.upsert_entity(ent)
        assert returned_id == "person:elon-musk"
        roundtrip = repo.get_entity("person:elon-musk")
        assert roundtrip is not None
        assert roundtrip.canonical_name == "Elon Musk"
        assert sorted(roundtrip.aliases) == sorted(["Musk", "@elonmusk"])

    def test_repeat_upsert_unions_aliases_without_duplicates(self, seeded):
        tmp_db, _ = seeded
        repo = SqliteEntityRepository(db_path=str(tmp_db))
        from thestill.models.entities import EntityRecord, EntityType

        first = EntityRecord(
            id="company:openai",
            type=EntityType.COMPANY,
            canonical_name="OpenAI",
            aliases=["OAI"],
        )
        second = EntityRecord(
            id="company:openai",
            type=EntityType.COMPANY,
            canonical_name="OpenAI",
            aliases=["OAI", "Open AI"],  # one overlap, one new
        )
        repo.upsert_entity(first)
        repo.upsert_entity(second)
        # The aliases column should be the deduplicated union.
        roundtrip = repo.get_entity("company:openai")
        assert roundtrip is not None
        assert sorted(roundtrip.aliases) == ["OAI", "Open AI"]

    def test_upsert_succeeds_when_row_was_just_cascade_deleted(self, seeded):
        """Race regression: another worker just deleted the entity row
        (e.g. via _merge_qid_duplicates_for repointing then deleting
        the loser of a duplicate pair). The next upsert must re-create
        the row cleanly, not error out — and a subsequent FK-bound
        write that references it must succeed.
        """
        tmp_db, episode_id = seeded
        repo = SqliteEntityRepository(db_path=str(tmp_db))
        from thestill.models.entities import EntityRecord, EntityType

        ent = EntityRecord(
            id="person:scott-galloway",
            type=EntityType.PERSON,
            canonical_name="Scott Galloway",
            wikidata_qid="Q7437099",
        )
        repo.upsert_entity(ent)
        # Simulate the merge step: delete the row out from under us.
        assert repo.delete_entity("person:scott-galloway") is True
        # Re-upsert should succeed (no SELECT-then-INSERT race).
        repo.upsert_entity(ent)
        # And a downstream FK-bound write should land cleanly.
        repo.insert_mentions([_mention(episode_id, 1)])
        conn = sqlite3.connect(str(tmp_db))
        try:
            row = conn.execute(
                "SELECT id FROM entity_mentions WHERE episode_id = ?",
                (episode_id,),
            ).fetchone()
            mention_id = row["id"] if hasattr(row, "keys") else row[0]
        finally:
            conn.close()
        # This is the operation that would FK-fail if the entity row
        # were missing or in a half-state.
        ok = repo.resolve_mention(
            mention_id=mention_id,
            entity_id="person:scott-galloway",
            status="resolved",
        )
        assert ok is True

    def test_atomic_upsert_preserves_qid_when_caller_passes_none(self, seeded):
        """COALESCE behaviour: an upsert with wikidata_qid=None must
        not blank out a QID written by an earlier resolve.
        """
        tmp_db, _ = seeded
        repo = SqliteEntityRepository(db_path=str(tmp_db))
        from thestill.models.entities import EntityRecord, EntityType

        with_qid = EntityRecord(
            id="company:spacex",
            type=EntityType.COMPANY,
            canonical_name="SpaceX",
            wikidata_qid="Q193701",
        )
        without_qid = EntityRecord(
            id="company:spacex",
            type=EntityType.COMPANY,
            canonical_name="SpaceX",
            wikidata_qid=None,
        )
        repo.upsert_entity(with_qid)
        repo.upsert_entity(without_qid)
        roundtrip = repo.get_entity("company:spacex")
        assert roundtrip is not None
        assert roundtrip.wikidata_qid == "Q193701"
