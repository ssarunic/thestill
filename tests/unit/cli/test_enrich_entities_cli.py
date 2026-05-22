"""Spec #45 Tier 0 — ``enrich-entities`` CLI resilience (spec #42 FM-1).

The bulk enrichment run must survive a single bad entity (a transient
SQLite write lock under the live web server, an unexpected payload):
the failure is skipped + counted, the batch carries on, and the command
exits non-zero so the failure is visible. This regression locks in the
fix for the crash where one ``database is locked`` aborted a 7,000-entity
run mid-batch.

Also unit-tests ``_upsert_enrichment_with_retry`` — the lock-retry that
lets a single pass survive contention with the running server.
"""

from __future__ import annotations

import sqlite3
import uuid

import pytest
from click.testing import CliRunner

from thestill.cli import _upsert_enrichment_with_retry, main
from thestill.models.enrichment import EnrichmentStatus, EntityEnrichment
from thestill.models.entities import EntityRecord, EntityType

# The middle entity (by id sort order) whose enrichment blows up.
_BAD_ID = "person:bbb-bad"


class _StubEnricher:
    """Stand-in for ``EntityEnricher`` — no network. Returns OK enrichment
    for every entity except ``_BAD_ID``, which raises (as a transient DB
    lock would)."""

    def __init__(self, **_kwargs):
        pass

    def enrich(self, entity):
        if entity.id == _BAD_ID:
            raise sqlite3.OperationalError("database is locked")
        return EntityEnrichment(
            entity_id=entity.id,
            headline="stub headline",
            wikidata_status=EnrichmentStatus.OK,
            wikipedia_status=EnrichmentStatus.OK,
        )


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    """Temp-DB-backed CLIContext with three QID entities; ``_BAD_ID`` sorts
    in the middle so we can prove the batch continues *past* the failure."""
    storage = tmp_path / "data"
    storage.mkdir()
    monkeypatch.setenv("STORAGE_PATH", str(storage))
    monkeypatch.setenv("THESTILL_ENV_FILE", str(tmp_path / ".no-such-env"))
    monkeypatch.setenv("ENRICHMENT_REQUEST_DELAY_SEC", "0")  # no politeness sleep in tests

    from thestill.repositories.sqlite_entity_repository import SqliteEntityRepository
    from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository

    db_path = storage / "podcasts.db"
    SqlitePodcastRepository(db_path=str(db_path))  # run migrations
    repo = SqliteEntityRepository(db_path=str(db_path))
    # ids sort: person:aaa < person:bbb-bad < person:ccc
    for slug in ("person:aaa", _BAD_ID, "person:ccc"):
        repo.upsert_entity(
            EntityRecord(
                id=slug, type=EntityType.PERSON, canonical_name=slug, wikidata_qid=f"Q{uuid.uuid4().int % 100000}"
            )
        )

    monkeypatch.setattr("thestill.core.entity_enricher.EntityEnricher", _StubEnricher)
    return repo


def test_bad_entity_does_not_abort_batch(cli_env):
    repo = cli_env
    result = CliRunner().invoke(main, ["enrich-entities"], catch_exceptions=False)

    # FM-1: one entity's failure must not kill the run — the entity that
    # sorts AFTER the bad one still got enriched.
    assert repo.get_enrichment("person:aaa") is not None
    assert repo.get_enrichment("person:ccc") is not None
    assert repo.get_enrichment(_BAD_ID) is None  # skipped, left for retry

    # FM-4: the failure is surfaced — non-zero exit + counted in the summary.
    assert result.exit_code == 1
    assert "2 enriched" in result.output
    assert "1 errored" in result.output


class TestUpsertWithRetry:
    class _LockingRepo:
        def __init__(self, fail_times):
            self.fail_times = fail_times
            self.calls = 0

        def upsert_enrichment(self, enrichment):
            self.calls += 1
            if self.calls <= self.fail_times:
                raise sqlite3.OperationalError("database is locked")

    def _enrichment(self):
        return EntityEnrichment(entity_id="person:x")

    def test_retries_then_succeeds_on_transient_lock(self):
        repo = self._LockingRepo(fail_times=2)
        _upsert_enrichment_with_retry(repo, self._enrichment(), base_delay=0)
        assert repo.calls == 3  # two locks, third succeeds

    def test_gives_up_after_attempts(self):
        repo = self._LockingRepo(fail_times=99)
        with pytest.raises(sqlite3.OperationalError):
            _upsert_enrichment_with_retry(repo, self._enrichment(), attempts=4, base_delay=0)
        assert repo.calls == 4

    def test_non_lock_error_is_not_retried(self):
        class _BoomRepo:
            def __init__(self):
                self.calls = 0

            def upsert_enrichment(self, enrichment):
                self.calls += 1
                raise ValueError("not a lock")

        repo = _BoomRepo()
        with pytest.raises(ValueError):
            _upsert_enrichment_with_retry(repo, self._enrichment(), base_delay=0)
        assert repo.calls == 1  # raised immediately, no retry
