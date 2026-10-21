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

"""``POST /api/commands/dlq/retry-all`` against real repositories on both backends.

Regression (spec #107 Phase 1): the route requeued the dead tasks and then
called ``repository.clear_episode_failures``, which only the SQLite repository
implemented. On Postgres that raised ``AttributeError`` after the requeue had
landed, so the admin got a 500 for a retry that worked and the failure banners
stayed. The route unit tests mock the repository, which is how this shipped;
these tests wire the real repository and queue manager instead.

The Postgres case is skipped (not failed) when no server is reachable:

    TEST_DATABASE_URL=postgresql://localhost:5432/thestill_test \\
        ./venv/bin/python -m pytest tests/integration/web/test_dlq_retry_all.py
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.unit.web.auth_harness import ADMIN_USER, auth_client
from thestill.core.queue_manager import QueueManager, TaskStage, TaskStatus
from thestill.models.podcast import Episode, Podcast
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.web.routes import api_commands

PG_DSN = os.getenv("TEST_DATABASE_URL", "")

ROUTERS = [(api_commands.router, "/api/commands"), (api_commands.admin_router, "/api/commands")]


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


@pytest.fixture(
    params=[
        "sqlite",
        pytest.param(
            "postgres",
            marks=pytest.mark.skipif(not PG_OK, reason="Postgres not reachable — set TEST_DATABASE_URL"),
        ),
    ]
)
def backend(request, tmp_path: Path):
    """Yield ``(repository, queue_manager)`` sharing one database."""
    if request.param == "sqlite":
        db_path = str(tmp_path / "dlq.db")
        return SqlitePodcastRepository(db_path=db_path), QueueManager(db_path)

    from thestill.core.postgres_queue_manager import PostgresQueueManager
    from thestill.repositories.postgres_podcast_repository import PostgresPodcastRepository
    from thestill.repositories.postgres_schema import ensure_schema

    ensure_schema(PG_DSN)
    return PostgresPodcastRepository(PG_DSN), PostgresQueueManager(PG_DSN)


def _seed_failed_episodes(repository, count: int) -> list[str]:
    """Save one podcast with ``count`` episodes, each failed at transcribe.

    Ids are unique per call, so a shared Postgres needs no truncation.
    """
    nonce = uuid.uuid4().hex[:8]
    episodes = [
        Episode(
            external_id=f"dlq-{nonce}-{n}",
            title=f"DLQ episode {n}",
            description="",
            pub_date=datetime(2026, 1, n + 1, tzinfo=timezone.utc),
            audio_url=f"https://example.com/{nonce}/{n}.mp3",
            duration=60,
        )
        for n in range(count)
    ]
    repository.save(
        Podcast(
            rss_url=f"https://example.com/{nonce}/feed.xml",
            title=f"DLQ feed {nonce}",
            slug=f"dlq-feed-{nonce}",
            description="",
            episodes=episodes,
        )
    )
    for ep in episodes:
        repository.mark_episode_failed(ep.id, "transcribe", "fatal: unsupported content type", "fatal")
    return [ep.id for ep in episodes]


def _dead_task(queue_manager, episode_id: str) -> str:
    task = queue_manager.add_task(episode_id=episode_id, stage=TaskStage.TRANSCRIBE)
    queue_manager.mark_dead(task.id, "fatal: unsupported content type")
    return task.id


def test_retry_all_requeues_and_clears_failure_banners(backend):
    repository, queue_manager = backend
    episode_ids = _seed_failed_episodes(repository, 2)
    task_ids = [_dead_task(queue_manager, eid) for eid in episode_ids]

    client, state = auth_client(ROUTERS, multi_user=True, current_user=ADMIN_USER)
    state.repository = repository
    state.queue_manager = queue_manager

    # Scope to this test's tasks: a shared Postgres may hold other suites' DLQ rows.
    resp = client.post("/api/commands/dlq/retry-all", json={"task_ids": task_ids})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert sorted(body["task_ids"]) == sorted(task_ids)
    assert body["retried"] == 2
    assert body["skipped"] == 0
    for task_id in task_ids:
        assert queue_manager.get_task(task_id).status == TaskStatus.PENDING
    for episode_id in episode_ids:
        _, episode = repository.get_episode(episode_id)
        assert episode.failed_at_stage is None
        assert episode.failure_reason is None
        assert episode.failed_at is None
