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

"""
Unit tests for briefing API endpoints.

Tests cover:
- GET /api/briefings - List briefings with pagination and filtering
- GET /api/briefings/{briefing_id} - Get single briefing
- GET /api/briefings/{briefing_id}/content - Get briefing markdown content
- GET /api/briefings/{briefing_id}/episodes - Get episodes in briefing
- POST /api/briefings - Create new briefing
- POST /api/briefings/preview - Preview briefing selection
- DELETE /api/briefings/{briefing_id} - Delete briefing
"""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from thestill.models.briefing import Briefing, BriefingStatus
from thestill.models.podcast import Episode, EpisodeState, Podcast
from thestill.models.user import User
from thestill.web.routes import api_briefings


@pytest.fixture
def mock_user():
    """Create a mock user."""
    return User(
        id="test-user-id",
        email="test@example.com",
        name="Test User",
    )


@pytest.fixture
def sample_briefing():
    """Create a sample briefing for testing."""
    now = datetime.now(timezone.utc)
    return Briefing(
        id="test-briefing-id",
        user_id="test-user-id",
        created_at=now - timedelta(hours=1),
        updated_at=now,
        period_start=now - timedelta(days=7),
        period_end=now,
        status=BriefingStatus.COMPLETED,
        file_path="briefing_20250126_120000.md",
        episode_ids=["ep-1", "ep-2", "ep-3"],
        episodes_total=3,
        episodes_completed=3,
        episodes_failed=0,
        processing_time_seconds=45.5,
    )


@pytest.fixture
def sample_podcast():
    """Create a sample podcast."""
    return Podcast(
        id="podcast-1",
        title="Test Podcast",
        description="A test podcast",
        rss_url="https://example.com/feed.xml",
        slug="test-podcast",
    )


@pytest.fixture
def sample_episode():
    """Create a sample episode."""
    return Episode(
        id="ep-1",
        title="Test Episode",
        description="A test episode",
        audio_url="https://example.com/episode.mp3",
        external_id="ep-1",
        slug="test-episode",
        state=EpisodeState.SUMMARIZED,
        pub_date=datetime.now(timezone.utc) - timedelta(days=1),
    )


@pytest.fixture
def mock_app_state(mock_user, sample_briefing, sample_podcast, sample_episode):
    """Create mock app state with all required dependencies."""
    state = MagicMock()

    # Mock briefing repository
    state.briefing_repository = MagicMock()
    state.briefing_repository.get_all.return_value = [sample_briefing]
    state.briefing_repository.get_by_id.return_value = sample_briefing
    state.briefing_repository.save.return_value = sample_briefing
    state.briefing_repository.delete.return_value = True
    state.briefing_repository.count.return_value = 1

    # Mock briefing service (used by GET /api/briefings/latest for inbox-driven
    # lazy generation). The default return mirrors "throttled, returns the
    # most-recent briefing" — tests that want the 404 path override this
    # to return None.
    state.briefing_service = MagicMock()
    state.briefing_service.generate_for_user.return_value = sample_briefing

    # Mock podcast repository
    state.repository = MagicMock()
    state.repository.get_episode.return_value = (sample_podcast, sample_episode)
    state.repository.get_all_episodes.return_value = (
        [(sample_podcast, sample_episode)],
        1,
    )

    # Mock path manager — real tempdirs so the narration variant
    # filesystem listing in GET /briefings/{id} can read files we write.
    state.path_manager = MagicMock()
    state.path_manager.briefings_dir.return_value = Path(tempfile.mkdtemp())
    narrations_root = Path(tempfile.mkdtemp())
    state.path_manager.narrations_dir.return_value = narrations_root

    # Default: narration runner disabled. Tests that exercise the
    # POST /narrate path opt in by setting state.narration_runner.
    state.narration_runner = None
    state.config = MagicMock()
    state.config.narration_default_duration_seconds = 300

    return state


@pytest.fixture
def test_app(mock_app_state, mock_user):
    """Create test FastAPI app with mocked dependencies."""
    app = FastAPI()
    app.include_router(api_briefings.router, prefix="/api/briefings")

    # Override dependencies
    def get_mock_state():
        return mock_app_state

    def get_mock_user():
        return mock_user

    app.dependency_overrides[api_briefings.get_app_state] = get_mock_state
    app.dependency_overrides[api_briefings.require_auth] = get_mock_user

    return app


@pytest.fixture
def client(test_app):
    """Create test client."""
    return TestClient(test_app)


class TestListBriefings:
    """Tests for GET /api/briefings endpoint."""

    def test_list_briefings_returns_paginated_response(self, client, sample_briefing):
        """List briefings returns paginated response with briefing data."""
        response = client.get("/api/briefings")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "briefings" in data
        assert "total" in data
        assert "offset" in data
        assert "limit" in data
        assert "has_more" in data

    def test_list_briefings_with_pagination_params(self, client, mock_app_state):
        """List briefings respects pagination parameters."""
        response = client.get("/api/briefings?limit=10&offset=5")

        assert response.status_code == 200
        # Verify repository was called with correct params
        mock_app_state.briefing_repository.get_all.assert_called()

    def test_list_briefings_with_status_filter(self, client, mock_app_state):
        """List briefings can filter by status."""
        response = client.get("/api/briefings?status=completed")

        assert response.status_code == 200
        mock_app_state.briefing_repository.get_all.assert_called()

    def test_list_briefings_invalid_status_returns_400(self, client):
        """Invalid status filter returns 400."""
        response = client.get("/api/briefings?status=invalid")

        assert response.status_code == 400
        assert "Invalid status" in response.json()["detail"]


class TestGetBriefing:
    """Tests for GET /api/briefings/{briefing_id} endpoint."""

    def test_get_briefing_returns_briefing(self, client, sample_briefing):
        """Get briefing by ID returns briefing data."""
        response = client.get("/api/briefings/test-briefing-id")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "briefing" in data
        assert data["briefing"]["id"] == sample_briefing.id

    def test_get_briefing_not_found_returns_404(self, client, mock_app_state):
        """Get non-existent briefing returns 404."""
        mock_app_state.briefing_repository.get_by_id.return_value = None

        response = client.get("/api/briefings/non-existent")

        assert response.status_code == 404

    def test_get_briefing_wrong_user_returns_404(self, client, mock_app_state, sample_briefing):
        """Get briefing owned by different user returns 404."""
        sample_briefing.user_id = "different-user"
        mock_app_state.briefing_repository.get_by_id.return_value = sample_briefing

        response = client.get("/api/briefings/test-briefing-id")

        assert response.status_code == 404

    def test_get_briefing_includes_narration_variants(self, client, mock_app_state, sample_briefing):
        """GET surfaces narration variants present on disk."""
        narrations_dir = mock_app_state.path_manager.narrations_dir.return_value
        for slug, target in [("short", 180), ("medium", 300), ("long", 600)]:
            json_path = narrations_dir / f"{sample_briefing.id}-{slug}.json"
            json_path.write_text(
                f'{{"target_duration_seconds": {target}, '
                f'"actual_duration_seconds": {target - 10}, '
                f'"mode": "narrated", '
                f'"generated_at": "2026-05-08T07:00:00+00:00"}}',
                encoding="utf-8",
            )
            (narrations_dir / f"{sample_briefing.id}-{slug}.md").write_text(
                "# briefing\n",
                encoding="utf-8",
            )

        response = client.get(f"/api/briefings/{sample_briefing.id}")
        assert response.status_code == 200
        data = response.json()
        narrations = data["narrations"]
        slugs = sorted(n["slug"] for n in narrations)
        assert slugs == ["long", "medium", "short"]
        # All variants share the briefing id as the prefix.
        assert all(n["narration_id"].startswith(f"{sample_briefing.id}-") for n in narrations)
        assert all(n["markdown_path"] for n in narrations)

    def test_get_briefing_with_no_narrations_returns_empty_list(self, client, sample_briefing):
        response = client.get(f"/api/briefings/{sample_briefing.id}")
        assert response.status_code == 200
        assert response.json()["narrations"] == []

    def test_get_briefing_skips_corrupt_narration_json(self, client, mock_app_state, sample_briefing):
        narrations_dir = mock_app_state.path_manager.narrations_dir.return_value
        (narrations_dir / f"{sample_briefing.id}-medium.json").write_text(
            "{not valid json",
            encoding="utf-8",
        )
        response = client.get(f"/api/briefings/{sample_briefing.id}")
        assert response.status_code == 200
        assert response.json()["narrations"] == []


class TestNarrateBriefing:
    """Tests for POST /api/briefings/{briefing_id}/narrate endpoint."""

    def _stub_run(self, briefing_id="test-briefing-id", slug="short", mode="narrated"):
        from thestill.services.narration.models import NarrationContent, NarrationStats
        from thestill.services.narration.narration_runner import NarrationRun

        stats = NarrationStats(
            target_duration_seconds=180,
            actual_duration_seconds=170.0,
            narration_words=350,
            quote_seconds=24.0,
            episodes_covered=2,
            episodes_in_tail=1,
            quote_count=2,
            fallback_reason=None,
        )
        content = NarrationContent(
            blocks=[],
            quotes=[],
            stats=stats,
            episode_ids_covered=["e1", "e2"],
            episode_ids_in_tail=["e3"],
            mode=mode,
            markdown="# briefing\n",
            generated_at=datetime(2026, 5, 8, 7, 0, tzinfo=timezone.utc),
            json_script_path=Path(f"/tmp/{briefing_id}-{slug}.json"),
            markdown_path=Path(f"/tmp/{briefing_id}-{slug}.md"),
        )
        return NarrationRun(briefing_id=briefing_id, slug=slug, content=content)

    def test_returns_503_when_runner_disabled(self, client, sample_briefing):
        # Default mock_app_state.narration_runner is None.
        response = client.post(f"/api/briefings/{sample_briefing.id}/narrate", json={})
        assert response.status_code == 503

    def test_resolves_preset_string_into_slug(self, client, mock_app_state, sample_briefing):
        mock_app_state.narration_runner = MagicMock()
        mock_app_state.narration_runner.run.return_value = self._stub_run(slug="short")
        response = client.post(
            f"/api/briefings/{sample_briefing.id}/narrate",
            json={"target_duration": "short"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["slug"] == "short"
        assert data["narration_id"].endswith("-short")
        kwargs = mock_app_state.narration_runner.run.call_args.kwargs
        assert kwargs["target_duration_seconds"] == 180
        assert kwargs["slug"] == "short"

    def test_int_seconds_default_to_custom_slug(self, client, mock_app_state, sample_briefing):
        mock_app_state.narration_runner = MagicMock()
        mock_app_state.narration_runner.run.return_value = self._stub_run(slug="custom-450s")
        response = client.post(
            f"/api/briefings/{sample_briefing.id}/narrate",
            json={"target_duration": 450},
        )
        assert response.status_code == 201
        kwargs = mock_app_state.narration_runner.run.call_args.kwargs
        assert kwargs["slug"] == "custom-450s"

    def test_explicit_slug_is_honoured(self, client, mock_app_state, sample_briefing):
        mock_app_state.narration_runner = MagicMock()
        mock_app_state.narration_runner.run.return_value = self._stub_run(slug="weekend")
        response = client.post(
            f"/api/briefings/{sample_briefing.id}/narrate",
            json={"target_duration": 300, "slug": "weekend"},
        )
        assert response.status_code == 201
        kwargs = mock_app_state.narration_runner.run.call_args.kwargs
        assert kwargs["slug"] == "weekend"

    def test_rejects_traversal_slug(self, client, mock_app_state, sample_briefing):
        mock_app_state.narration_runner = MagicMock()
        response = client.post(
            f"/api/briefings/{sample_briefing.id}/narrate",
            json={"slug": "../etc/passwd"},
        )
        assert response.status_code == 422

    def test_404_when_briefing_unknown(self, client, mock_app_state):
        mock_app_state.narration_runner = MagicMock()
        mock_app_state.briefing_repository.get_by_id.return_value = None
        response = client.post("/api/briefings/missing/narrate", json={})
        assert response.status_code == 404

    def test_404_when_runner_raises(self, client, mock_app_state, sample_briefing):
        from thestill.services.narration import NarrationRunnerError

        mock_app_state.narration_runner = MagicMock()
        mock_app_state.narration_runner.run.side_effect = NarrationRunnerError("no resolvable episodes")
        response = client.post(f"/api/briefings/{sample_briefing.id}/narrate", json={})
        assert response.status_code == 404

    def test_owner_check_returns_404_for_other_user(self, client, mock_app_state, sample_briefing):
        mock_app_state.narration_runner = MagicMock()
        sample_briefing.user_id = "different-user"
        mock_app_state.briefing_repository.get_by_id.return_value = sample_briefing
        response = client.post(f"/api/briefings/{sample_briefing.id}/narrate", json={})
        assert response.status_code == 404


class TestGetBriefingContent:
    """Tests for GET /api/briefings/{briefing_id}/content endpoint."""

    def test_get_content_returns_markdown(self, client, mock_app_state, sample_briefing):
        """Get briefing content returns markdown content."""
        # Create a test file
        content = "# Test Briefing\n\nThis is test content."
        briefing_file = mock_app_state.path_manager.briefings_dir() / sample_briefing.file_path
        briefing_file.write_text(content)

        response = client.get("/api/briefings/test-briefing-id/content")

        assert response.status_code == 200
        data = response.json()
        assert data["available"] is True
        assert data["content"] == content

    def test_get_content_unavailable(self, client, mock_app_state, sample_briefing):
        """Get content when file doesn't exist returns available=False."""
        sample_briefing.file_path = None
        mock_app_state.briefing_repository.get_by_id.return_value = sample_briefing

        response = client.get("/api/briefings/test-briefing-id/content")

        assert response.status_code == 200
        data = response.json()
        assert data["available"] is False
        assert data["content"] is None


class TestGetBriefingEpisodes:
    """Tests for GET /api/briefings/{briefing_id}/episodes endpoint."""

    def test_get_episodes_returns_list(self, client, sample_podcast, sample_episode):
        """Get briefing episodes returns episode list."""
        response = client.get("/api/briefings/test-briefing-id/episodes")

        assert response.status_code == 200
        data = response.json()
        assert "episodes" in data
        assert "count" in data


class TestCreateBriefing:
    """Tests for POST /api/briefings endpoint."""

    def test_create_briefing_ready_only_delegates_to_service(self, client, mock_app_state, sample_briefing):
        """ready_only=True goes through ``BriefingService.generate_from_criteria``."""
        mock_app_state.briefing_service.generate_from_criteria.return_value = sample_briefing

        response = client.post(
            "/api/briefings",
            json={"ready_only": True, "since_days": 7, "max_episodes": 10},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["briefing_id"] == sample_briefing.id
        assert data["episodes_selected"] == sample_briefing.episodes_total
        # The criteria passed to the service should mirror the request body.
        call = mock_app_state.briefing_service.generate_from_criteria.call_args
        assert call.args[0] == "test-user-id"
        criteria = call.args[1]
        assert criteria.since_days == 7
        assert criteria.max_episodes == 10
        assert criteria.ready_only is True

    def test_create_briefing_no_episodes(self, client, mock_app_state):
        """When the service returns None, ready_only POST yields ``no_episodes``."""
        mock_app_state.briefing_service.generate_from_criteria.return_value = None

        response = client.post(
            "/api/briefings",
            json={"ready_only": True},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "no_episodes"
        assert data["briefing_id"] is None

    def test_create_briefing_non_ready_only_marks_pending(self, client, mock_app_state):
        """ready_only=False still uses the selector inline and saves a PENDING row."""
        with patch("thestill.web.routes.api_briefings.BriefingEpisodeSelector") as mock_selector:
            mock_result = MagicMock()
            mock_result.episodes = [
                (MagicMock(id="podcast-1"), MagicMock(id="ep-1")),
            ]
            mock_selector.return_value.select.return_value = mock_result

            response = client.post(
                "/api/briefings",
                json={"ready_only": False, "since_days": 7, "max_episodes": 10},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "pending"
            # Service path must NOT be hit for the pipeline-processing flow.
            mock_app_state.briefing_service.generate_from_criteria.assert_not_called()


class TestCreateMorningBriefing:
    """Tests for POST /api/briefings/morning-briefing endpoint."""

    def test_morning_briefing_delegates_to_service(self, client, mock_app_state, sample_briefing):
        """POST /morning-briefing routes through BriefingService with config defaults."""
        mock_app_state.config.briefing_default_since_days = 3
        mock_app_state.config.briefing_default_max_episodes = 5
        mock_app_state.briefing_service.generate_from_criteria.return_value = sample_briefing

        response = client.post("/api/briefings/morning-briefing")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["briefing_id"] == sample_briefing.id

        criteria = mock_app_state.briefing_service.generate_from_criteria.call_args.args[1]
        assert criteria.since_days == 3
        assert criteria.max_episodes == 5
        assert criteria.ready_only is True
        assert criteria.exclude_briefed is True

    def test_morning_briefing_no_episodes(self, client, mock_app_state):
        """Service returning None surfaces as ``no_episodes``."""
        mock_app_state.config.briefing_default_since_days = 3
        mock_app_state.config.briefing_default_max_episodes = 5
        mock_app_state.briefing_service.generate_from_criteria.return_value = None

        response = client.post("/api/briefings/morning-briefing")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "no_episodes"
        assert data["briefing_id"] is None


class TestPreviewBriefing:
    """Tests for POST /api/briefings/preview endpoint."""

    def test_preview_returns_episode_list(self, client, mock_app_state, sample_podcast, sample_episode):
        """Preview briefing returns list of episodes that would be included."""
        with patch("thestill.web.routes.api_briefings.BriefingEpisodeSelector") as mock_selector:
            mock_result = MagicMock()
            mock_result.episodes = [(sample_podcast, sample_episode)]
            mock_result.total_matching = 1
            mock_selector.return_value.preview.return_value = mock_result

            response = client.post(
                "/api/briefings/preview",
                json={"since_days": 7, "max_episodes": 10},
            )

            assert response.status_code == 200
            data = response.json()
            assert "episodes" in data
            assert "total_matching" in data
            assert "criteria" in data


class TestDeleteBriefing:
    """Tests for DELETE /api/briefings/{briefing_id} endpoint."""

    def test_delete_briefing_success(self, client, mock_app_state, sample_briefing):
        """Delete briefing removes record and file."""
        # Create a test file
        briefing_file = mock_app_state.path_manager.briefings_dir() / sample_briefing.file_path
        briefing_file.write_text("test content")

        response = client.delete("/api/briefings/test-briefing-id")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"
        mock_app_state.briefing_repository.delete.assert_called_once_with("test-briefing-id")

    def test_delete_briefing_not_found_returns_404(self, client, mock_app_state):
        """Delete non-existent briefing returns 404."""
        mock_app_state.briefing_repository.get_by_id.return_value = None

        response = client.delete("/api/briefings/non-existent")

        assert response.status_code == 404


class TestLatestBriefing:
    """Tests for GET /api/briefings/latest endpoint."""

    def test_get_latest_briefing_success(self, client, mock_app_state, sample_briefing):
        """Get latest briefing returns most recent briefing."""
        response = client.get("/api/briefings/latest")

        assert response.status_code == 200
        data = response.json()
        assert "briefing" in data

    def test_get_latest_briefing_none_returns_404(self, client, mock_app_state):
        """Get latest when no eligible inbox items returns 404."""
        mock_app_state.briefing_service.generate_for_user.return_value = None

        response = client.get("/api/briefings/latest")

        assert response.status_code == 404
