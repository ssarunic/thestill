"""Postgres contract suite for the spec #114 quote share repository.

Runs the same cases as ``tests/unit/repositories/test_sqlite_quote_share_repository.py``
against Postgres, so the two backends cannot drift (FM-6). Cases skip (not
fail) when ``TEST_DATABASE_URL`` is unset or unreachable.
"""

from __future__ import annotations

import pytest

from tests.integration.test_analytics_repository_contract import PG_DSN, pg_only
from tests.unit.repositories import test_sqlite_quote_share_repository as cases

pytestmark = pg_only


@pytest.fixture
def repo():
    import psycopg

    from thestill.repositories.postgres_quote_share_repository import PostgresQuoteShareRepository
    from thestill.repositories.postgres_schema import ensure_schema

    ensure_schema(PG_DSN)
    with psycopg.connect(PG_DSN) as conn:
        conn.execute("TRUNCATE users, podcasts, episodes CASCADE")
        conn.execute("DELETE FROM entities WHERE id = 'person:jane-doe'")
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (%s, 'https://example.com/rss', 'Show', 'show')",
            (cases.PODCAST,),
        )
        conn.execute(
            "INSERT INTO episodes (id, podcast_id, external_id, title, slug, audio_url)"
            " VALUES (%s, %s, 'ext-1', 'Episode', 'episode', 'https://example.com/a.mp3')",
            (cases.EPISODE, cases.PODCAST),
        )
        conn.execute("INSERT INTO entities (id, type, canonical_name) VALUES ('person:jane-doe', 'person', 'Jane Doe')")
    return PostgresQuoteShareRepository(PG_DSN)


test_upsert_inserts_once_and_reports_the_id_usable = cases.test_upsert_inserts_once_and_reports_the_id_usable
test_get_joins_what_a_card_needs = cases.test_get_joins_what_a_card_needs
test_upsert_refreshes_the_cached_speaker_and_segment = cases.test_upsert_refreshes_the_cached_speaker_and_segment
test_an_id_that_holds_another_quote_is_a_collision_and_is_left_alone = (
    cases.test_an_id_that_holds_another_quote_is_a_collision_and_is_left_alone
)
test_revoke_is_once_and_a_revoked_id_is_not_usable = cases.test_revoke_is_once_and_a_revoked_id_is_not_usable
test_revoke_for_podcast_returns_the_live_ids = cases.test_revoke_for_podcast_returns_the_live_ids
test_hits_add_up_per_share = cases.test_hits_add_up_per_share
test_reconcile_queries = cases.test_reconcile_queries


def test_deleting_the_episode_removes_its_shares_and_hits(repo):
    from datetime import date

    import psycopg

    repo.upsert_gated([cases._share()])
    repo.record_hit("aaaaaaaaaa", date(2026, 11, 24), "")
    with psycopg.connect(PG_DSN) as conn:
        conn.execute("DELETE FROM episodes WHERE id = %s", (cases.EPISODE,))
        assert conn.execute("SELECT count(*) FROM quote_share_hits").fetchone()[0] == 0
    assert repo.get("aaaaaaaaaa") is None
