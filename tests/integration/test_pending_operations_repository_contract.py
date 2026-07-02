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

"""Dual-backend contract suite for the pending-operations repository (spec #44).

The same tests run against BOTH the SQLite and the PostgreSQL implementations
behind the ``PendingOperationsRepository`` ABC. Every behaviour is asserted
identically on both engines so a dialect divergence fails the build (spec #42
FM-5: real Postgres, never a mock).

The Postgres cases are skipped (not failed) when no server is reachable, so the
suite still passes on a SQLite-only CI runner. Point ``TEST_DATABASE_URL`` at a
Postgres to include them:

    TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/thestill_pending \\
        ./venv/bin/python -m pytest tests/integration/test_pending_operations_repository_contract.py
"""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import timezone

import pytest

from thestill.repositories.sqlite_pending_operations_repository import (
    SqlitePendingOperationsRepository,
)
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

# Backend-specific integrity errors share no base class other than Exception;
# the tuple keeps the duplicate-create assertion identical on both engines.
try:
    import psycopg

    INTEGRITY_ERRORS = (sqlite3.IntegrityError, psycopg.errors.UniqueViolation)
except ImportError:  # pragma: no cover — psycopg is a hard dep, but be safe
    INTEGRITY_ERRORS = (sqlite3.IntegrityError,)

# episode_id is a native uuid column in Postgres (text in SQLite) — use
# production-shaped UUID strings so the same values bind on both engines.
EP_1 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"
EP_2 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2"
EP_SHARED = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1"
EP_OTHER = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2"


@pytest.fixture(params=["sqlite", "postgres"])
def repo(request, tmp_path):
    """Yield a clean pending-operations repository for each backend."""
    if request.param == "sqlite":
        db = str(tmp_path / "contract.db")
        # SqlitePodcastRepository owns the pending_transcription_operations DDL.
        SqlitePodcastRepository(db_path=db)
        yield SqlitePendingOperationsRepository(db_path=db)
        return

    if not PG_OK:
        pytest.skip("Postgres not reachable — set TEST_DATABASE_URL to include this backend")
    import psycopg

    from thestill.repositories.postgres_pending_operations_repository import (
        PostgresPendingOperationsRepository,
    )
    from thestill.repositories.postgres_schema import ensure_schema

    ensure_schema(PG_DSN)  # idempotent typed-schema bootstrap
    with psycopg.connect(PG_DSN) as conn:
        # episode_id has no FK on this table — no parent rows needed.
        conn.execute("TRUNCATE pending_transcription_operations")
    yield PostgresPendingOperationsRepository(PG_DSN)


def _sample_payload(**overrides) -> dict:
    base = {
        "provider": "elevenlabs",
        "transcription_id": "el-job-123",
        "audio_path": "downsampled_audio/foo/ep.wav",
        "language": "en",
        "episode_id": EP_1,
        "state": "pending",
        "created_at": "2026-07-01T12:00:00+00:00",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# create + get round-trip
# ---------------------------------------------------------------------------
def test_create_then_get_round_trips_payload(repo):
    payload = _sample_payload()
    repo.create(operation_id="el-job-123", provider="elevenlabs", episode_id=EP_1, payload=payload)

    op = repo.get("el-job-123")
    assert op is not None
    assert op.operation_id == "el-job-123"
    assert op.provider == "elevenlabs"
    assert op.episode_id == EP_1  # uuid column reads back as the same str
    assert op.payload == payload  # full lossless round-trip (dict in, dict out)
    # Timestamps are tz-aware UTC instants on both engines.
    assert op.created_at.tzinfo is not None
    assert op.updated_at.tzinfo is not None
    assert op.created_at.astimezone(timezone.utc) == op.created_at


def test_get_missing_returns_none(repo):
    assert repo.get("does-not-exist") is None


def test_nested_payload_structures_preserved(repo):
    """JSON-text (SQLite) vs jsonb (Postgres) must be invisible to callers:
    nested dicts/lists, None, bools, ints and floats all round-trip."""
    payload = {
        "operation_id": "x",
        "extras": {"chunks": [{"index": 0, "start": 0}, {"index": 1, "start": 1500}]},
        "completed_at": None,
        "flag": True,
        "ratio": 0.5,
    }
    repo.create(operation_id="x", provider="google", episode_id=EP_1, payload=payload)
    assert repo.get("x").payload == payload


def test_duplicate_operation_id_raises_integrity_error(repo):
    """operation_id is a real uniqueness invariant — duplicate create raises
    the backend integrity error rather than silently upserting."""
    repo.create("dup", "elevenlabs", EP_1, {"state": "pending"})
    with pytest.raises(INTEGRITY_ERRORS):
        repo.create("dup", "elevenlabs", EP_1, {"state": "pending"})


# ---------------------------------------------------------------------------
# list_by_provider
# ---------------------------------------------------------------------------
def test_list_by_provider_filters(repo):
    repo.create("el-1", "elevenlabs", EP_1, {"k": "v1"})
    repo.create("el-2", "elevenlabs", EP_2, {"k": "v2"})
    repo.create("g-1", "google", EP_OTHER, {"k": "v3"})

    assert sorted(op.operation_id for op in repo.list_by_provider("elevenlabs")) == ["el-1", "el-2"]
    assert [op.operation_id for op in repo.list_by_provider("google")] == ["g-1"]


def test_list_by_provider_oldest_first(repo):
    repo.create("first", "elevenlabs", EP_1, {})
    time.sleep(0.002)  # SQLite stamps have millisecond precision — avoid a tie
    repo.create("second", "elevenlabs", EP_2, {})
    assert [op.operation_id for op in repo.list_by_provider("elevenlabs")] == ["first", "second"]


def test_list_by_provider_empty_when_no_matches(repo):
    repo.create("g", "google", EP_1, {})
    assert repo.list_by_provider("elevenlabs") == []


# ---------------------------------------------------------------------------
# list_by_episode
# ---------------------------------------------------------------------------
def test_list_by_episode_filters(repo):
    # Google's chunked transcription writes multiple rows for one episode.
    repo.create("op-1", "google", EP_SHARED, {"chunk_index": 0})
    repo.create("op-2", "google", EP_SHARED, {"chunk_index": 1})
    repo.create("op-3", "google", EP_OTHER, {"chunk_index": 0})

    assert sorted(op.operation_id for op in repo.list_by_episode(EP_SHARED)) == ["op-1", "op-2"]
    assert repo.list_by_episode(EP_1) == []


# ---------------------------------------------------------------------------
# update_payload
# ---------------------------------------------------------------------------
def test_update_payload_replaces_and_touches_updated_at(repo):
    repo.create("u", "elevenlabs", EP_1, {"state": "pending"})
    time.sleep(0.002)
    repo.update_payload("u", {"state": "polling", "attempts": 3})

    op = repo.get("u")
    assert op.payload == {"state": "polling", "attempts": 3}
    assert op.updated_at > op.created_at  # updated_at advances on update


def test_update_payload_missing_is_silent(repo):
    repo.update_payload("ghost", {"state": "wat"})  # no-op, not an error
    assert repo.get("ghost") is None


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------
def test_delete_removes_row(repo):
    repo.create("d", "elevenlabs", EP_1, {})
    repo.delete("d")
    assert repo.get("d") is None


def test_delete_missing_is_idempotent(repo):
    # Repeated deletes don't raise — matches the legacy
    # Path.unlink(missing_ok=True) semantics.
    repo.delete("never-existed")
    repo.delete("never-existed")
