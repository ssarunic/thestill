"""Dual-backend contract suite for the link decision repository (spec #81).

Same shape as ``test_mcp_token_repository_contract.py``: every behaviour is
asserted identically on SQLite and PostgreSQL. Postgres cases skip (not
fail) when ``TEST_DATABASE_URL`` is unset or unreachable.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from thestill.repositories.link_decision_repository import StoredLinkDecision
from thestill.repositories.sqlite_link_decision_repository import SqliteLinkDecisionRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository

PG_DSN = os.getenv("TEST_DATABASE_URL", "")


def _pg_reachable(dsn: str) -> bool:
    if not dsn:
        return False
    try:
        import psycopg

        with psycopg.connect(dsn, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


PG_OK = _pg_reachable(PG_DSN)

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
POD_A = "11111111-1111-4111-8111-111111111111"
POD_B = "22222222-2222-4222-8222-222222222222"


@pytest.fixture(params=["sqlite", "postgres"])
def repo(request, tmp_path):
    """A clean decision repository with two podcasts to scope rows to."""
    if request.param == "sqlite":
        db = str(tmp_path / "contract.db")
        SqlitePodcastRepository(db_path=db)  # owns the DDL
        with sqlite3.connect(db) as conn:
            for pid in (POD_A, POD_B):
                conn.execute(
                    "INSERT INTO podcasts (id, rss_url, title, description) VALUES (?, ?, ?, '')",
                    (pid, f"https://example.com/{pid}.xml", f"Show {pid[:4]}"),
                )
        yield SqliteLinkDecisionRepository(db_path=db)
        return

    if not PG_OK:
        pytest.skip("Postgres not reachable — set TEST_DATABASE_URL to include this backend")
    import psycopg

    from thestill.repositories.postgres_link_decision_repository import PostgresLinkDecisionRepository
    from thestill.repositories.postgres_schema import ensure_schema

    ensure_schema(PG_DSN)
    with psycopg.connect(PG_DSN) as conn:
        conn.execute("TRUNCATE podcasts, entity_link_decisions CASCADE")
        for pid in (POD_A, POD_B):
            conn.execute(
                "INSERT INTO podcasts (id, rss_url, title) VALUES (%s, %s, %s)",
                (pid, f"https://example.com/{pid}.xml", f"Show {pid[:4]}"),
            )
    yield PostgresLinkDecisionRepository(PG_DSN)


def _decision(**overrides) -> StoredLinkDecision:
    base = dict(
        surface_key="dario amodei",
        podcast_id=POD_A,
        qid="Q100",
        label="Dario Amodei",
        description="American AI researcher",
        confidence="high",
        reason="named as Anthropic's CEO",
        decided_at=NOW,
        linker_version="v1",
    )
    base.update(overrides)
    return StoredLinkDecision(**base)


def test_upsert_then_get_round_trips(repo):
    repo.upsert(_decision())
    got = repo.get("dario amodei", POD_A)
    assert got == _decision()
    assert got.decided_at.tzinfo is not None


def test_a_decided_none_round_trips_as_a_null_qid(repo):
    repo.upsert(_decision(qid=None, label=None, description=None, confidence="medium", reason=None))
    got = repo.get("dario amodei", POD_A)
    assert got is not None and got.qid is None and got.reason is None


def test_scopes_are_separate_rows(repo):
    repo.upsert(_decision(podcast_id=POD_A, qid="Q1"))
    repo.upsert(_decision(podcast_id=POD_B, qid="Q2"))
    repo.upsert(_decision(podcast_id=None, qid="Q3"))
    assert repo.get("dario amodei", POD_A).qid == "Q1"
    assert repo.get("dario amodei", POD_B).qid == "Q2"
    assert repo.get("dario amodei", None).qid == "Q3"


def test_get_does_not_fall_through_to_another_scope(repo):
    repo.upsert(_decision(podcast_id=None))
    assert repo.get("dario amodei", POD_A) is None
    assert repo.get("someone else", None) is None


@pytest.mark.parametrize("podcast_id", [POD_A, None])
def test_upsert_replaces_in_place_and_keeps_hits(repo, podcast_id):
    repo.upsert(_decision(podcast_id=podcast_id, qid="Q1"))
    repo.record_hit("dario amodei", podcast_id)
    repo.record_hit("dario amodei", podcast_id)
    later = NOW + timedelta(days=1)
    repo.upsert(_decision(podcast_id=podcast_id, qid="Q2", confidence="medium", decided_at=later, linker_version="v2"))
    got = repo.get("dario amodei", podcast_id)
    assert (got.qid, got.confidence, got.decided_at, got.linker_version, got.hits) == ("Q2", "medium", later, "v2", 2)


def test_record_hit_touches_only_its_own_scope(repo):
    repo.upsert(_decision(podcast_id=POD_A))
    repo.upsert(_decision(podcast_id=None))
    repo.record_hit("dario amodei", None)
    assert repo.get("dario amodei", POD_A).hits == 0
    assert repo.get("dario amodei", None).hits == 1


def test_podcast_decisions_lists_podcast_scoped_rows_only(repo):
    repo.upsert(_decision(podcast_id=POD_A, qid="Q1"))
    repo.upsert(_decision(podcast_id=POD_B, qid=None))
    repo.upsert(_decision(podcast_id=None, qid="Q9"))
    rows = sorted(repo.podcast_decisions("dario amodei"), key=lambda r: r.podcast_id)
    assert [(r.podcast_id, r.qid) for r in rows] == [(POD_A, "Q1"), (POD_B, None)]


def test_delete_removes_every_scope_of_the_name_and_nothing_else(repo):
    repo.upsert(_decision(podcast_id=POD_A))
    repo.upsert(_decision(podcast_id=None))
    repo.upsert(_decision(surface_key="openai", podcast_id=POD_A))
    assert repo.delete("dario amodei") == 2
    assert repo.get("dario amodei", POD_A) is None and repo.get("dario amodei", None) is None
    assert repo.get("openai", POD_A) is not None
    assert repo.delete("dario amodei") == 0


def test_keys_are_stored_exactly_as_given(repo):
    """Folding is the caller's job (Python ``casefold``); SQL never folds."""
    repo.upsert(_decision(surface_key="škoda"))
    assert repo.get("škoda", POD_A) is not None
    assert repo.get("ŠKODA", POD_A) is None


def test_rejects_a_confidence_outside_the_three_levels(repo):
    with pytest.raises(Exception):
        repo.upsert(replace(_decision(), confidence="certain"))
