"""Postgres contract suite for the spec #116 sync track repository.

Runs the same cases as ``tests/unit/repositories/test_sqlite_sync_track_repository.py``
against Postgres, so the two backends cannot drift (FM-6). Cases skip (not
fail) when ``TEST_DATABASE_URL`` is unset or unreachable.
"""

from __future__ import annotations

import pytest

from tests.integration.test_analytics_repository_contract import PG_DSN, pg_only
from tests.unit.repositories import test_sqlite_sync_track_repository as cases

pytestmark = pg_only


@pytest.fixture
def repo():
    import psycopg

    from thestill.repositories.postgres_schema import ensure_schema
    from thestill.repositories.postgres_sync_track_repository import PostgresSyncTrackRepository

    ensure_schema(PG_DSN)
    with psycopg.connect(PG_DSN) as conn:
        conn.execute("TRUNCATE users, podcasts, episodes CASCADE")
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (%s, 'https://example.com/rss', 'Show', 'show')",
            (cases.PODCAST,),
        )
        conn.execute(
            "INSERT INTO episodes (id, podcast_id, external_id, title, slug, audio_url)"
            " VALUES (%s, %s, 'ext-1', 'Episode', 'episode', 'https://example.com/a.mp3')",
            (cases.EPISODE, cases.PODCAST),
        )
    return PostgresSyncTrackRepository(PG_DSN)


test_pending_is_not_visible_as_current = cases.test_pending_is_not_visible_as_current
test_new_pending_replaces_the_old_one_and_reports_its_path = (
    cases.test_new_pending_replaces_the_old_one_and_reports_its_path
)
test_promote_switches_and_returns_the_retired_path = cases.test_promote_switches_and_returns_the_retired_path
test_promote_of_unknown_generation_changes_nothing = cases.test_promote_of_unknown_generation_changes_nothing
test_retire_by_state_and_all = cases.test_retire_by_state_and_all


def test_deleting_the_episode_removes_its_track_rows(repo):
    import psycopg

    repo.put_pending(cases._record("g1"))
    repo.promote(cases.EPISODE, "g1")
    with psycopg.connect(PG_DSN) as conn:
        conn.execute("DELETE FROM episodes WHERE id = %s", (cases.EPISODE,))
    assert repo.get(cases.EPISODE) is None


def test_malformed_episode_id_is_a_miss_not_an_error(repo):
    assert repo.get("not-a-uuid") is None
    assert repo.promote("not-a-uuid", "g1") is None
    assert repo.retire("not-a-uuid") == []
