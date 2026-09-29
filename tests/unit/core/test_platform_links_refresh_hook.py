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

"""Spec #87 — the platform-link pass hangs off both refresh paths, best-effort."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from thestill.core.queue_manager import QueueManager, Task, TaskStage, TaskStatus
from thestill.core.refresh_failure import RefreshAttemptResult
from thestill.core.task_handlers import handle_refresh_feed
from thestill.models.podcast import Episode, Podcast
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.services.refresh_service import RefreshService

PODCAST_ID = "00000000-0000-0000-0000-000000000087"


def _seed(tmp_path) -> str:
    db = str(tmp_path / "hook.db")
    SqlitePodcastRepository(db_path=db).save(
        Podcast(
            id=PODCAST_ID,
            rss_url="https://example.com/feed.xml",
            title="Hook Podcast",
            description="",
            episodes=[
                Episode(
                    external_id="ep-1",
                    title="Ep 1",
                    description="",
                    pub_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    audio_url="https://example.com/ep1.mp3",
                )
            ],
        )
    )
    # Spec #63 — the refresh loader skips feeds nobody follows; seed one.
    import sqlite3
    import uuid

    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys = OFF")
    con.execute(
        "INSERT INTO podcast_followers (id, user_id, podcast_id, created_at) VALUES (?, ?, ?, ?)",
        (str(uuid.uuid4()), str(uuid.uuid4()), PODCAST_ID, datetime.now(timezone.utc).isoformat()),
    )
    con.commit()
    con.close()
    return db


def _queued_state(db, platform_link_service):
    repo = SqlitePodcastRepository(db)
    podcast, _ = repo.get_podcast_for_refresh(PODCAST_ID)
    fm = MagicMock()
    fm._refresh_single_podcast.return_value = RefreshAttemptResult(podcast=podcast, conditional_hit=True)
    state = SimpleNamespace(
        repository=repo,
        queue_manager=QueueManager(db),
        feed_manager=fm,
        config=SimpleNamespace(max_episodes_per_podcast=None, transcription_provider="whisper"),
    )
    if platform_link_service is not None:
        state.platform_link_service = platform_link_service
    return state


def _task():
    return Task(id="h" * 36, podcast_id=PODCAST_ID, stage=TaskStage.REFRESH_FEED, status=TaskStatus.PROCESSING)


class TestQueuedRefresh:
    def test_runs_the_pass_for_the_refreshed_podcast(self, tmp_path):
        service = MagicMock()
        handle_refresh_feed(_task(), _queued_state(_seed(tmp_path), service))
        (call,) = service.link_podcast.call_args_list
        assert call.args[0].id == PODCAST_ID

    def test_a_failing_pass_never_fails_the_refresh(self, tmp_path):
        service = MagicMock()
        service.link_podcast.side_effect = RuntimeError("iTunes down")
        state = _queued_state(_seed(tmp_path), service)
        handle_refresh_feed(_task(), state)  # no raise
        assert state.repository.get_podcast_for_refresh(PODCAST_ID) is not None

    def test_bare_state_without_the_service_is_fine(self, tmp_path):
        handle_refresh_feed(_task(), _queued_state(_seed(tmp_path), None))


class TestInlineRefresh:
    def _service(self, db, platform_link_service):
        repo = SqlitePodcastRepository(db)
        podcast = repo.get_all()[0]
        fm = MagicMock()
        fm.repository = repo
        fm.refresh_feeds.return_value = SimpleNamespace(
            episodes_by_podcast=[(podcast, [podcast.episodes[0]])], podcasts_with_errors=0
        )
        return RefreshService(fm, MagicMock(), platform_link_service=platform_link_service)

    def test_runs_once_per_refreshed_podcast(self, tmp_path):
        service = MagicMock()
        self._service(_seed(tmp_path), service).refresh()
        assert [c.args[0].id for c in service.link_podcast.call_args_list] == [PODCAST_ID]

    def test_runs_even_when_the_feed_had_nothing_new(self, tmp_path):
        """A 304 still lets an expired not-found marker retry (review of #270)."""
        db = _seed(tmp_path)
        service = MagicMock()
        fm = MagicMock()
        fm.repository = SqlitePodcastRepository(db)
        fm.refresh_feeds.return_value = SimpleNamespace(episodes_by_podcast=[], podcasts_with_errors=0)
        result = RefreshService(fm, MagicMock(), platform_link_service=service).refresh()
        assert result.total_episodes == 0
        assert [c.args[0].id for c in service.link_podcast.call_args_list] == [PODCAST_ID]

    def test_a_single_podcast_refresh_links_only_that_podcast(self, tmp_path):
        db = _seed(tmp_path)
        service = MagicMock()
        fm = MagicMock()
        fm.repository = SqlitePodcastRepository(db)
        fm.refresh_feeds.return_value = SimpleNamespace(episodes_by_podcast=[], podcasts_with_errors=0)
        podcast_service = MagicMock()
        podcast_service.get_podcast.return_value = fm.repository.get_all()[0]
        RefreshService(fm, podcast_service, platform_link_service=service).refresh(podcast_id=PODCAST_ID)
        assert [c.args[0].id for c in service.link_podcast.call_args_list] == [PODCAST_ID]

    def test_skipped_on_dry_run(self, tmp_path):
        service = MagicMock()
        self._service(_seed(tmp_path), service).refresh(dry_run=True)
        service.link_podcast.assert_not_called()

    def test_failure_is_swallowed(self, tmp_path):
        service = MagicMock()
        service.link_podcast.side_effect = RuntimeError("boom")
        result = self._service(_seed(tmp_path), service).refresh()
        assert result.total_episodes == 1
