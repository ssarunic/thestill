"""Spec #45 Tier 0 — entity_enrichment persistence + read-path tests.

Verifies ``upsert_enrichment`` / ``get_enrichment`` round-trip, the
``entity_ids_needing_enrichment`` staleness gating, reindex survival
(enrichment is keyed by entity, not episode), and that
``get_entity_summary`` surfaces enrichment + ``most_discussed_on``.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from thestill.models.enrichment import EnrichmentStatus, EntityAffiliation, EntityEnrichment, EntityFact
from thestill.models.entities import EntityRecord, EntityType
from thestill.repositories.sqlite_entity_repository import SqliteEntityRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository


@pytest.fixture
def repo_env(tmp_path) -> tuple[SqliteEntityRepository, dict]:
    """A migrated DB with two podcasts/episodes and one QID entity with
    resolved mentions skewed toward the first podcast."""
    db = tmp_path / "thestill.db"
    SqlitePodcastRepository(db_path=str(db))
    repo = SqliteEntityRepository(db_path=str(db))

    ids = {"p1": str(uuid.uuid4()), "p2": str(uuid.uuid4()), "e1": str(uuid.uuid4()), "e2": str(uuid.uuid4())}
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
            (ids["p1"], "https://a.example/feed", "Podcast A", "podcast-a"),
        )
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
            (ids["p2"], "https://b.example/feed", "Podcast B", "podcast-b"),
        )
        conn.execute(
            "INSERT INTO episodes (id, podcast_id, external_id, title, audio_url) VALUES (?, ?, ?, ?, ?)",
            (ids["e1"], ids["p1"], "a1", "A Ep", "https://a.example/1.mp3"),
        )
        conn.execute(
            "INSERT INTO episodes (id, podcast_id, external_id, title, audio_url) VALUES (?, ?, ?, ?, ?)",
            (ids["e2"], ids["p2"], "b1", "B Ep", "https://b.example/1.mp3"),
        )
        conn.commit()
    finally:
        conn.close()

    repo.upsert_entity(
        EntityRecord(id="person:elon-musk", type=EntityType.PERSON, canonical_name="Elon Musk", wikidata_qid="Q317521")
    )
    # 3 resolved mentions on episode e1 (podcast A), 1 on e2 (podcast B).
    _seed_resolved_mentions(db, "person:elon-musk", ids["e1"], 3)
    _seed_resolved_mentions(db, "person:elon-musk", ids["e2"], 1)
    return repo, ids


def _seed_resolved_mentions(db: Path, entity_id: str, episode_id: str, count: int) -> None:
    conn = sqlite3.connect(str(db))
    try:
        for seg in range(count):
            conn.execute(
                """
                INSERT INTO entity_mentions (
                    entity_id, resolution_status, episode_id, segment_id,
                    start_ms, end_ms, surface_form, quote_excerpt, confidence, extractor
                ) VALUES (?, 'resolved', ?, ?, ?, ?, 'Elon Musk', '...Elon Musk...', 0.9, 'test')
                """,
                (entity_id, episode_id, seg, seg * 1000, seg * 1000 + 500),
            )
        conn.commit()
    finally:
        conn.close()


def _sample_enrichment(entity_id: str = "person:elon-musk", **overrides) -> EntityEnrichment:
    base = dict(
        entity_id=entity_id,
        image_url="https://commons.wikimedia.org/wiki/Special:FilePath/Musk.jpg?width=400",
        image_attribution="Wikimedia Commons",
        headline="business magnate",
        wikipedia_extract="Elon Musk is a businessman.",
        wikipedia_url="https://en.wikipedia.org/wiki/Elon_Musk",
        facts=[
            EntityFact(label="Born", value="June 28, 1971"),
            EntityFact(label="Website", value="x.com", url="https://x.com"),
        ],
        affiliations=[
            EntityAffiliation(
                qid="Q478214", label="Tesla", relation="Works at", entity_id="company:tesla", entity_type="company"
            )
        ],
        wikidata_status=EnrichmentStatus.OK,
        wikidata_fetched_at=datetime.now(timezone.utc),
        wikipedia_status=EnrichmentStatus.OK,
        wikipedia_fetched_at=datetime.now(timezone.utc),
    )
    base.update(overrides)
    return EntityEnrichment(**base)


class TestRoundTrip:
    def test_upsert_then_get(self, repo_env):
        repo, _ = repo_env
        repo.upsert_enrichment(_sample_enrichment())
        got = repo.get_enrichment("person:elon-musk")
        assert got is not None
        assert got.headline == "business magnate"
        assert got.image_url.endswith("?width=400")
        assert got.wikidata_status == EnrichmentStatus.OK
        assert {f.label for f in got.facts} == {"Born", "Website"}
        assert got.affiliations[0].entity_id == "company:tesla"
        assert got.wikidata_fetched_at is not None

    def test_get_missing_returns_none(self, repo_env):
        repo, _ = repo_env
        assert repo.get_enrichment("person:nobody") is None

    def test_failed_reupsert_preserves_prior_content(self, repo_env):
        # Spec #42 FM-1: a re-run that FAILED transiently must not wipe the
        # image/facts a previous successful run committed. Only the status,
        # timestamps, and retry_after advance.
        repo, _ = repo_env
        repo.upsert_enrichment(_sample_enrichment())  # OK run with image + facts

        failed = EntityEnrichment(
            entity_id="person:elon-musk",
            image_url=None,
            headline=None,
            facts=[],
            affiliations=[],
            wikidata_status=EnrichmentStatus.FAILED,
            retry_after=datetime.now(timezone.utc) + timedelta(hours=6),
            wikipedia_status=EnrichmentStatus.PENDING,
        )
        repo.upsert_enrichment(failed)

        got = repo.get_enrichment("person:elon-musk")
        # Content survives...
        assert got.image_url is not None and "Musk.jpg" in got.image_url
        assert got.headline == "business magnate"
        assert {f.label for f in got.facts} == {"Born", "Website"}
        # ...but the failure + retry window are recorded.
        assert got.wikidata_status == EnrichmentStatus.FAILED
        assert got.retry_after is not None

    def test_wikipedia_image_preserved_when_wikipedia_fails(self, repo_env):
        # Image owned by Wikipedia (Wikidata had no photo). A later run where
        # Wikidata is OK but Wikipedia transiently FAILS must not wipe it.
        repo, _ = repo_env
        repo.upsert_enrichment(
            EntityEnrichment(
                entity_id="person:elon-musk",
                image_url="https://upload.wikimedia.org/wp.jpg",
                image_attribution="Wikipedia",
                headline="businessman",
                wikidata_status=EnrichmentStatus.OK,
                wikipedia_status=EnrichmentStatus.OK,
            )
        )
        repo.upsert_enrichment(
            EntityEnrichment(
                entity_id="person:elon-musk",
                image_url=None,
                headline="businessman",
                wikidata_status=EnrichmentStatus.OK,
                wikipedia_status=EnrichmentStatus.FAILED,
                retry_after=datetime.now(timezone.utc) + timedelta(hours=6),
            )
        )
        got = repo.get_enrichment("person:elon-musk")
        assert got.image_url == "https://upload.wikimedia.org/wp.jpg"
        assert got.image_attribution == "Wikipedia"

    def test_empty_reupsert_clears_stale_content(self, repo_env):
        # EMPTY (fetched OK, genuinely nothing) SHOULD clear stale content —
        # only FAILED preserves it.
        repo, _ = repo_env
        repo.upsert_enrichment(_sample_enrichment())
        empty = EntityEnrichment(
            entity_id="person:elon-musk",
            wikidata_status=EnrichmentStatus.EMPTY,
            wikipedia_status=EnrichmentStatus.EMPTY,
        )
        repo.upsert_enrichment(empty)
        got = repo.get_enrichment("person:elon-musk")
        assert got.image_url is None
        assert got.headline is None
        assert got.facts == []

    def test_upsert_preserves_created_at_and_updates_content(self, repo_env):
        repo, _ = repo_env
        first = _sample_enrichment()
        repo.upsert_enrichment(first)
        original_created = repo.get_enrichment("person:elon-musk").created_at

        updated = _sample_enrichment(headline="updated headline")
        repo.upsert_enrichment(updated)
        got = repo.get_enrichment("person:elon-musk")
        assert got.headline == "updated headline"
        assert got.created_at == original_created  # created_at not clobbered


class TestNeedingEnrichment:
    def test_new_entity_needs_enrichment(self, repo_env):
        repo, _ = repo_env
        assert "person:elon-musk" in repo.entity_ids_needing_enrichment(max_age_days=30)

    def test_fresh_row_does_not_need(self, repo_env):
        repo, _ = repo_env
        repo.upsert_enrichment(_sample_enrichment())
        assert "person:elon-musk" not in repo.entity_ids_needing_enrichment(schema_version=1, max_age_days=30)

    def test_schema_bump_marks_stale(self, repo_env):
        repo, _ = repo_env
        repo.upsert_enrichment(_sample_enrichment())
        assert "person:elon-musk" in repo.entity_ids_needing_enrichment(schema_version=2, max_age_days=30)

    def test_failed_with_elapsed_retry_after_needs(self, repo_env):
        repo, _ = repo_env
        repo.upsert_enrichment(
            _sample_enrichment(
                wikidata_status=EnrichmentStatus.FAILED,
                retry_after=datetime.now(timezone.utc) - timedelta(hours=1),
            )
        )
        assert "person:elon-musk" in repo.entity_ids_needing_enrichment(schema_version=1, max_age_days=30)

    def test_failed_with_future_retry_after_waits(self, repo_env):
        repo, _ = repo_env
        repo.upsert_enrichment(
            _sample_enrichment(
                wikidata_status=EnrichmentStatus.FAILED,
                retry_after=datetime.now(timezone.utc) + timedelta(hours=6),
            )
        )
        assert "person:elon-musk" not in repo.entity_ids_needing_enrichment(schema_version=1, max_age_days=30)

    def test_force_returns_everything_in_scope(self, repo_env):
        repo, _ = repo_env
        repo.upsert_enrichment(_sample_enrichment())  # fresh
        assert "person:elon-musk" in repo.entity_ids_needing_enrichment(force=True)

    def test_entity_without_qid_is_never_selected(self, repo_env):
        repo, _ = repo_env
        repo.upsert_entity(EntityRecord(id="person:local", type=EntityType.PERSON, canonical_name="Local"))
        assert "person:local" not in repo.entity_ids_needing_enrichment(force=True)

    def test_entity_id_scopes_to_one(self, repo_env):
        repo, _ = repo_env
        repo.upsert_entity(
            EntityRecord(id="person:other", type=EntityType.PERSON, canonical_name="Other", wikidata_qid="Q999")
        )
        ids = repo.entity_ids_needing_enrichment(entity_id="person:elon-musk", force=True)
        assert ids == ["person:elon-musk"]


class TestReindexSurvival:
    def test_enrichment_survives_mention_wipe(self, repo_env):
        repo, ids = repo_env
        repo.upsert_enrichment(_sample_enrichment())
        # The reindex path wipes per-episode mentions; enrichment is keyed
        # by entity_id and must be untouched (like mention_overrides).
        repo.delete_mentions_for_episode(ids["e1"])
        repo.delete_mentions_for_episode(ids["e2"])
        assert repo.get_enrichment("person:elon-musk") is not None


class TestSummaryIntegration:
    def test_summary_includes_enrichment_and_most_discussed(self, repo_env):
        repo, _ = repo_env
        repo.upsert_enrichment(_sample_enrichment())
        summary = repo.get_entity_summary("person:elon-musk")
        assert summary is not None
        assert summary["enrichment"] is not None
        assert summary["enrichment"].headline == "business magnate"

        discussed = summary["most_discussed_on"]
        assert [d["podcast_title"] for d in discussed] == ["Podcast A", "Podcast B"]
        assert [d["mention_count"] for d in discussed] == [3, 1]

    def test_summary_enrichment_none_when_unenriched(self, repo_env):
        repo, _ = repo_env
        summary = repo.get_entity_summary("person:elon-musk")
        assert summary["enrichment"] is None
