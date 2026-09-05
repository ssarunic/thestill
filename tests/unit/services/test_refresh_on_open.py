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

"""Spec #74 — ``RefreshOnOpenService`` guard matrix against a real SQLite
repository + queue (no mocks, spec #42 FM-5)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from thestill.core.queue_manager import QueueManager, TaskStage
from thestill.models.podcast import Podcast
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.services.refresh_on_open import OPEN_REFRESH_PRIORITY, RefreshOnOpenOutcome, RefreshOnOpenService

PODCAST_ID = "22222222-2222-2222-2222-222222222222"
NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path: Path) -> str:
    db_path = str(tmp_path / "refresh_on_open.db")
    SqlitePodcastRepository(db_path=db_path).save(
        Podcast(
            id=PODCAST_ID,
            rss_url="https://example.com/feed.xml",
            title="Opened Podcast",
            description="",
            last_processed=NOW - timedelta(days=40),
            episodes=[],
        )
    )
    return db_path


def _service(db: str, *, min_interval: int = 900, enabled: bool = True):
    repo = SqlitePodcastRepository(db)
    qm = QueueManager(db)
    return RefreshOnOpenService(repo, qm, min_interval_seconds=min_interval, enabled=enabled), qm


def _sql(db: str, sql: str, params=()) -> None:
    con = sqlite3.connect(db)
    con.execute(sql, params)
    con.commit()
    con.close()


def test_first_open_enqueues_a_high_priority_refresh(db: str):
    svc, qm = _service(db)

    assert svc.maybe_trigger(PODCAST_ID, now=NOW) is RefreshOnOpenOutcome.ENQUEUED

    task = qm.get_next_task(stage=TaskStage.REFRESH_FEED)
    assert task is not None
    assert task.podcast_id == PODCAST_ID
    assert task.priority == OPEN_REFRESH_PRIORITY
    assert task.metadata.get("initiated_by") == "open"


def test_reopen_while_in_flight_coalesces(db: str):
    svc, qm = _service(db)
    assert svc.maybe_trigger(PODCAST_ID, now=NOW) is RefreshOnOpenOutcome.ENQUEUED

    outcome = svc.maybe_trigger(PODCAST_ID, now=NOW)

    assert outcome is RefreshOnOpenOutcome.COALESCED
    assert outcome.pending is True
    # Exactly one task — the queue's uniqueness guard held.
    con = sqlite3.connect(db)
    (count,) = con.execute("SELECT count(*) FROM tasks WHERE podcast_id = ?", (PODCAST_ID,)).fetchone()
    con.close()
    assert count == 1


def test_throttled_within_min_interval_then_allowed(db: str):
    svc, _qm = _service(db, min_interval=900)
    _sql(
        db,
        "UPDATE podcasts SET last_refresh_at = ? WHERE id = ?",
        ((NOW - timedelta(minutes=5)).isoformat(), PODCAST_ID),
    )

    throttled = svc.maybe_trigger(PODCAST_ID, now=NOW)
    assert throttled is RefreshOnOpenOutcome.THROTTLED
    assert throttled.pending is False

    _sql(
        db,
        "UPDATE podcasts SET last_refresh_at = ? WHERE id = ?",
        ((NOW - timedelta(minutes=16)).isoformat(), PODCAST_ID),
    )
    assert svc.maybe_trigger(PODCAST_ID, now=NOW) is RefreshOnOpenOutcome.ENQUEUED


def test_failed_refresh_also_throttles(db: str):
    """``last_refresh_at`` is written on failure too, so a dead feed opened
    repeatedly is retried at most once per interval, never on every open."""
    svc, _qm = _service(db)
    _sql(
        db,
        "UPDATE podcasts SET last_refresh_at = ?, last_refresh_error = ? WHERE id = ?",
        ((NOW - timedelta(minutes=1)).isoformat(), "boom", PODCAST_ID),
    )
    assert svc.maybe_trigger(PODCAST_ID, now=NOW) is RefreshOnOpenOutcome.THROTTLED


def test_half_imported_podcast_is_refreshed_on_open(db: str):
    """``last_processed`` is a watermark, not a lifecycle lock: a row whose
    first discovery failed (or whose feed has no dated episodes) still gets
    a refresh on open instead of being suppressed forever."""
    svc, qm = _service(db)
    _sql(db, "UPDATE podcasts SET last_processed = NULL WHERE id = ?", (PODCAST_ID,))

    assert svc.maybe_trigger(PODCAST_ID, now=NOW) is RefreshOnOpenOutcome.ENQUEUED
    assert qm.get_next_task(stage=TaskStage.REFRESH_FEED) is not None


def test_server_retry_after_suppresses_opens_until_it_expires(db: str):
    """A 429/5xx ``Retry-After`` recorded by the handler outranks the
    15-minute floor: opens are suppressed until the server-directed
    cooldown expires, then allowed again."""
    svc, qm = _service(db, min_interval=900)
    _sql(
        db,
        "UPDATE podcasts SET last_refresh_at = ?, refresh_retry_after_at = ? WHERE id = ?",
        ((NOW - timedelta(minutes=30)).isoformat(), (NOW + timedelta(hours=1)).isoformat(), PODCAST_ID),
    )
    backing_off = svc.maybe_trigger(PODCAST_ID, now=NOW)
    assert backing_off is RefreshOnOpenOutcome.BACKING_OFF
    assert backing_off.pending is False
    assert qm.get_next_task(stage=TaskStage.REFRESH_FEED) is None

    assert svc.maybe_trigger(PODCAST_ID, now=NOW + timedelta(hours=1, seconds=1)) is RefreshOnOpenOutcome.ENQUEUED


def test_open_promotes_a_queued_scheduler_task(db: str):
    """A scheduler-enqueued refresh sits at priority 0; an open coalesces
    onto it AND lifts it to reader priority so it does not wait behind a
    scheduler burst."""
    svc, qm = _service(db)
    scheduled = qm.add_feed_task(PODCAST_ID, TaskStage.REFRESH_FEED)
    assert scheduled is not None and scheduled.priority == 0

    assert svc.maybe_trigger(PODCAST_ID, now=NOW) is RefreshOnOpenOutcome.COALESCED
    assert qm.get_task(scheduled.id).priority == OPEN_REFRESH_PRIORITY


def test_open_leaves_a_processing_task_alone(db: str):
    svc, qm = _service(db)
    scheduled = qm.add_feed_task(PODCAST_ID, TaskStage.REFRESH_FEED)
    claimed = qm.get_next_task(stage=TaskStage.REFRESH_FEED)
    assert claimed is not None and claimed.id == scheduled.id

    outcome = svc.maybe_trigger(PODCAST_ID, now=NOW)

    assert outcome is RefreshOnOpenOutcome.COALESCED
    assert outcome.pending is True
    assert qm.get_task(scheduled.id).priority == 0


def test_quarantined_feed_is_never_probed_by_an_open(db: str):
    svc, qm = _service(db)
    _sql(db, "UPDATE podcasts SET refresh_disabled_reason = 'auth_required' WHERE id = ?", (PODCAST_ID,))

    assert svc.maybe_trigger(PODCAST_ID, now=NOW) is RefreshOnOpenOutcome.QUARANTINED
    assert qm.get_next_task(stage=TaskStage.REFRESH_FEED) is None


def test_unknown_podcast_and_disabled_flag(db: str):
    svc, qm = _service(db)
    assert svc.maybe_trigger("no-such-id", now=NOW) is RefreshOnOpenOutcome.NOT_FOUND

    off, qm_off = _service(db, enabled=False)
    assert off.maybe_trigger(PODCAST_ID, now=NOW) is RefreshOnOpenOutcome.DISABLED
    assert qm_off.get_next_task(stage=TaskStage.REFRESH_FEED) is None
    assert qm.get_next_task(stage=TaskStage.REFRESH_FEED) is None
