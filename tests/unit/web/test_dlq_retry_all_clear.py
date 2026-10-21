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

"""``POST /dlq/retry-all`` failure-banner clears after the requeue (spec #107 Phase 1).

The repository is autospecced from the real backend classes, not a bare
``MagicMock``: a bare mock answers any attribute, which is how a method that
only SQLite implemented reached production and 500'd the endpoint on Postgres.
"""

import sqlite3
from unittest.mock import create_autospec

import pytest

from thestill.core.queue_manager import Task, TaskStage, TaskStatus
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.web.routes import api_commands

from .auth_harness import ADMIN_USER, auth_client

psycopg = pytest.importorskip("psycopg")

from thestill.repositories.postgres_podcast_repository import PostgresPodcastRepository  # noqa: E402

ROUTERS = [(api_commands.router, "/api/commands"), (api_commands.admin_router, "/api/commands")]

EPISODE_TASK = Task(id="task-ep", episode_id="ep-1", stage=TaskStage.TRANSCRIBE, status=TaskStatus.DEAD)
FEED_TASK = Task(id="task-feed", podcast_id="pod-1", stage=TaskStage.REFRESH_FEED, status=TaskStatus.DEAD)


def _client(repository_cls):
    client, state = auth_client(ROUTERS, multi_user=True, current_user=ADMIN_USER)
    state.repository = create_autospec(repository_cls, instance=True)
    state.queue_manager.get_dead_tasks.return_value = [EPISODE_TASK, FEED_TASK]
    state.queue_manager.retry_dead_tasks.return_value = [EPISODE_TASK.id, FEED_TASK.id]
    return client, state


@pytest.mark.parametrize("repository_cls", [SqlitePodcastRepository, PostgresPodcastRepository])
def test_retry_all_clears_episode_and_feed_failures_on_both_backends(repository_cls):
    client, state = _client(repository_cls)

    resp = client.post("/api/commands/dlq/retry-all")

    assert resp.status_code == 200, resp.text
    assert resp.json()["retried"] == 2
    state.repository.clear_episode_failures.assert_called_once_with(["ep-1"])
    args, _ = state.repository.clear_podcast_refresh_failures.call_args
    assert args[0] == ["pod-1"]


@pytest.mark.parametrize(
    "error",
    [sqlite3.OperationalError("database is locked"), psycopg.OperationalError("lock timeout")],
    ids=["sqlite", "postgres"],
)
def test_a_lost_episode_clear_still_returns_200_and_clears_feeds(error):
    client, state = _client(PostgresPodcastRepository)
    state.repository.clear_episode_failures.side_effect = error

    resp = client.post("/api/commands/dlq/retry-all")

    assert resp.status_code == 200, resp.text
    assert resp.json()["retried"] == 2
    state.repository.clear_podcast_refresh_failures.assert_called_once()


def test_a_bug_in_the_clear_is_not_swallowed():
    # Only lock/timeout/connection errors are tolerated; anything else is a bug
    # and must surface rather than degrade silently.
    client, state = _client(PostgresPodcastRepository)
    state.repository.clear_episode_failures.side_effect = RuntimeError("boom")

    resp = client.post("/api/commands/dlq/retry-all")

    assert resp.status_code == 500
