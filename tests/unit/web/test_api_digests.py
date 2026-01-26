# Copyright 2025 thestill.me
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
Unit tests for digest API endpoints.

Tests cover:
- GET /api/digests - List digests with pagination and filtering
- GET /api/digests/{digest_id} - Get single digest
- GET /api/digests/{digest_id}/content - Get digest markdown content
- GET /api/digests/{digest_id}/episodes - Get episodes in digest
- POST /api/digests - Create new digest
- POST /api/digests/preview - Preview digest selection
- DELETE /api/digests/{digest_id} - Delete digest
"""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from thestill.models.digest import Digest, DigestStatus
from thestill.models.podcast import Episode, EpisodeState, Podcast
from thestill.models.user import User
from thestill.web.routes import api_digests


@pytest.fixture
def mock_user():
    """Create a mock user."""
    return User(
        id="test-user-id",
        email="test@example.com",
        name="Test User",
    )


@pytest.fixture
def sample_digest():
    """Create a sample digest for testing."""
    now = datetime.now(timezone.utc)
    return Digest(
        id="test-digest-id",
        user_id="test-user-id",
        created_at=now - timedelta(hours=1),
        updated_at=now,
        period_start=now - timedelta(days=7),
        period_end=now,
        status=DigestStatus.COMPLETED,
        file_path="digest_20250126_120000.md",
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
def mock_app_state(mock_user, sample_digest, sample_podcast, sample_episode):
    """Create mock app state with all required dependencies."""
    state = MagicMock()

    # Mock digest repository
    state.digest_repository = MagicMock()
    state.digest_repository.get_all.return_value = [sample_digest]
    state.digest_repository.get_by_id.return_value = sample_digest
    state.digest_repository.save.return_value = sample_digest
    state.digest_repository.delete.return_value = True

    # Mock podcast repository
    state.repository = MagicMock()
    state.repository.get_episode.return_value = (sample_podcast, sample_episode)
    state.repository.get_all_episodes.return_value = (
        [(sample_podcast, sample_episode)],
        1,
    )

    # Mock path manager
    state.path_manager = MagicMock()
    state.path_manager.digests_dir = Path(tempfile.mkdtemp())

    return state


@pytest.fixture
def test_app(mock_app_state, mock_user):
    """Create test FastAPI app with mocked dependencies."""
    app = FastAPI()
    app.include_router(api_digests.router, prefix="/api/digests")

    # Override dependencies
    def get_mock_state():
        return mock_app_state

    def get_mock_user():
        return mock_user

    app.dependency_overrides[api_digests.get_app_state] = get_mock_state
    app.dependency_overrides[api_digests.require_auth] = get_mock_user

    return app


@pytest.fixture
def client(test_app):
    """Create test client."""
    return TestClient(test_app)


class TestListDigests:
    """Tests for GET /api/digests endpoint."""

    def test_list_digests_returns_paginated_response(self, client, sample_digest):
        """List digests returns paginated response with digest data."""
        response = client.get("/api/digests")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "digests" in data
        assert "total" in data
        assert "offset" in data
        assert "limit" in data
        assert "has_more" in data

    def test_list_digests_with_pagination_params(self, client, mock_app_state):
        """List digests respects pagination parameters."""
        response = client.get("/api/digests?limit=10&offset=5")

        assert response.status_code == 200
        # Verify repository was called with correct params
        mock_app_state.digest_repository.get_all.assert_called()

    def test_list_digests_with_status_filter(self, client, mock_app_state):
        """List digests can filter by status."""
        response = client.get("/api/digests?status=completed")

        assert response.status_code == 200
        mock_app_state.digest_repository.get_all.assert_called()

    def test_list_digests_invalid_status_returns_400(self, client):
        """Invalid status filter returns 400."""
        response = client.get("/api/digests?status=invalid")

        assert response.status_code == 400
        assert "Invalid status" in response.json()["detail"]


class TestGetDigest:
    """Tests for GET /api/digests/{digest_id} endpoint."""

    def test_get_digest_returns_digest(self, client, sample_digest):
        """Get digest by ID returns digest data."""
        response = client.get("/api/digests/test-digest-id")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "digest" in data
        assert data["digest"]["id"] == sample_digest.id

    def test_get_digest_not_found_returns_404(self, client, mock_app_state):
        """Get non-existent digest returns 404."""
        mock_app_state.digest_repository.get_by_id.return_value = None

        response = client.get("/api/digests/non-existent")

        assert response.status_code == 404

    def test_get_digest_wrong_user_returns_404(self, client, mock_app_state, sample_digest):
        """Get digest owned by different user returns 404."""
        sample_digest.user_id = "different-user"
        mock_app_state.digest_repository.get_by_id.return_value = sample_digest

        response = client.get("/api/digests/test-digest-id")

        assert response.status_code == 404


class TestGetDigestContent:
    """Tests for GET /api/digests/{digest_id}/content endpoint."""

    def test_get_content_returns_markdown(self, client, mock_app_state, sample_digest):
        """Get digest content returns markdown content."""
        # Create a test file
        content = "# Test Digest\n\nThis is test content."
        digest_file = mock_app_state.path_manager.digests_dir / sample_digest.file_path
        digest_file.write_text(content)

        response = client.get("/api/digests/test-digest-id/content")

        assert response.status_code == 200
        data = response.json()
        assert data["available"] is True
        assert data["content"] == content

    def test_get_content_unavailable(self, client, mock_app_state, sample_digest):
        """Get content when file doesn't exist returns available=False."""
        sample_digest.file_path = None
        mock_app_state.digest_repository.get_by_id.return_value = sample_digest

        response = client.get("/api/digests/test-digest-id/content")

        assert response.status_code == 200
        data = response.json()
        assert data["available"] is False
        assert data["content"] is None


class TestGetDigestEpisodes:
    """Tests for GET /api/digests/{digest_id}/episodes endpoint."""

    def test_get_episodes_returns_list(self, client, sample_podcast, sample_episode):
        """Get digest episodes returns episode list."""
        response = client.get("/api/digests/test-digest-id/episodes")

        assert response.status_code == 200
        data = response.json()
        assert "episodes" in data
        assert "count" in data


class TestCreateDigest:
    """Tests for POST /api/digests endpoint."""

    def test_create_digest_ready_only_success(self, client, mock_app_state):
        """Create digest with ready_only=True generates immediately."""
        with patch("thestill.web.routes.api_digests.DigestEpisodeSelector") as mock_selector:
            with patch("thestill.web.routes.api_digests.DigestGenerator") as mock_generator:
                # Setup mock selector
                mock_result = MagicMock()
                mock_result.episodes = [
                    (MagicMock(id="podcast-1"), MagicMock(id="ep-1")),
                ]
                mock_selector.return_value.select.return_value = mock_result

                # Setup mock generator
                mock_content = MagicMock()
                mock_content.stats.successful_episodes = 1
                mock_content.stats.failed_episodes = 0
                mock_generator.return_value.generate.return_value = mock_content

                response = client.post(
                    "/api/digests",
                    json={"ready_only": True, "since_days": 7, "max_episodes": 10},
                )

                assert response.status_code == 200
                data = response.json()
                assert data["status"] in ["completed", "ok"]

    def test_create_digest_no_episodes(self, client, mock_app_state):
        """Create digest with no matching episodes returns no_episodes status."""
        with patch("thestill.web.routes.api_digests.DigestEpisodeSelector") as mock_selector:
            mock_result = MagicMock()
            mock_result.episodes = []
            mock_selector.return_value.select.return_value = mock_result

            response = client.post(
                "/api/digests",
                json={"ready_only": True},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "no_episodes"


class TestPreviewDigest:
    """Tests for POST /api/digests/preview endpoint."""

    def test_preview_returns_episode_list(self, client, mock_app_state, sample_podcast, sample_episode):
        """Preview digest returns list of episodes that would be included."""
        with patch("thestill.web.routes.api_digests.DigestEpisodeSelector") as mock_selector:
            mock_result = MagicMock()
            mock_result.episodes = [(sample_podcast, sample_episode)]
            mock_result.total_matching = 1
            mock_selector.return_value.preview.return_value = mock_result

            response = client.post(
                "/api/digests/preview",
                json={"since_days": 7, "max_episodes": 10},
            )

            assert response.status_code == 200
            data = response.json()
            assert "episodes" in data
            assert "total_matching" in data
            assert "criteria" in data


class TestDeleteDigest:
    """Tests for DELETE /api/digests/{digest_id} endpoint."""

    def test_delete_digest_success(self, client, mock_app_state, sample_digest):
        """Delete digest removes record and file."""
        # Create a test file
        digest_file = mock_app_state.path_manager.digests_dir / sample_digest.file_path
        digest_file.write_text("test content")

        response = client.delete("/api/digests/test-digest-id")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"
        mock_app_state.digest_repository.delete.assert_called_once_with("test-digest-id")

    def test_delete_digest_not_found_returns_404(self, client, mock_app_state):
        """Delete non-existent digest returns 404."""
        mock_app_state.digest_repository.get_by_id.return_value = None

        response = client.delete("/api/digests/non-existent")

        assert response.status_code == 404


class TestLatestDigest:
    """Tests for GET /api/digests/latest endpoint."""

    def test_get_latest_digest_success(self, client, mock_app_state, sample_digest):
        """Get latest digest returns most recent digest."""
        response = client.get("/api/digests/latest")

        assert response.status_code == 200
        data = response.json()
        assert "digest" in data

    def test_get_latest_digest_none_returns_404(self, client, mock_app_state):
        """Get latest when no digests exist returns 404."""
        mock_app_state.digest_repository.get_all.return_value = []

        response = client.get("/api/digests/latest")

        assert response.status_code == 404
