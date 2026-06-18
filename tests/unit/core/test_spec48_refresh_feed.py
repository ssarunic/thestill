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

"""Spec #48 — REFRESH_FEED queued stage + background scheduling.

Covers the queue/schema/cadence invariants the review rounds pinned:

- nullable ``episode_id`` + ``podcast_id`` target, exactly-one CHECK
- per-feed coalescing (enqueue uniqueness guard)
- ``NULL NOT IN`` claim-starvation fix (feed task claimable while episodes active)
- terminal-failure park + operator re-arm
- adaptive (AIMD) cadence on success
- due-query + seed-unscheduled scheduling
- scheduler tick end to end
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from thestill.core.queue_manager import QueueManager, TaskStage, is_feed_scoped_stage
from thestill.core.refresh_scheduler import RefreshScheduler
from thestill.models.podcast import Episode, Podcast
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.utils.datetime_utils import now_utc

PODCAST_ID = "00000000-0000-0000-0000-000000000001"
EPISODE_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def db(tmp_path: Path) -> str:
    db_path = str(tmp_path / "spec48.db")
    repo = SqlitePodcastRepository(db_path=db_path)
    repo.save(
        Podcast(
            id=PODCAST_ID,
            rss_url="https://example.com/feed.xml",
            title="Spec48 Podcast",
            description="",
            episodes=[
                Episode(
                    id=EPISODE_ID,
                    external_id="ep-1",
                    title="Ep 1",
                    description="",
                    pub_date=datetime(2026, 1, 1),
                    audio_url="https://example.com/ep1.mp3",
                    duration=60,
                )
            ],
        )
    )
    return db_path


def _force_due(db_path: str, podcast_id: str = PODCAST_ID) -> None:
    past = (now_utc() - timedelta(seconds=10)).isoformat()
    con = sqlite3.connect(db_path)
    con.execute("UPDATE podcasts SET next_refresh_at=? WHERE id=?", (past, podcast_id))
    con.commit()
    con.close()


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------
def test_classifier(db: str) -> None:
    assert is_feed_scoped_stage(TaskStage.REFRESH_FEED)
    assert not is_feed_scoped_stage(TaskStage.DOWNLOAD)


def test_tasks_table_has_nullable_episode_and_target_check(db: str) -> None:
    QueueManager(db)  # triggers migration/rebuild
    con = sqlite3.connect(db)
    ddl = con.execute("SELECT sql FROM sqlite_master WHERE name='tasks'").fetchone()[0]
    con.close()
    assert "episode_id TEXT NOT NULL" not in ddl
    assert "podcast_id" in ddl
    assert "(episode_id IS NOT NULL)" in ddl


def test_check_rejects_both_and_neither(db: str) -> None:
    qm = QueueManager(db)
    con = sqlite3.connect(db)
    # neither target
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO tasks (id, episode_id, podcast_id, stage, status) VALUES (?,?,?,?,?)",
            ("a" * 36, None, None, "refresh-feed", "pending"),
        )
    # both targets
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO tasks (id, episode_id, podcast_id, stage, status) VALUES (?,?,?,?,?)",
            ("b" * 36, EPISODE_ID, PODCAST_ID, "refresh-feed", "pending"),
        )
    con.rollback()
    con.close()


# --------------------------------------------------------------------------
# Queue: add_feed_task / coalescing / claim
# --------------------------------------------------------------------------
def test_add_feed_task_roundtrips_with_null_episode(db: str) -> None:
    qm = QueueManager(db)
    task = qm.add_feed_task(PODCAST_ID, TaskStage.REFRESH_FEED)
    assert task is not None
    assert task.episode_id is None
    assert task.podcast_id == PODCAST_ID


def test_coalescing_skips_duplicate(db: str) -> None:
    qm = QueueManager(db)
    assert qm.add_feed_task(PODCAST_ID) is not None
    assert qm.add_feed_task(PODCAST_ID) is None  # coalesced
    assert qm.has_pending_feed_task(PODCAST_ID)


def test_feed_task_claimable_despite_episode_exclusion(db: str) -> None:
    """The ``NULL NOT IN`` starvation regression: a feed task must remain
    claimable even when episode ids are being excluded."""
    qm = QueueManager(db)
    qm.add_feed_task(PODCAST_ID)
    claimed = qm.get_next_task(
        stage=TaskStage.REFRESH_FEED,
        exclude_episode_ids={EPISODE_ID, "other-ep"},
    )
    assert claimed is not None
    assert claimed.podcast_id == PODCAST_ID


def test_exclude_podcast_ids_filters_feed_task(db: str) -> None:
    qm = QueueManager(db)
    qm.add_feed_task(PODCAST_ID)
    assert qm.get_next_task(stage=TaskStage.REFRESH_FEED, exclude_podcast_ids={PODCAST_ID}) is None


def test_add_feed_task_rejects_episode_scoped_stage(db: str) -> None:
    qm = QueueManager(db)
    with pytest.raises(ValueError):
        qm.add_feed_task(PODCAST_ID, TaskStage.DOWNLOAD)


# --------------------------------------------------------------------------
# Cadence / failure state
# --------------------------------------------------------------------------
def test_due_query_excludes_parked_and_future(db: str) -> None:
    repo = SqlitePodcastRepository(db)
    repo.seed_unscheduled_feeds(3600)
    # Seeded into the future -> not due yet.
    assert PODCAST_ID not in repo.get_due_podcasts()
    _force_due(db)
    assert PODCAST_ID in repo.get_due_podcasts()


def test_terminal_failure_parks_and_operator_rearms(db: str) -> None:
    repo = SqlitePodcastRepository(db)
    repo.seed_unscheduled_feeds(3600)
    _force_due(db)
    repo.record_refresh_error(PODCAST_ID, "boom", terminal=True)
    assert PODCAST_ID not in repo.get_due_podcasts()  # parked
    repo.clear_podcast_refresh_failure(PODCAST_ID, 3600)
    assert PODCAST_ID in repo.get_due_podcasts()  # re-armed


def test_retryable_error_does_not_park(db: str) -> None:
    repo = SqlitePodcastRepository(db)
    repo.seed_unscheduled_feeds(3600)
    _force_due(db)
    repo.record_refresh_error(PODCAST_ID, "transient", terminal=False)
    # Still scheduled (the task's own retry re-fetches); not parked.
    assert PODCAST_ID in repo.get_due_podcasts()


def test_aimd_shortens_on_new_and_lengthens_on_none(db: str) -> None:
    repo = SqlitePodcastRepository(db)
    repo.seed_unscheduled_feeds(3600)

    def interval() -> int:
        con = sqlite3.connect(db)
        v = con.execute("SELECT refresh_interval_seconds FROM podcasts WHERE id=?", (PODCAST_ID,)).fetchone()[0]
        con.close()
        return v

    repo.record_refresh_success(PODCAST_ID, found_new=True, min_interval=900, max_interval=86400, default_interval=3600)
    assert interval() == 1800  # 3600 // 2
    repo.record_refresh_success(
        PODCAST_ID, found_new=False, min_interval=900, max_interval=86400, default_interval=3600
    )
    assert interval() == 2700  # int(1800 * 1.5)


def test_aimd_clamps_to_min_and_max(db: str) -> None:
    repo = SqlitePodcastRepository(db)
    repo.seed_unscheduled_feeds(1000)
    # Repeated "found new" can't go below min.
    for _ in range(10):
        repo.record_refresh_success(
            PODCAST_ID, found_new=True, min_interval=900, max_interval=86400, default_interval=1000
        )
    con = sqlite3.connect(db)
    v = con.execute("SELECT refresh_interval_seconds FROM podcasts WHERE id=?", (PODCAST_ID,)).fetchone()[0]
    con.close()
    assert v == 900


def test_seed_does_not_revive_parked_feed(db: str) -> None:
    repo = SqlitePodcastRepository(db)
    repo.seed_unscheduled_feeds(3600)
    repo.record_refresh_error(PODCAST_ID, "dead", terminal=True)  # park (sets last_refresh_*)
    # Seeding again must NOT re-arm a parked feed.
    repo.seed_unscheduled_feeds(3600)
    assert PODCAST_ID not in repo.get_due_podcasts()


# --------------------------------------------------------------------------
# Scheduler tick
# --------------------------------------------------------------------------
def test_scheduler_tick_enqueues_due_feed(db: str) -> None:
    repo = SqlitePodcastRepository(db)
    qm = QueueManager(db)
    repo.seed_unscheduled_feeds(3600)
    _force_due(db)
    sched = RefreshScheduler(repo, qm, tick_seconds=60, default_interval_seconds=3600)
    enqueued = sched.tick()
    assert enqueued == 1
    assert qm.has_pending_feed_task(PODCAST_ID)
    # Idempotent: a second tick coalesces (the task is still pending).
    assert sched.tick() == 0


# --------------------------------------------------------------------------
# Handler: handle_refresh_feed contracts
# --------------------------------------------------------------------------
def _make_state(db: str, refresh_return, transcription_provider: str = "whisper"):
    """Build a minimal AppState-like object for handle_refresh_feed."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    repo = SqlitePodcastRepository(db)
    qm = QueueManager(db)
    fm = MagicMock()
    fm._refresh_single_podcast.return_value = refresh_return
    fm._save_transcript_links_for_episodes = MagicMock()
    state = SimpleNamespace(
        repository=repo,
        queue_manager=qm,
        feed_manager=fm,
        config=SimpleNamespace(max_episodes_per_podcast=None, transcription_provider=transcription_provider),
    )
    return state, repo, qm, fm


def test_handler_raises_on_had_error_and_enqueues_nothing(db: str) -> None:
    from thestill.core.queue_manager import Task, TaskStatus
    from thestill.core.task_handlers import handle_refresh_feed
    from thestill.utils.exceptions import TransientError

    repo0 = SqlitePodcastRepository(db)
    podcast, _ = repo0.get_podcast_for_refresh(PODCAST_ID)
    # had_error=True returned normally (batch contract) — handler must RAISE.
    state, repo, qm, _ = _make_state(db, (podcast, [], True, False, None, False))
    task = Task(id="c" * 36, podcast_id=PODCAST_ID, stage=TaskStage.REFRESH_FEED, status=TaskStatus.PROCESSING)

    with pytest.raises(TransientError):
        handle_refresh_feed(task, state)

    # No DOWNLOAD enqueued; error stamped (non-terminal, not parked).
    assert qm.get_next_task(stage=TaskStage.DOWNLOAD) is None
    con = sqlite3.connect(db)
    err, nxt = con.execute(
        "SELECT last_refresh_error, next_refresh_at FROM podcasts WHERE id=?", (PODCAST_ID,)
    ).fetchone()
    con.close()
    assert err is not None  # stamped


def test_handler_persists_reconciles_and_enqueues_download_at_priority(db: str) -> None:
    from thestill.core.queue_manager import Task, TaskStatus
    from thestill.core.task_handlers import handle_refresh_feed

    repo0 = SqlitePodcastRepository(db)
    podcast, _ = repo0.get_podcast_for_refresh(PODCAST_ID)
    new_ep = Episode(
        id="22222222-2222-2222-2222-222222222222",
        podcast_id=PODCAST_ID,
        external_id="ep-2",
        title="Ep 2",
        description="",
        pub_date=datetime(2026, 2, 1),
        audio_url="https://example.com/ep2.mp3",
        duration=60,
    )
    state, repo, qm, _ = _make_state(db, (podcast, [new_ep], False, False, None, False))
    task = Task(id="d" * 36, podcast_id=PODCAST_ID, stage=TaskStage.REFRESH_FEED, status=TaskStatus.PROCESSING)

    handle_refresh_feed(task, state)

    # Episode persisted + DOWNLOAD enqueued at fresh priority against the
    # reconciled id.
    resolved = repo.get_episode_by_external_id("https://example.com/feed.xml", "ep-2")
    assert resolved is not None
    dl = qm.get_next_task(stage=TaskStage.DOWNLOAD)
    assert dl is not None
    assert dl.episode_id == resolved.id
    assert dl.priority == 10
    assert dl.metadata.get("run_full_pipeline") is True
    # Success recorded: error cleared, schedule re-armed.
    con = sqlite3.connect(db)
    err = con.execute("SELECT last_refresh_error FROM podcasts WHERE id=?", (PODCAST_ID,)).fetchone()[0]
    con.close()
    assert err is None


def test_handler_starts_at_transcribe_for_dalston(db: str) -> None:
    """With Dalston, a freshly-discovered episode SKIPS local download/downsample
    and starts at TRANSCRIBE (Dalston fetches the audio URL itself)."""
    from thestill.core.queue_manager import Task, TaskStatus
    from thestill.core.task_handlers import handle_refresh_feed

    repo0 = SqlitePodcastRepository(db)
    podcast, _ = repo0.get_podcast_for_refresh(PODCAST_ID)
    new_ep = Episode(
        id="33333333-3333-3333-3333-333333333333",
        podcast_id=PODCAST_ID,
        external_id="ep-3",
        title="Ep 3",
        description="",
        pub_date=datetime(2026, 3, 1),
        audio_url="https://example.com/ep3.mp3",
        duration=60,
    )
    state, repo, qm, _ = _make_state(
        db, (podcast, [new_ep], False, False, None, False), transcription_provider="dalston"
    )
    task = Task(id="e" * 36, podcast_id=PODCAST_ID, stage=TaskStage.REFRESH_FEED, status=TaskStatus.PROCESSING)

    handle_refresh_feed(task, state)

    resolved = repo.get_episode_by_external_id("https://example.com/feed.xml", "ep-3")
    # No DOWNLOAD task; a TRANSCRIBE task at fresh priority instead.
    assert qm.get_next_task(stage=TaskStage.DOWNLOAD) is None
    tr = qm.get_next_task(stage=TaskStage.TRANSCRIBE)
    assert tr is not None
    assert tr.episode_id == resolved.id
    assert tr.priority == 10
