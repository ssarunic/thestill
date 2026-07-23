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

"""Spec #64 — dual-backend contract for ``LegacyClaimRepository``.

Same shape as ``test_podcast_repository_podcasts_contract.py``: one test
body parametrized over sqlite/postgres, Postgres skipped unless
``TEST_DATABASE_URL`` is reachable. All assertions are white-box raw SQL
against a real database — the whole point of this repository is FK
cascades and transactional atomicity, which mocks cannot exercise.
"""

import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from thestill.repositories.legacy_claim_repository import LegacyClaimRepository
from thestill.repositories.sqlite_legacy_claim_repository import SqliteLegacyClaimRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository

PG_DSN = os.getenv("TEST_DATABASE_URL", "")
_PG_TABLES = "podcasts, episodes, users"

LOCAL_EMAIL = "local@thestill.me"


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


@pytest.fixture(params=["sqlite", "postgres"])
def claim_ctx(request, tmp_path):
    """Yield ``(claim_repo, exec_fn)`` per backend on a clean database."""
    if request.param == "sqlite":
        db_path = str(tmp_path / "claim.db")
        SqlitePodcastRepository(db_path=db_path)  # bootstraps full schema

        def _exec(sql, params=(), fetch=False):
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            try:
                cur = conn.execute(sql, params)
                rows = [dict(r) for r in cur.fetchall()] if fetch else None
                conn.commit()
                return rows
            finally:
                conn.close()

        _exec.is_pg = False
        yield SqliteLegacyClaimRepository(db_path=db_path), _exec
        return

    if not PG_OK:
        pytest.skip("Postgres not reachable — set TEST_DATABASE_URL to include this backend")
    import psycopg
    from psycopg.rows import dict_row

    from thestill.repositories.postgres_legacy_claim_repository import PostgresLegacyClaimRepository
    from thestill.repositories.postgres_schema import ensure_schema

    ensure_schema(PG_DSN)
    with psycopg.connect(PG_DSN) as conn:
        conn.execute(f"TRUNCATE {_PG_TABLES} CASCADE")

    def _exec(sql, params=(), fetch=False):
        with psycopg.connect(PG_DSN, row_factory=dict_row) as conn:
            cur = conn.execute(sql.replace("?", "%s"), params)
            return [dict(r) for r in cur.fetchall()] if fetch else None

    _exec.is_pg = True
    yield PostgresLegacyClaimRepository(PG_DSN), _exec


# ---------------------------------------------------------------------------
# Seeding helpers (raw SQL, minimal NOT NULL columns, both dialects)
# ---------------------------------------------------------------------------
_NOW = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def _ts(ex, dt=_NOW):
    """Bind a timestamp per backend (spec #44): tz-aware datetime on PG,
    ISO-8601 (+00:00) text on SQLite."""
    return dt if getattr(ex, "is_pg", False) else dt.isoformat()


def _mk_user(ex, email=None, is_admin=False) -> str:
    uid = str(uuid.uuid4())
    ex(
        "INSERT INTO users (id, email, created_at, is_admin) VALUES (?, ?, ?, ?)",
        (uid, email or f"u-{uid[:8]}@p1.test", _ts(ex), is_admin),
    )
    return uid


def _mk_podcast(ex) -> str:
    pid = str(uuid.uuid4())
    ex(
        "INSERT INTO podcasts (id, created_at, updated_at, rss_url, title, slug, description, language)"
        " VALUES (?, ?, ?, ?, ?, ?, '', 'en')",
        (pid, _ts(ex), _ts(ex), f"https://p1.test/{pid[:8]}/feed.xml", f"P {pid[:8]}", f"p-{pid[:8]}"),
    )
    return pid


def _mk_episode(ex, podcast_id: str) -> str:
    eid = str(uuid.uuid4())
    ex(
        "INSERT INTO episodes (id, podcast_id, created_at, updated_at, external_id, title, audio_url)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (eid, podcast_id, _ts(ex), _ts(ex), f"ep-{eid[:8]}", f"E {eid[:8]}", f"https://p1.test/{eid[:8]}.mp3"),
    )
    return eid


def _follow(ex, user_id: str, podcast_id: str) -> None:
    ex(
        "INSERT INTO podcast_followers (id, user_id, podcast_id, created_at) VALUES (?, ?, ?, ?)",
        (str(uuid.uuid4()), user_id, podcast_id, _ts(ex)),
    )


def _inbox(ex, user_id: str, episode_id: str) -> None:
    ex(
        "INSERT INTO user_episode_inbox (id, user_id, episode_id, source, state, delivered_at)"
        " VALUES (?, ?, ?, 'follow_seed', 'unread', ?)",
        (str(uuid.uuid4()), user_id, episode_id, _ts(ex)),
    )


def _briefing(ex, user_id: str) -> str:
    bid = str(uuid.uuid4())
    ex(
        "INSERT INTO user_briefings (id, user_id, cursor_from, cursor_to, episode_count, created_at)"
        " VALUES (?, ?, ?, ?, 1, ?)",
        (bid, user_id, _ts(ex, _NOW - timedelta(days=1)), _ts(ex), _ts(ex)),
    )
    return bid


def _delivery(ex, briefing_id: str) -> str:
    did = str(uuid.uuid4())
    ex(
        "INSERT INTO briefing_deliveries (id, briefing_id, channel, status, created_at)"
        " VALUES (?, ?, 'email', 'pending', ?)",
        (did, briefing_id, _ts(ex)),
    )
    return did


def _schedule(ex, user_id: str, tz="Europe/Berlin") -> None:
    ex(
        "INSERT INTO user_briefing_schedules (user_id, frequency, hour_local, timezone, created_at, updated_at)"
        " VALUES (?, 'daily', 8, ?, ?, ?)",
        (user_id, tz, _ts(ex), _ts(ex)),
    )


def _count(ex, table: str, user_id: str) -> int:
    return ex(f"SELECT COUNT(*) AS n FROM {table} WHERE user_id = ?", (user_id,), fetch=True)[0]["n"]


def _seed_local_with_everything(ex):
    """Local user with 2 follows, 2 inbox rows, 1 briefing (+delivery), 1 schedule."""
    local = _mk_user(ex, email=LOCAL_EMAIL, is_admin=True)
    p1, p2 = _mk_podcast(ex), _mk_podcast(ex)
    e1, e2 = _mk_episode(ex, p1), _mk_episode(ex, p2)
    _follow(ex, local, p1)
    _follow(ex, local, p2)
    _inbox(ex, local, e1)
    _inbox(ex, local, e2)
    bid = _briefing(ex, local)
    _delivery(ex, bid)
    _schedule(ex, local)
    return local, (p1, p2), (e1, e2), bid


# ---------------------------------------------------------------------------
# claim_local_account
# ---------------------------------------------------------------------------
def test_claim_moves_everything_and_deletes_local(claim_ctx):
    repo, ex = claim_ctx
    local, _, _, bid = _seed_local_with_everything(ex)
    target = _mk_user(ex)

    result = repo.claim_local_account(local_email=LOCAL_EMAIL, target_user_id=target)

    assert result.found and result.claimed
    assert result.counts == {"followers": 2, "inbox": 2, "briefings": 1, "schedule": 1}
    # Everything now belongs to target.
    assert _count(ex, "podcast_followers", target) == 2
    assert _count(ex, "user_episode_inbox", target) == 2
    assert _count(ex, "user_briefings", target) == 1
    assert _count(ex, "user_briefing_schedules", target) == 1
    # Deliveries rode along via briefing_id (untouched, still attached).
    assert ex("SELECT COUNT(*) AS n FROM briefing_deliveries WHERE briefing_id = ?", (bid,), fetch=True)[0]["n"] == 1
    # Target inherited admin; local row is gone.
    assert ex("SELECT is_admin FROM users WHERE id = ?", (target,), fetch=True)[0]["is_admin"] in (1, True)
    assert ex("SELECT COUNT(*) AS n FROM users WHERE email = ?", (LOCAL_EMAIL,), fetch=True)[0]["n"] == 0


def test_claim_skips_collisions_and_existing_schedule(claim_ctx):
    repo, ex = claim_ctx
    local, (p1, _p2), (e1, _e2), _ = _seed_local_with_everything(ex)
    target = _mk_user(ex)
    # Target independently follows p1, has e1 in inbox, and has a schedule.
    _follow(ex, target, p1)
    _inbox(ex, target, e1)
    _schedule(ex, target, tz="America/New_York")

    result = repo.claim_local_account(local_email=LOCAL_EMAIL, target_user_id=target)

    assert result.claimed
    # Only the non-colliding rows moved; schedule stayed the target's own.
    assert result.counts == {"followers": 1, "inbox": 1, "briefings": 1, "schedule": 0}
    assert _count(ex, "podcast_followers", target) == 2  # own + moved
    assert _count(ex, "user_episode_inbox", target) == 2
    row = ex("SELECT timezone FROM user_briefing_schedules WHERE user_id = ?", (target,), fetch=True)
    assert row[0]["timezone"] == "America/New_York"
    # Colliding local rows were cascade-deleted with the local user.
    assert ex("SELECT COUNT(*) AS n FROM users WHERE email = ?", (LOCAL_EMAIL,), fetch=True)[0]["n"] == 0
    assert _count(ex, "podcast_followers", local) == 0
    assert _count(ex, "user_episode_inbox", local) == 0
    assert _count(ex, "user_briefing_schedules", local) == 0


def test_claim_missing_local_is_noop(claim_ctx):
    repo, ex = claim_ctx
    target = _mk_user(ex)
    result = repo.claim_local_account(local_email=LOCAL_EMAIL, target_user_id=target)
    assert result.found is False and result.claimed is False


def test_claim_is_idempotent(claim_ctx):
    repo, ex = claim_ctx
    _seed_local_with_everything(ex)
    target = _mk_user(ex)
    assert repo.claim_local_account(local_email=LOCAL_EMAIL, target_user_id=target).claimed
    second = repo.claim_local_account(local_email=LOCAL_EMAIL, target_user_id=target)
    assert second.found is False and second.claimed is False


def test_claim_dry_run_writes_nothing(claim_ctx):
    repo, ex = claim_ctx
    local, _, _, _ = _seed_local_with_everything(ex)
    target = _mk_user(ex)

    result = repo.claim_local_account(local_email=LOCAL_EMAIL, target_user_id=target, dry_run=True)

    assert result.found and not result.claimed
    assert result.counts == {"followers": 2, "inbox": 2, "briefings": 1, "schedule": 1}
    # Nothing moved, local row intact, no admin grant.
    assert _count(ex, "podcast_followers", local) == 2
    assert _count(ex, "podcast_followers", target) == 0
    assert ex("SELECT COUNT(*) AS n FROM users WHERE email = ?", (LOCAL_EMAIL,), fetch=True)[0]["n"] == 1
    assert ex("SELECT is_admin FROM users WHERE id = ?", (target,), fetch=True)[0]["is_admin"] in (0, False)


def test_claim_onto_local_itself_is_refused(claim_ctx):
    repo, ex = claim_ctx
    local, _, _, _ = _seed_local_with_everything(ex)
    result = repo.claim_local_account(local_email=LOCAL_EMAIL, target_user_id=local)
    assert result.found is True and result.claimed is False
    assert ex("SELECT COUNT(*) AS n FROM users WHERE email = ?", (LOCAL_EMAIL,), fetch=True)[0]["n"] == 1


# ---------------------------------------------------------------------------
# discard_local_account
# ---------------------------------------------------------------------------
def test_discard_cascades_all_per_user_rows(claim_ctx):
    repo, ex = claim_ctx
    local, _, _, bid = _seed_local_with_everything(ex)

    result = repo.discard_local_account(local_email=LOCAL_EMAIL)

    assert result.found and result.claimed
    assert result.counts == {"followers": 2, "inbox": 2, "briefings": 1, "schedule": 1}
    assert ex("SELECT COUNT(*) AS n FROM users WHERE email = ?", (LOCAL_EMAIL,), fetch=True)[0]["n"] == 0
    assert _count(ex, "podcast_followers", local) == 0
    assert _count(ex, "user_episode_inbox", local) == 0
    assert _count(ex, "user_briefings", local) == 0
    assert _count(ex, "user_briefing_schedules", local) == 0
    # briefing_deliveries cascade transitively through user_briefings.
    assert ex("SELECT COUNT(*) AS n FROM briefing_deliveries WHERE briefing_id = ?", (bid,), fetch=True)[0]["n"] == 0


def test_discard_dry_run_and_missing(claim_ctx):
    repo, ex = claim_ctx
    assert repo.discard_local_account(local_email=LOCAL_EMAIL).found is False

    local, _, _, _ = _seed_local_with_everything(ex)
    result = repo.discard_local_account(local_email=LOCAL_EMAIL, dry_run=True)
    assert result.found and not result.claimed
    assert result.counts == {"followers": 2, "inbox": 2, "briefings": 1, "schedule": 1}
    assert _count(ex, "podcast_followers", local) == 2
    assert ex("SELECT COUNT(*) AS n FROM users WHERE email = ?", (LOCAL_EMAIL,), fetch=True)[0]["n"] == 1


def test_abc_is_satisfied(claim_ctx):
    repo, _ = claim_ctx
    assert isinstance(repo, LegacyClaimRepository)
