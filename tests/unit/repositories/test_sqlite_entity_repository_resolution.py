"""Spec #28 §1.5–1.7 — entity repository resolution + cooccurrence + alias paths.

Round-trips ``upsert_entity``, ``get_entity``, ``find_entity_by_qid``,
``list_pending_mentions``, ``resolve_mention``,
``rebuild_cooccurrences``, ``find_duplicate_qid_pairs``,
``repoint_mentions``, ``delete_entity``, and
``list_entities_by_type`` against a real SQLite DB.

Mention insert tests live in ``test_sqlite_entity_repository.py``;
this file covers what Phase 1.5–1.7 added.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from thestill.models.entities import EntityMention, EntityRecord, EntityType, MentionRole, ResolutionStatus
from thestill.repositories.sqlite_entity_repository import SqliteEntityRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository


@pytest.fixture
def tmp_db(tmp_path) -> Path:
    return tmp_path / "thestill.db"


@pytest.fixture
def seeded(tmp_db) -> tuple[Path, str, str]:
    """DB with one podcast + two episodes."""
    SqlitePodcastRepository(db_path=str(tmp_db))
    podcast_id = str(uuid.uuid4())
    ep1 = str(uuid.uuid4())
    ep2 = str(uuid.uuid4())
    conn = sqlite3.connect(str(tmp_db))
    try:
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
            (podcast_id, "https://example.com/feed.xml", "Fixture", "fixture"),
        )
        for eid, ext in ((ep1, "e1"), (ep2, "e2")):
            conn.execute(
                "INSERT INTO episodes (id, podcast_id, external_id, title, audio_url) " "VALUES (?, ?, ?, ?, ?)",
                (eid, podcast_id, ext, f"Ep {ext}", f"https://example.com/{ext}.mp3"),
            )
        conn.commit()
    finally:
        conn.close()
    return tmp_db, ep1, ep2


def _entity(id_: str, *, type_: EntityType = EntityType.PERSON, qid: str | None = None, aliases=None):
    return EntityRecord(
        id=id_,
        type=type_,
        canonical_name=id_.split(":", 1)[1].replace("-", " ").title(),
        wikidata_qid=qid,
        aliases=list(aliases or []),
    )


def _mention(
    episode_id: str,
    segment_id: int,
    surface: str = "Elon Musk",
    *,
    surface_label: str = "person",
) -> EntityMention:
    return EntityMention(
        episode_id=episode_id,
        segment_id=segment_id,
        start_ms=segment_id * 1000,
        end_ms=segment_id * 1000 + 5000,
        speaker="Host",
        role=MentionRole.MENTIONED,
        surface_form=surface,
        surface_label=surface_label,
        quote_excerpt=f"… mentioning {surface} …",
        confidence=0.9,
        extractor="gliner:test",
    )


class TestUpsertEntity:
    def test_insert_then_idempotent_update(self, seeded):
        tmp_db, _, _ = seeded
        repo = SqliteEntityRepository(db_path=str(tmp_db))

        eid = repo.upsert_entity(_entity("person:elon-musk", qid="Q317521", aliases=["Musk"]))
        assert eid == "person:elon-musk"

        round1 = repo.get_entity("person:elon-musk")
        assert round1.canonical_name == "Elon Musk"
        assert round1.aliases == ["Musk"]
        assert round1.wikidata_qid == "Q317521"

        # Re-upsert with new alias — existing aliases preserved (union)
        repo.upsert_entity(_entity("person:elon-musk", qid="Q317521", aliases=["@elonmusk"]))
        round2 = repo.get_entity("person:elon-musk")
        assert set(round2.aliases) == {"Musk", "@elonmusk"}

    def test_qid_lookup(self, seeded):
        tmp_db, _, _ = seeded
        repo = SqliteEntityRepository(db_path=str(tmp_db))
        repo.upsert_entity(_entity("person:elon-musk", qid="Q317521"))
        found = repo.find_entity_by_qid("Q317521")
        assert found is not None
        assert found.id == "person:elon-musk"
        assert repo.find_entity_by_qid("Q999") is None


class TestPendingAndResolve:
    def test_list_pending_mentions_filters_by_episode(self, seeded):
        tmp_db, ep1, ep2 = seeded
        repo = SqliteEntityRepository(db_path=str(tmp_db))
        repo.insert_mentions([_mention(ep1, 1), _mention(ep1, 2), _mention(ep2, 1)])

        ep1_pending = repo.list_pending_mentions(episode_id=ep1)
        ep2_pending = repo.list_pending_mentions(episode_id=ep2)
        all_pending = repo.list_pending_mentions()
        assert len(ep1_pending) == 2
        assert len(ep2_pending) == 1
        assert len(all_pending) == 3

    def test_resolve_mention_flips_status(self, seeded):
        tmp_db, ep1, _ = seeded
        repo = SqliteEntityRepository(db_path=str(tmp_db))
        repo.upsert_entity(_entity("person:elon-musk", qid="Q317521"))
        repo.insert_mentions([_mention(ep1, 1)])
        pending = repo.list_pending_mentions(episode_id=ep1)
        assert len(pending) == 1
        ok = repo.resolve_mention(
            mention_id=pending[0].id,
            entity_id="person:elon-musk",
            status="resolved",
        )
        assert ok is True
        assert repo.list_pending_mentions(episode_id=ep1) == []

    def test_resolve_mention_unresolvable(self, seeded):
        tmp_db, ep1, _ = seeded
        repo = SqliteEntityRepository(db_path=str(tmp_db))
        repo.insert_mentions([_mention(ep1, 1, "Whoever")])
        pending = repo.list_pending_mentions(episode_id=ep1)
        ok = repo.resolve_mention(
            mention_id=pending[0].id,
            entity_id=None,
            status="unresolvable",
        )
        assert ok is True

    def test_resolve_mention_rejects_invalid_status(self, seeded):
        tmp_db, ep1, _ = seeded
        repo = SqliteEntityRepository(db_path=str(tmp_db))
        repo.insert_mentions([_mention(ep1, 1)])
        pending = repo.list_pending_mentions(episode_id=ep1)
        with pytest.raises(ValueError):
            repo.resolve_mention(
                mention_id=pending[0].id,
                entity_id="person:foo",
                status="bogus",
            )


class TestCooccurrenceRebuild:
    def _seed_two_resolved_pair(self, tmp_db, ep1, ep2):
        repo = SqliteEntityRepository(db_path=str(tmp_db))
        # Two entities co-occur in ep1 and only one of them in ep2.
        for eid in ("person:musk", "company:tesla", "company:openai"):
            type_ = EntityType.PERSON if eid.startswith("person:") else EntityType.COMPANY
            repo.upsert_entity(_entity(eid, type_=type_, qid=eid.split(":")[1].upper()))
        # ep1: musk + tesla
        # ep2: musk + openai
        repo.insert_mentions(
            [
                _mention(ep1, 1, "Musk"),
                _mention(ep1, 2, "Tesla"),
                _mention(ep2, 1, "Musk"),
                _mention(ep2, 2, "OpenAI"),
            ]
        )
        for m in repo.list_pending_mentions():
            entity_id = {
                "Musk": "person:musk",
                "Tesla": "company:tesla",
                "OpenAI": "company:openai",
            }[m.surface_form]
            repo.resolve_mention(mention_id=m.id, entity_id=entity_id, status="resolved")
        return repo

    def test_full_rebuild_materialises_pair_per_episode(self, seeded):
        tmp_db, ep1, ep2 = seeded
        repo = self._seed_two_resolved_pair(tmp_db, ep1, ep2)
        n = repo.rebuild_cooccurrences(episode_ids=None)
        assert n == 2  # (musk, tesla) and (musk, openai)

        conn = sqlite3.connect(str(tmp_db))
        rows = conn.execute(
            "SELECT entity_a_id, entity_b_id, episode_count FROM entity_cooccurrences "
            "ORDER BY entity_a_id, entity_b_id"
        ).fetchall()
        conn.close()
        assert len(rows) == 2
        # Canonical (a < b) ordering per the CHECK
        for a, b, _count in rows:
            assert a < b

    def test_episode_scoped_rebuild_recomputes_corpus_count(self, seeded):
        # If ep1 already has resolved mentions but the cooccurrences
        # table is empty (or stale), an episode-scoped rebuild for
        # only ep2 should still produce corpus-wide counts.
        tmp_db, ep1, ep2 = seeded
        repo = self._seed_two_resolved_pair(tmp_db, ep1, ep2)
        # Scope to ep2 only — pairs touching its entities (musk +
        # openai) should still pick up ep1's musk-tesla via the
        # entity-set widening described in the rebuild docstring.
        repo.rebuild_cooccurrences(episode_ids=[ep2])
        conn = sqlite3.connect(str(tmp_db))
        pairs = {
            (r[0], r[1]): r[2]
            for r in conn.execute("SELECT entity_a_id, entity_b_id, episode_count FROM entity_cooccurrences").fetchall()
        }
        conn.close()
        # Both pairs touching musk should be present after the rebuild
        keys = set(pairs.keys())
        assert ("company:openai", "person:musk") in keys or ("person:musk", "company:openai") in keys
        # When scoped to ep2's entities {musk, openai}, the affected
        # set widens via the entity-touching predicate to include
        # any pair where musk is one side — which catches musk-tesla.
        assert any("tesla" in pair[0] or "tesla" in pair[1] for pair in keys)

    def test_aggregate_runs_outside_the_write_transaction(self, seeded):
        """The expensive self-join must NOT hold the WAL writer lock.

        Pins the contention fix: phase 1 computes the pair aggregate in a
        read connection that is fully closed *before* phase 2 opens the
        write connection for the DELETE + bulk INSERT. We spy on
        ``_get_connection`` to assert the connection lifecycle and that no
        write statement is issued on the read connection.
        """
        from contextlib import contextmanager

        tmp_db, ep1, ep2 = seeded
        repo = self._seed_two_resolved_pair(tmp_db, ep1, ep2)

        events: list[tuple] = []
        orig = repo._get_connection

        class _ProxyConn:
            def __init__(self, real, idx):
                self._real = real
                self._idx = idx

            def _record(self, sql):
                events.append(("exec", self._idx, sql.strip().split()[0].upper()))

            def execute(self, sql, *a, **k):
                self._record(sql)
                return self._real.execute(sql, *a, **k)

            def executemany(self, sql, *a, **k):
                self._record(sql)
                return self._real.executemany(sql, *a, **k)

            def __getattr__(self, name):
                return getattr(self._real, name)

        @contextmanager
        def spy():
            idx = sum(1 for e in events if e[0] == "open")
            events.append(("open", idx))
            try:
                with orig() as real:
                    yield _ProxyConn(real, idx)
            finally:
                events.append(("close", idx))

        repo._get_connection = spy
        try:
            repo.rebuild_cooccurrences(episode_ids=[ep2])
        finally:
            repo._get_connection = orig

        # Exactly two connections, strictly sequential: read phase (0)
        # closes before write phase (1) opens — no overlap, so the
        # aggregate never coexists with the writer lock.
        lifecycle = [e for e in events if e[0] in ("open", "close")]
        assert lifecycle == [("open", 0), ("close", 0), ("open", 1), ("close", 1)]

        writes_on_read_conn = [
            e for e in events if e[0] == "exec" and e[1] == 0 and e[2] in {"INSERT", "DELETE", "UPDATE"}
        ]
        assert writes_on_read_conn == [], "read/aggregate phase must not write"

        writes_on_write_conn = [e[2] for e in events if e[0] == "exec" and e[1] == 1]
        assert "DELETE" in writes_on_write_conn and "INSERT" in writes_on_write_conn

    def test_scoped_rebuild_clears_stale_rows_with_no_new_pairs(self, seeded):
        """A scoped rebuild whose entities yield no pairs still DELETEs.

        Guards the ``if insert_values:`` guard around the INSERT: the
        DELETE must run unconditionally so stale cooccurrence rows for
        the affected entities are removed even when the aggregate yields
        nothing to replace them.
        """
        tmp_db, ep1, ep2 = seeded
        repo = self._seed_two_resolved_pair(tmp_db, ep1, ep2)

        # A third episode whose only resolved mention is a brand-new
        # entity that co-occurs with nothing → affected set {solo},
        # zero new pairs.
        ep3 = str(uuid.uuid4())
        conn = sqlite3.connect(str(tmp_db))
        try:
            podcast_id = conn.execute("SELECT id FROM podcasts LIMIT 1").fetchone()[0]
            conn.execute(
                "INSERT INTO episodes (id, podcast_id, external_id, title, audio_url) VALUES (?, ?, ?, ?, ?)",
                (ep3, podcast_id, "e3", "Ep e3", "https://example.com/e3.mp3"),
            )
            # A pre-existing stale cooccurrence row touching solo that the
            # scoped DELETE must remove (a < b per the CHECK constraint).
            conn.execute(
                "INSERT INTO entity_cooccurrences (entity_a_id, entity_b_id, episode_count, last_seen_at) "
                "VALUES (?, ?, ?, ?)",
                ("person:solo", "zzz:ghost", 1, "2020-01-01T00:00:00+00:00"),
            )
            conn.commit()
        finally:
            conn.close()

        repo.upsert_entity(_entity("person:solo", type_=EntityType.PERSON, qid="SOLO"))
        repo.insert_mentions([_mention(ep3, 1, "Solo")])
        for m in repo.list_pending_mentions(episode_id=ep3):
            repo.resolve_mention(mention_id=m.id, entity_id="person:solo", status="resolved")

        n = repo.rebuild_cooccurrences(episode_ids=[ep3])

        assert n == 0  # solo forms no pair → nothing inserted
        conn = sqlite3.connect(str(tmp_db))
        try:
            stale = conn.execute(
                "SELECT 1 FROM entity_cooccurrences WHERE entity_a_id = ? OR entity_b_id = ?",
                ("person:solo", "person:solo"),
            ).fetchall()
        finally:
            conn.close()
        assert stale == [], "unconditional DELETE must clear stale rows touching the affected entity"


class TestAliasMergeHelpers:
    def test_find_duplicate_qid_pairs(self, seeded):
        tmp_db, _, _ = seeded
        repo = SqliteEntityRepository(db_path=str(tmp_db))
        repo.upsert_entity(_entity("person:elon-musk", qid="Q317521"))
        repo.upsert_entity(_entity("person:musk", qid="Q317521"))
        repo.upsert_entity(_entity("company:tesla", qid="Q478214"))

        pairs = repo.find_duplicate_qid_pairs()
        assert len(pairs) == 1
        qid, keeper, loser = pairs[0]
        assert qid == "Q317521"
        # Both entries are type=person with 0 mentions, so the
        # tiebreak is id ASC — "person:elon-musk" wins.
        assert keeper == "person:elon-musk"
        assert loser == "person:musk"

    def test_find_duplicate_qid_pairs_prefers_mention_majority(self, seeded):
        """The Donald Trump case: ``company:donald-trump`` and
        ``person:donald-trump`` share QID Q22686, but the person row
        has hundreds of mentions and the company row only a handful.
        Mention count is the primary keeper signal — person wins.
        """
        tmp_db, ep1, _ = seeded
        repo = SqliteEntityRepository(db_path=str(tmp_db))
        repo.upsert_entity(_entity("company:donald-trump", type_=EntityType.COMPANY, qid="Q22686"))
        repo.upsert_entity(_entity("person:donald-trump", type_=EntityType.PERSON, qid="Q22686"))
        # Five person mentions, one company mention — majority is person.
        for i in range(5):
            repo.insert_mentions([_mention(ep1, i + 1, "Trump")])
        repo.insert_mentions([_mention(ep1, 6, "Trump administration")])
        pendings = repo.list_pending_mentions(episode_id=ep1)
        for m in pendings[:5]:
            repo.resolve_mention(mention_id=m.id, entity_id="person:donald-trump", status="resolved")
        repo.resolve_mention(mention_id=pendings[5].id, entity_id="company:donald-trump", status="resolved")

        pairs = repo.find_duplicate_qid_pairs()
        assert len(pairs) == 1
        qid, keeper, loser = pairs[0]
        assert qid == "Q22686"
        assert keeper == "person:donald-trump"
        assert loser == "company:donald-trump"

    def test_find_duplicate_qid_pairs_type_priority_tiebreak(self, seeded):
        """When mention counts tie, type priority kicks in:
        person > company > product > topic.
        """
        tmp_db, _, _ = seeded
        repo = SqliteEntityRepository(db_path=str(tmp_db))
        repo.upsert_entity(_entity("topic:ethereum", type_=EntityType.TOPIC, qid="Q16783523"))
        repo.upsert_entity(_entity("company:ethereum", type_=EntityType.COMPANY, qid="Q16783523"))

        pairs = repo.find_duplicate_qid_pairs()
        assert len(pairs) == 1
        _, keeper, loser = pairs[0]
        assert keeper == "company:ethereum"
        assert loser == "topic:ethereum"

    def test_repoint_mentions_then_delete_entity(self, seeded):
        tmp_db, ep1, _ = seeded
        repo = SqliteEntityRepository(db_path=str(tmp_db))
        repo.upsert_entity(_entity("person:elon-musk", qid="Q317521"))
        repo.upsert_entity(_entity("person:musk", qid="Q317521"))
        repo.insert_mentions([_mention(ep1, 1, "Musk")])
        # Resolve to the loser
        m_id = repo.list_pending_mentions(episode_id=ep1)[0].id
        repo.resolve_mention(mention_id=m_id, entity_id="person:musk", status="resolved")

        moved = repo.repoint_mentions(from_entity_id="person:musk", to_entity_id="person:elon-musk")
        assert moved == 1
        deleted = repo.delete_entity("person:musk")
        assert deleted is True
        assert repo.get_entity("person:musk") is None

    def test_list_entities_by_type(self, seeded):
        tmp_db, _, _ = seeded
        repo = SqliteEntityRepository(db_path=str(tmp_db))
        repo.upsert_entity(_entity("person:a", type_=EntityType.PERSON))
        repo.upsert_entity(_entity("person:b", type_=EntityType.PERSON))
        repo.upsert_entity(_entity("company:c", type_=EntityType.COMPANY))
        people = repo.list_entities_by_type("person")
        assert {p.id for p in people} == {"person:a", "person:b"}
        companies = repo.list_entities_by_type("company")
        assert {c.id for c in companies} == {"company:c"}


class TestFindMistypedEntities:
    """Spec #28 §1.6 follow-up — relabel entities whose stored type
    disagrees with the majority surface_label of their mentions.
    """

    def _resolve_all(self, repo, episode_id, entity_id):
        for m in repo.list_pending_mentions(episode_id=episode_id):
            repo.resolve_mention(mention_id=m.id, entity_id=entity_id, status="resolved")

    def test_flags_entity_when_majority_label_disagrees(self, seeded):
        tmp_db, ep1, _ = seeded
        repo = SqliteEntityRepository(db_path=str(tmp_db))
        repo.upsert_entity(_entity("company:donald-trump", type_=EntityType.COMPANY, qid="Q22686"))
        # 5 person-labeled mentions, 1 company-labeled — the entity is
        # stored as ``company`` but should be ``person``.
        for i in range(5):
            repo.insert_mentions([_mention(ep1, i + 1, "Trump", surface_label="person")])
        repo.insert_mentions([_mention(ep1, 6, "Trump administration", surface_label="company")])
        self._resolve_all(repo, ep1, "company:donald-trump")

        out = repo.find_mistyped_entities(min_mentions=3, min_majority_ratio=0.6)
        assert len(out) == 1
        entity_id, current, suggested, top_count, total = out[0]
        assert entity_id == "company:donald-trump"
        assert current == "company"
        assert suggested == "person"
        assert top_count == 5
        assert total == 6

    def test_skips_when_majority_below_ratio(self, seeded):
        tmp_db, ep1, _ = seeded
        repo = SqliteEntityRepository(db_path=str(tmp_db))
        repo.upsert_entity(_entity("topic:ambiguous", type_=EntityType.TOPIC, qid="Q1"))
        # Roughly 50/50 — no clear majority, leave the type alone.
        for i in range(3):
            repo.insert_mentions([_mention(ep1, i + 1, "ambiguous", surface_label="person")])
        for i in range(3):
            repo.insert_mentions([_mention(ep1, 10 + i, "ambiguous", surface_label="company")])
        self._resolve_all(repo, ep1, "topic:ambiguous")

        out = repo.find_mistyped_entities(min_mentions=3, min_majority_ratio=0.6)
        assert out == []

    def test_skips_when_below_min_mentions(self, seeded):
        tmp_db, ep1, _ = seeded
        repo = SqliteEntityRepository(db_path=str(tmp_db))
        repo.upsert_entity(_entity("company:lone", type_=EntityType.COMPANY, qid="Q2"))
        repo.insert_mentions([_mention(ep1, 1, "Lone", surface_label="person")])
        repo.insert_mentions([_mention(ep1, 2, "Lone", surface_label="person")])
        self._resolve_all(repo, ep1, "company:lone")

        out = repo.find_mistyped_entities(min_mentions=3, min_majority_ratio=0.6)
        assert out == []

    def test_skips_when_already_correctly_typed(self, seeded):
        tmp_db, ep1, _ = seeded
        repo = SqliteEntityRepository(db_path=str(tmp_db))
        repo.upsert_entity(_entity("person:elon-musk", type_=EntityType.PERSON, qid="Q317521"))
        for i in range(5):
            repo.insert_mentions([_mention(ep1, i + 1, "Musk", surface_label="person")])
        self._resolve_all(repo, ep1, "person:elon-musk")

        out = repo.find_mistyped_entities(min_mentions=3, min_majority_ratio=0.6)
        assert out == []
