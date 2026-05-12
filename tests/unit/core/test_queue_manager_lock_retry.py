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

"""Lock-retry coverage for QueueManager bookkeeping writes.

The wedged-task incident behind this suite: a clean-stage handler finished
its work (transcript + facts saved to disk) but the follow-up
``UPDATE tasks SET status='completed'`` lost a 5s ``busy_timeout`` race
against concurrent reindex writers. The worker's catch-all then tried
``schedule_retry`` — that write hit the same lock and raised, leaving the
row in ``processing`` forever and the pipeline blocked.

``QueueManager._exec_with_lock_retry`` retries ``database is locked``
``OperationalError``s with backoff so the bookkeeping landing is
independent of any one writer's burst. These tests pin down: (1) lock
errors retry, (2) non-lock errors don't, (3) we eventually give up.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from thestill.core.queue_manager import QueueManager, TaskStage
from thestill.models.podcast import Episode, Podcast
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository


@pytest.fixture
def queue_manager(tmp_path: Path) -> QueueManager:
    """Real DB with one episode so ``add_task`` FK passes."""
    db_path = str(tmp_path / "test.db")
    repo = SqlitePodcastRepository(db_path=db_path)
    repo.save(
        Podcast(
            id="00000000-0000-0000-0000-000000000001",
            rss_url="https://example.com/feed.xml",
            title="Lock Retry Test",
            description="",
            episodes=[
                Episode(
                    id="11111111-1111-1111-1111-111111111111",
                    external_id="ep-1",
                    title="Ep 1",
                    description="",
                    pub_date=datetime(2026, 1, 1),
                    audio_url="https://example.com/ep1.mp3",
                    duration=60,
                ),
            ],
        )
    )
    return QueueManager(db_path)


def test_lock_retry_recovers_after_transient_lock(queue_manager, monkeypatch):
    """``database is locked`` errors are retried; first success wins."""
    monkeypatch.setattr("thestill.core.queue_manager.time.sleep", lambda _s: None)

    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    result = queue_manager._exec_with_lock_retry("test_op", flaky)

    assert result == "ok"
    assert attempts["n"] == 3


def test_lock_retry_propagates_non_lock_errors_immediately(queue_manager, monkeypatch):
    """Non-lock OperationalErrors (schema, constraint, etc.) must not retry."""
    monkeypatch.setattr("thestill.core.queue_manager.time.sleep", lambda _s: None)

    attempts = {"n": 0}

    def boom():
        attempts["n"] += 1
        raise sqlite3.OperationalError("no such table: tasks")

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        queue_manager._exec_with_lock_retry("test_op", boom)

    assert attempts["n"] == 1, "non-lock errors must not retry"


def test_lock_retry_gives_up_after_exhausting_budget(queue_manager, monkeypatch):
    """Persistent lock failure ultimately propagates so the caller can react."""
    monkeypatch.setattr("thestill.core.queue_manager.time.sleep", lambda _s: None)

    attempts = {"n": 0}

    def always_locked():
        attempts["n"] += 1
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError, match="locked"):
        queue_manager._exec_with_lock_retry("test_op", always_locked)

    # Budget = len(_LOCK_RETRY_DELAYS) sleeps + one final attempt that raises.
    from thestill.core.queue_manager import _LOCK_RETRY_DELAYS

    assert attempts["n"] == len(_LOCK_RETRY_DELAYS) + 1


def test_complete_task_survives_one_transient_lock(queue_manager, monkeypatch):
    """End-to-end: ``complete_task`` recovers from a transient lock.

    Wraps ``QueueManager._get_connection`` so the first call throws
    ``database is locked`` and subsequent calls go through. The fix is
    the retry helper inside ``complete_task``; without it the UPDATE
    would propagate the lock error and the row would stay in
    ``processing``.
    """
    monkeypatch.setattr("thestill.core.queue_manager.time.sleep", lambda _s: None)

    task = queue_manager.add_task(
        episode_id="11111111-1111-1111-1111-111111111111",
        stage=TaskStage.CLEAN,
    )
    # Mark processing so we have a realistic in-progress row to complete.
    claimed = queue_manager.get_next_task(stage=TaskStage.CLEAN)
    assert claimed is not None and claimed.id == task.id

    real_get_connection = QueueManager._get_connection
    calls = {"n": 0}

    def flaky_get_connection(self):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_get_connection(self)

    monkeypatch.setattr(QueueManager, "_get_connection", flaky_get_connection)

    queue_manager.complete_task(task.id)

    assert calls["n"] >= 2, "first attempt should have raised, retry should have succeeded"
    refreshed = queue_manager.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status.value == "completed"
