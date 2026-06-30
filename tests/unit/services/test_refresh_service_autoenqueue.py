"""Inline refresh auto-enqueues newly discovered episodes for the full pipeline.

The queued ``handle_refresh_feed`` path already fans out new episodes into the
pipeline; these tests pin the parity behaviour for the *inline* path
(``RefreshService.refresh`` — used by the CLI ``thestill refresh``, the web
"Refresh" button, and add-podcast). The service is wired with a real
``QueueManager`` + repo against a temp DB and a stubbed ``feed_manager`` (no
network), so we can assert real task rows.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from thestill.core.queue_manager import QueueManager, TaskStage
from thestill.models.podcast import Episode, Podcast
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.services.refresh_service import RefreshService

PODCAST_ID = "00000000-0000-0000-0000-000000000001"
EPISODE_ID = "11111111-1111-1111-1111-111111111111"
RSS = "https://example.com/feed.xml"


@pytest.fixture
def db(tmp_path: Path) -> str:
    """DB seeded with one podcast whose single episode is DISCOVERED-unqueued."""
    db_path = str(tmp_path / "refresh_autoenqueue.db")
    repo = SqlitePodcastRepository(db_path=db_path)
    repo.save(
        Podcast(
            id=PODCAST_ID,
            rss_url=RSS,
            title="Auto-enqueue Podcast",
            description="",
            episodes=[
                Episode(
                    id=EPISODE_ID,
                    external_id="ep-1",
                    title="Ep 1",
                    description="",
                    pub_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    audio_url="https://example.com/ep1.mp3",
                    duration=60,
                )
            ],
        )
    )
    return db_path


def _refresh_service(db: str, *, wired: bool, provider: str = ""):
    """Build a RefreshService whose stubbed feed_manager reports one new episode."""
    repo = SqlitePodcastRepository(db)
    podcast, _ = repo.get_podcast_for_refresh(PODCAST_ID)

    feed_manager = MagicMock()
    feed_manager.repository = repo
    # The episode list only needs to be non-empty — the auto-enqueue helper
    # drives off DB state (get_discovered_unqueued_episodes), not this list.
    feed_manager.refresh_feeds.return_value = SimpleNamespace(
        episodes_by_podcast=[(podcast, [_stub_ep()])],
        podcasts_with_errors=0,
    )

    config = SimpleNamespace(transcription_provider=provider, inbox_seed_on_follow=2)
    queue_manager = QueueManager(db) if wired else None
    svc = RefreshService(
        feed_manager,
        MagicMock(),
        queue_manager=queue_manager,
        config=config if wired else None,
    )
    return svc, repo, queue_manager


def _stub_ep() -> Episode:
    return Episode(
        id=EPISODE_ID,
        external_id="ep-1",
        title="Ep 1",
        description="",
        audio_url="https://example.com/ep1.mp3",
        duration=1,
    )


def test_inline_refresh_enqueues_discovered_episode(db: str):
    svc, _repo, qm = _refresh_service(db, wired=True)

    svc.refresh(dry_run=False)

    # Default provider → first stage is DOWNLOAD, full-pipeline chained.
    task = qm.get_next_task(stage=TaskStage.DOWNLOAD)
    assert task is not None
    assert task.episode_id == EPISODE_ID
    assert task.metadata.get("run_full_pipeline") is True
    assert task.metadata.get("initiated_by") == "refresh"


def test_dalston_provider_starts_at_transcribe(db: str):
    svc, _repo, qm = _refresh_service(db, wired=True, provider="dalston")

    svc.refresh(dry_run=False)

    # Dalston fetches the URL itself → skip download, start at TRANSCRIBE.
    assert qm.get_next_task(stage=TaskStage.DOWNLOAD) is None
    assert qm.get_next_task(stage=TaskStage.TRANSCRIBE) is not None


def test_dry_run_does_not_enqueue(db: str):
    svc, _repo, qm = _refresh_service(db, wired=True)

    svc.refresh(dry_run=True)

    assert qm.get_next_task(stage=TaskStage.DOWNLOAD) is None
    assert qm.get_next_task(stage=TaskStage.TRANSCRIBE) is None


def test_legacy_unwired_service_is_discover_only(db: str):
    # No queue_manager/config → refresh stays discover-and-persist (no crash).
    svc, _repo, _qm = _refresh_service(db, wired=False)
    probe = QueueManager(db)

    result = svc.refresh(dry_run=False)

    assert result.total_episodes >= 1
    assert probe.get_next_task(stage=TaskStage.DOWNLOAD) is None


def test_enqueue_is_idempotent_across_two_refreshes(db: str):
    svc, _repo, qm = _refresh_service(db, wired=True)

    svc.refresh(dry_run=False)
    first = qm.get_next_task(stage=TaskStage.DOWNLOAD)
    assert first is not None

    # A second refresh that re-reports the same episode must not double-enqueue
    # (get_discovered_unqueued_episodes excludes episodes that already have a
    # task row; enqueue_full_pipeline coalesces on has_pending_task).
    svc.refresh(dry_run=False)
    import sqlite3

    con = sqlite3.connect(db)
    n = con.execute(
        "SELECT COUNT(*) FROM tasks WHERE episode_id=? AND stage=?",
        (EPISODE_ID, TaskStage.DOWNLOAD.value),
    ).fetchone()[0]
    con.close()
    assert n == 1
