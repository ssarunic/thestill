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

"""Unit tests for ``BriefingService`` selection modes and shared finalize path."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from thestill.models.podcast import Episode, Podcast
from thestill.services.briefing_selector import BriefingSelectionCriteria
from thestill.services.briefing_service import BriefingService


@pytest.fixture
def podcast():
    return Podcast(
        id="podcast-1",
        title="Test Podcast",
        description="desc",
        rss_url="https://example.com/feed.xml",
        slug="test-podcast",
    )


@pytest.fixture
def episode():
    # ``Episode.state`` is a computed property — populate ``summary_path``
    # so the selector's SUMMARIZED filter accepts the row.
    return Episode(
        id="ep-1",
        title="Ep one",
        description="desc",
        audio_url="https://example.com/ep1.mp3",
        external_id="ep-1",
        slug="ep-one",
        summary_path="ep-one.summary.md",
        pub_date=datetime.now(timezone.utc) - timedelta(days=1),
    )


@pytest.fixture
def mock_repos(podcast, episode):
    briefing_repo = MagicMock()
    briefing_repo.get_all.return_value = []
    briefing_repo.save.return_value = None
    inbox_repo = MagicMock()
    podcast_repo = MagicMock()
    podcast_repo.get_episode.return_value = (podcast, episode)
    podcast_repo.get_all_episodes.return_value = ([(podcast, episode)], 1)
    return briefing_repo, inbox_repo, podcast_repo


@pytest.fixture
def mock_generator():
    gen = MagicMock()
    gen.generate.return_value = MagicMock(markdown="# briefing\n")
    gen.write.return_value = None
    return gen


@pytest.fixture
def mock_paths(tmp_path):
    paths = MagicMock()
    paths.briefing_file.side_effect = lambda name: tmp_path / name
    return paths


def _service(mock_repos, mock_generator, mock_paths, *, min_interval_seconds=0):
    briefing_repo, inbox_repo, podcast_repo = mock_repos
    return BriefingService(
        briefing_repo,
        inbox_repo,
        podcast_repo,
        mock_generator,
        mock_paths,
        min_interval_seconds=min_interval_seconds,
    )


class TestGenerateFromCriteria:
    """``generate_from_criteria`` consolidates the morning-briefing path."""

    def test_returns_none_when_selector_finds_nothing(self, mock_repos, mock_generator, mock_paths):
        briefing_repo, _inbox, podcast_repo = mock_repos
        podcast_repo.get_all_episodes.return_value = ([], 0)

        service = _service(mock_repos, mock_generator, mock_paths)
        criteria = BriefingSelectionCriteria(since_days=7, max_episodes=10, ready_only=True)

        result = service.generate_from_criteria("user-1", criteria)

        assert result is None
        mock_generator.generate.assert_not_called()
        mock_generator.write.assert_not_called()
        briefing_repo.save.assert_not_called()

    def test_writes_renders_and_saves_when_selector_has_episodes(self, mock_repos, mock_generator, mock_paths, episode):
        """Happy path: render → write → save, and the briefing carries
        the criteria's ``date_from`` as ``period_start``."""
        briefing_repo, _inbox, _podcast_repo = mock_repos
        service = _service(mock_repos, mock_generator, mock_paths)
        criteria = BriefingSelectionCriteria(since_days=3, max_episodes=10, ready_only=True)
        now = datetime(2026, 5, 13, 8, 0, tzinfo=timezone.utc)

        briefing = service.generate_from_criteria("user-1", criteria, now=now)

        assert briefing is not None
        assert briefing.user_id == "user-1"
        assert briefing.episode_ids == [episode.id]
        assert briefing.episodes_total == 1
        assert briefing.episodes_completed == 1
        # period_start is the selector's cutoff (criteria.date_from), not
        # the inbox cursor. The selector resolves date_from off the real
        # clock so we only verify the relationship, not an exact value.
        assert briefing.period_start < briefing.period_end
        assert briefing.period_end == now
        # File path is collision-safe (id suffix) so two same-second runs differ.
        assert briefing.file_path is not None
        assert briefing.file_path.startswith("briefing_20260513_080000_")
        assert briefing.file_path.endswith(".md")
        mock_generator.generate.assert_called_once()
        mock_generator.write.assert_called_once()
        briefing_repo.save.assert_called_once_with(briefing)

    def test_bypasses_throttle(self, mock_repos, mock_generator, mock_paths):
        """Criteria-driven path runs on demand even when an inbox-driven
        briefing exists inside the throttle window."""
        briefing_repo, _inbox, _podcast_repo = mock_repos
        recent = MagicMock()
        recent.period_end = datetime.now(timezone.utc) - timedelta(minutes=5)
        recent.created_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        briefing_repo.get_all.return_value = [recent]

        service = _service(mock_repos, mock_generator, mock_paths, min_interval_seconds=3600)
        criteria = BriefingSelectionCriteria(since_days=7, max_episodes=10, ready_only=True)

        result = service.generate_from_criteria("user-1", criteria)

        assert result is not None
        # Throttle only gates the inbox-driven path; the criteria path
        # is explicit and always renders when episodes match.
        briefing_repo.save.assert_called_once()


class TestGenerateForUserStillRenders:
    """Smoke check that the inbox-driven path still uses the shared
    render-write-save helper after the refactor."""

    def test_renders_and_saves(self, mock_repos, mock_generator, mock_paths, episode):
        briefing_repo, inbox_repo, _podcast_repo = mock_repos
        inbox_repo.list_episode_ids_in_window.return_value = [episode.id]

        service = _service(mock_repos, mock_generator, mock_paths)
        briefing = service.generate_for_user("user-1")

        assert briefing is not None
        assert briefing.file_path is not None
        assert briefing.file_path.endswith(".md")
        mock_generator.generate.assert_called_once()
        mock_generator.write.assert_called_once()
        briefing_repo.save.assert_called_once_with(briefing)
        # File written under the path manager's resolved location.
        written_path = mock_generator.write.call_args.args[1]
        assert isinstance(written_path, Path)
