# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Dual-backend contract suite for the digest repository (spec #44).

The same tests run against BOTH the SQLite and the PostgreSQL implementations
behind the ``DigestRepository`` ABC. This is the fidelity guarantee spec #42
FM-5 demands: the Postgres port is exercised against a *real* Postgres, never a
mock, and every behaviour is asserted identically on both engines so a dialect
divergence fails the build.

The Postgres cases are skipped (not failed) when no server is reachable, so the
suite still passes on a SQLite-only CI runner. Point ``TEST_DATABASE_URL`` at a
Postgres to include them:

    TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/thestill_digest \\
        ./venv/bin/python -m pytest tests/integration/test_digest_repository_contract.py
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from thestill.models.digest import Digest, DigestStatus
from thestill.models.user import User
from thestill.repositories.sqlite_digest_repository import SqliteDigestRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.repositories.sqlite_user_repository import SqliteUserRepository

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

# digests.user_id is a NOT NULL FK to users on Postgres — parent rows are
# created per-fixture. Ids are production-shaped uuid strings (FM-5).
USER_1 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
USER_2 = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

# digest_episodes.episode_id is uuid on Postgres (no FK to episodes), so
# arbitrary uuid-shaped ids work on both engines.
EP_1 = "11111111-1111-4111-8111-111111111111"
EP_2 = "22222222-2222-4222-8222-222222222222"
EP_3 = "33333333-3333-4333-8333-333333333333"

NOW = datetime(2026, 7, 1, 8, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(params=["sqlite", "postgres"])
def repo(request, tmp_path):
    """Yield a clean digest repository (with parent users) for each backend."""
    if request.param == "sqlite":
        db = str(tmp_path / "contract.db")
        # SqlitePodcastRepository owns the users/digests DDL.
        SqlitePodcastRepository(db_path=db)
        users = SqliteUserRepository(db_path=db)
        users.save(User(id=USER_1, email="ada@example.com", name="Ada"))
        users.save(User(id=USER_2, email="grace@example.com", name="Grace"))
        yield SqliteDigestRepository(db_path=db)
        return

    if not PG_OK:
        pytest.skip("Postgres not reachable — set TEST_DATABASE_URL to include this backend")
    import psycopg

    from thestill.repositories.postgres_digest_repository import PostgresDigestRepository
    from thestill.repositories.postgres_schema import ensure_schema

    ensure_schema(PG_DSN)  # idempotent typed-schema bootstrap
    with psycopg.connect(PG_DSN) as conn:
        conn.execute("TRUNCATE digests, digest_episodes, users CASCADE")
        conn.execute(
            "INSERT INTO users (id, email, name, created_at) VALUES (%s, %s, %s, %s), (%s, %s, %s, %s)",
            (USER_1, "ada@example.com", "Ada", NOW, USER_2, "grace@example.com", "Grace", NOW),
        )
    yield PostgresDigestRepository(PG_DSN)


def _mk_digest(**overrides) -> Digest:
    base = dict(
        user_id=USER_1,
        created_at=NOW,
        period_start=NOW - timedelta(hours=24),
        period_end=NOW,
        episode_ids=[EP_1, EP_2],
        episodes_total=2,
    )
    base.update(overrides)
    return Digest(**base)


# ---------------------------------------------------------------------------
# Save / get round-trip
# ---------------------------------------------------------------------------
def test_save_then_get_by_id_roundtrips_all_fields(repo):
    d = _mk_digest(
        status=DigestStatus.COMPLETED,
        file_path="digests/2026-07-01.md",
        episodes_completed=2,
        episodes_failed=0,
        processing_time_seconds=12.5,
    )
    repo.save(d)

    got = repo.get_by_id(d.id)
    assert got is not None
    assert got.id == d.id
    assert got.user_id == USER_1
    assert got.status is DigestStatus.COMPLETED
    assert got.file_path == "digests/2026-07-01.md"
    assert sorted(got.episode_ids) == sorted([EP_1, EP_2])
    assert got.episodes_total == 2
    assert got.episodes_completed == 2
    assert got.episodes_failed == 0
    assert got.processing_time_seconds == pytest.approx(12.5)
    assert got.error_message is None
    # tz-aware instants round-trip identically on both engines.
    assert got.created_at == d.created_at
    assert got.updated_at == d.updated_at
    assert got.period_start == d.period_start
    assert got.period_end == d.period_end
    assert got.created_at.tzinfo is not None


def test_get_by_id_missing_returns_none(repo):
    assert repo.get_by_id("00000000-0000-0000-0000-000000000000") is None


def test_save_without_episodes(repo):
    d = _mk_digest(episode_ids=[], episodes_total=0)
    repo.save(d)
    got = repo.get_by_id(d.id)
    assert got.episode_ids == []


def test_save_upserts_mutable_fields_and_replaces_episodes(repo):
    """ON CONFLICT(id): status/file_path/counters/error update; created_at
    and the period columns are preserved; episode links are replaced."""
    d = _mk_digest()
    repo.save(d)

    d.mark_completed(
        file_path="digests/out.md",
        episodes_completed=1,
        episodes_failed=1,
        processing_time_seconds=3.25,
    )
    d.episode_ids = [EP_3]  # replace associations
    repo.save(d)

    got = repo.get_by_id(d.id)
    assert got.status is DigestStatus.PARTIAL  # 1 completed + 1 failed
    assert got.file_path == "digests/out.md"
    assert got.episodes_completed == 1
    assert got.episodes_failed == 1
    assert got.processing_time_seconds == pytest.approx(3.25)
    assert got.created_at == NOW  # preserved on conflict
    assert got.period_start == NOW - timedelta(hours=24)  # preserved
    assert got.episode_ids == [EP_3]  # old links gone
    assert got.updated_at >= d.created_at
    # count stays 1 — it was an update, not a second row.
    assert repo.count() == 1


def test_status_transitions_roundtrip(repo):
    d = _mk_digest()
    repo.save(d)
    assert repo.get_by_id(d.id).status is DigestStatus.PENDING

    d.mark_in_progress()
    repo.save(d)
    assert repo.get_by_id(d.id).status is DigestStatus.IN_PROGRESS

    d.mark_failed("llm exploded")
    repo.save(d)
    got = repo.get_by_id(d.id)
    assert got.status is DigestStatus.FAILED
    assert got.error_message == "llm exploded"


# ---------------------------------------------------------------------------
# get_all / get_latest / count
# ---------------------------------------------------------------------------
def _seed_three(repo):
    """Three digests: oldest failed (user 2), middle pending, newest completed."""
    d1 = _mk_digest(created_at=NOW - timedelta(hours=2), user_id=USER_2, status=DigestStatus.FAILED, episode_ids=[])
    d2 = _mk_digest(created_at=NOW - timedelta(hours=1), episode_ids=[EP_1])
    d3 = _mk_digest(created_at=NOW, status=DigestStatus.COMPLETED, episode_ids=[EP_2])
    for d in (d1, d2, d3):
        repo.save(d)
    return d1, d2, d3


def test_get_all_orders_by_created_at_desc(repo):
    d1, d2, d3 = _seed_three(repo)
    got = repo.get_all()
    assert [d.id for d in got] == [d3.id, d2.id, d1.id]
    # Episode links are batch-loaded per digest.
    assert got[0].episode_ids == [EP_2]
    assert got[1].episode_ids == [EP_1]
    assert got[2].episode_ids == []


def test_get_all_limit_offset(repo):
    d1, d2, d3 = _seed_three(repo)
    assert [d.id for d in repo.get_all(limit=1)] == [d3.id]
    assert [d.id for d in repo.get_all(limit=2, offset=1)] == [d2.id, d1.id]
    assert repo.get_all(offset=3) == []


def test_get_all_filters_by_status_and_user(repo):
    d1, d2, d3 = _seed_three(repo)
    assert [d.id for d in repo.get_all(status=DigestStatus.COMPLETED)] == [d3.id]
    assert [d.id for d in repo.get_all(user_id=USER_2)] == [d1.id]
    assert [d.id for d in repo.get_all(status=DigestStatus.FAILED, user_id=USER_2)] == [d1.id]
    assert repo.get_all(status=DigestStatus.FAILED, user_id=USER_1) == []


def test_get_all_empty_repo(repo):
    assert repo.get_all() == []


def test_get_latest(repo):
    assert repo.get_latest() is None
    _, _, d3 = _seed_three(repo)
    assert repo.get_latest().id == d3.id


def test_count_with_filters(repo):
    assert repo.count() == 0
    _seed_three(repo)
    assert repo.count() == 3
    assert repo.count(status=DigestStatus.FAILED) == 1
    assert repo.count(user_id=USER_1) == 2
    assert repo.count(status=DigestStatus.PENDING, user_id=USER_1) == 1
    assert repo.count(status=DigestStatus.COMPLETED, user_id=USER_2) == 0


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------
def test_delete_cascades_episode_links(repo):
    d = _mk_digest()
    repo.save(d)
    assert repo.is_episode_in_any_digest(EP_1) is True

    assert repo.delete(d.id) is True
    assert repo.get_by_id(d.id) is None
    # digest_episodes rows go with the digest (ON DELETE CASCADE).
    assert repo.is_episode_in_any_digest(EP_1) is False
    assert repo.delete(d.id) is False  # already gone


# ---------------------------------------------------------------------------
# Episode membership
# ---------------------------------------------------------------------------
def test_get_episodes_in_digest(repo):
    d = _mk_digest(episode_ids=[EP_1, EP_2, EP_3], episodes_total=3)
    repo.save(d)
    assert sorted(repo.get_episodes_in_digest(d.id)) == sorted([EP_1, EP_2, EP_3])
    # Missing digest → empty list, not an error.
    assert repo.get_episodes_in_digest("00000000-0000-0000-0000-000000000000") == []


def test_is_episode_in_any_digest(repo):
    assert repo.is_episode_in_any_digest(EP_1) is False
    repo.save(_mk_digest(episode_ids=[EP_1]))
    assert repo.is_episode_in_any_digest(EP_1) is True
    assert repo.is_episode_in_any_digest(EP_3) is False


def test_get_digests_containing_episode(repo):
    d1 = _mk_digest(created_at=NOW - timedelta(hours=1), episode_ids=[EP_1, EP_2])
    d2 = _mk_digest(created_at=NOW, user_id=USER_2, episode_ids=[EP_1])
    repo.save(d1)
    repo.save(d2)

    got = repo.get_digests_containing_episode(EP_1)
    assert [d.id for d in got] == [d2.id, d1.id]  # created_at DESC
    # Full episode lists are hydrated on each hit.
    assert sorted(got[1].episode_ids) == sorted([EP_1, EP_2])

    # user filter
    assert [d.id for d in repo.get_digests_containing_episode(EP_1, user_id=USER_1)] == [d1.id]
    # episode only in one digest
    assert [d.id for d in repo.get_digests_containing_episode(EP_2)] == [d1.id]
    # unknown episode → empty
    assert repo.get_digests_containing_episode(EP_3) == []


# ---------------------------------------------------------------------------
# Period overlap
# ---------------------------------------------------------------------------
def test_get_digests_in_period_overlap_semantics(repo):
    """Overlap is inclusive: period_start <= end AND period_end >= start."""
    early = _mk_digest(
        created_at=NOW - timedelta(hours=2),
        period_start=NOW - timedelta(hours=48),
        period_end=NOW - timedelta(hours=24),
        episode_ids=[],
    )
    late = _mk_digest(
        created_at=NOW,
        user_id=USER_2,
        period_start=NOW - timedelta(hours=24),
        period_end=NOW,
        episode_ids=[EP_1],
    )
    repo.save(early)
    repo.save(late)

    # Window covering both.
    got = repo.get_digests_in_period(NOW - timedelta(hours=48), NOW)
    assert [d.id for d in got] == [late.id, early.id]  # created_at DESC
    assert got[0].episode_ids == [EP_1]

    # Window touching only `early`'s end boundary (inclusive).
    got = repo.get_digests_in_period(NOW - timedelta(hours=24), NOW - timedelta(hours=24))
    assert {d.id for d in got} == {early.id, late.id}  # both touch that instant

    # Window strictly after both periods.
    assert repo.get_digests_in_period(NOW + timedelta(hours=1), NOW + timedelta(hours=2)) == []

    # Window strictly before both periods.
    assert repo.get_digests_in_period(NOW - timedelta(hours=72), NOW - timedelta(hours=49)) == []

    # user filter
    got = repo.get_digests_in_period(NOW - timedelta(hours=48), NOW, user_id=USER_2)
    assert [d.id for d in got] == [late.id]
