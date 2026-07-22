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

"""Spec #60 — regression for the 2026-07-15 incident.

A laptop-hosted instance lost network for ~2 days; every REFRESH_FEED task
burned its retry budget against the dead network and ALL 90 feeds were
terminally parked (``next_refresh_at = NULL``), stalling discovery for a
week until an operator intervened.

These tests reproduce the shape of the incident against the REAL SQLite
repository (no consistent-mock, per #42): a fleet of feeds failing with
connectivity errors across a multi-day offline window must end with ZERO
parked feeds, and discovery must self-resume when the network returns.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from thestill.core.queue_manager import TaskStage
from thestill.core.refresh_failure import RefreshFailure, RefreshFailureKind, RefreshPolicySettings
from thestill.core.task_worker import TaskWorker
from thestill.models.podcast import Podcast
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.utils.exceptions import TransientError

SETTINGS = RefreshPolicySettings(min_interval_seconds=900, max_interval_seconds=86400, default_interval_seconds=3600)
CONNECTIVITY = RefreshFailure(
    kind=RefreshFailureKind.CONNECTIVITY,
    exception="[Errno 8] nodename nor servname provided, or not known",
)


@pytest.fixture
def repo(tmp_path: Path) -> SqlitePodcastRepository:
    return SqlitePodcastRepository(db_path=str(tmp_path / "incident.db"))


def _seed_fleet(repo: SqlitePodcastRepository, count: int, base: datetime) -> list[str]:
    ids = []
    for n in range(count):
        pid = f"00000000-0000-0000-0000-{n:012d}"
        repo.save(
            Podcast(
                id=pid,
                rss_url=f"https://example{n}.com/feed.xml",
                title=f"Feed {n}",
                description="",
                episodes=[],
            )
        )
        ids.append(pid)
    repo.seed_unscheduled_feeds(3600, now=base)
    return ids


def test_incident_regression_offline_window_parks_nothing(repo):
    """90 feeds, every attempt failing with a connectivity error across a
    simulated 2-day offline window (attempt + 3 queue retries per scheduler
    firing, several firings) → ZERO feeds parked; discovery self-resumes on
    the first success after the network returns."""
    base = datetime(2026, 7, 15, 9, 0, 0, tzinfo=timezone.utc)
    ids = _seed_fleet(repo, 90, base)

    # Offline window: several waves of failures, each wave = the handler's
    # per-attempt write firing for the initial attempt and every retry.
    for wave in range(6):  # spread over ~2 days
        at = base + timedelta(hours=8 * wave)
        for pid in ids:
            for attempt in range(4):  # initial + 3 retries
                repo.record_refresh_failure(pid, CONNECTIVITY, SETTINGS, now=at + timedelta(seconds=attempt * 30))

    # THE incident assertion: nothing parked, everything still scheduled.
    counts = repo.get_refresh_health_counts(now=base + timedelta(days=2))
    assert counts["parked_total"] == 0
    assert counts["active"] == 90
    assert counts["backing_off"] == 90

    # Network returns: every feed is due within one max interval (24h) of the
    # last failure — no operator action needed.
    due = repo.get_due_podcasts(now=base + timedelta(days=4), limit=500)
    assert len(due) == 90

    # First successful refresh clears the failure state entirely.
    for pid in ids:
        repo.record_refresh_success(pid, found_new=True, min_interval=900, max_interval=86400, default_interval=3600)
    counts = repo.get_refresh_health_counts()
    assert counts["backing_off"] == 0
    assert counts["parked_total"] == 0


def _worker(repo: SqlitePodcastRepository) -> TaskWorker:
    return TaskWorker(queue_manager=MagicMock(), task_handlers={}, repository=repo)


def _feed_task(pid: str):
    task = MagicMock()
    task.stage = TaskStage.REFRESH_FEED
    task.podcast_id = pid
    task.episode_id = None
    return task


def test_worker_exhaustion_skips_write_when_handler_recorded(repo):
    """The worker's exhaustion path must NOT double-apply policy when the
    handler already recorded this attempt's failure (C+ idempotency)."""
    base = datetime(2026, 7, 15, 9, 0, 0, tzinfo=timezone.utc)
    (pid,) = _seed_fleet(repo, 1, base)
    repo.record_refresh_failure(pid, CONNECTIVITY, SETTINGS, now=base)

    import sqlite3

    def snapshot():
        con = sqlite3.connect(str(repo.db_path))
        row = con.execute(
            "SELECT next_refresh_at, consecutive_refresh_failures, last_refresh_failure_kind "
            "FROM podcasts WHERE id=?",
            (pid,),
        ).fetchone()
        con.close()
        return row

    before = snapshot()
    exc = TransientError("Feed refresh failed: connectivity", error_class="infra")
    exc.refresh_failure_recorded = True
    _worker(repo)._mark_episode_failed(_feed_task(pid), str(exc), "transient", exc=exc)
    assert snapshot() == before  # observational only — no second write


def test_worker_exhaustion_fallback_records_internal_never_parks(repo):
    """When the handler never recorded (exception escaped classification),
    the worker records ONE internal fallback — visibility without parking."""
    base = datetime(2026, 7, 15, 9, 0, 0, tzinfo=timezone.utc)
    (pid,) = _seed_fleet(repo, 1, base)

    exc = RuntimeError("escaped before classification")
    _worker(repo)._mark_episode_failed(_feed_task(pid), str(exc), "transient", exc=exc)

    import sqlite3

    con = sqlite3.connect(str(repo.db_path))
    nxt, kind, reason = con.execute(
        "SELECT next_refresh_at, last_refresh_failure_kind, refresh_disabled_reason FROM podcasts WHERE id=?",
        (pid,),
    ).fetchone()
    con.close()
    assert nxt is not None  # never parked by our own bug
    assert kind == "internal"  # stamped for visibility
    assert reason is None
