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
    state.digest_repository.count.return_value = 1

    # Mock digest service (used by GET /api/digests/latest for inbox-driven
    # lazy generation). The default return mirrors "throttled, returns the
    # most-recent digest" — tests that want the 404 path override this
    # to return None.
    state.digest_service = MagicMock()
    state.digest_service.generate_for_user.return_value = sample_digest

    # Mock podcast repository
    state.repository = MagicMock()
    state.repository.get_episode.return_value = (sample_podcast, sample_episode)
    state.repository.get_all_episodes.return_value = (
        [(sample_podcast, sample_episode)],
        1,
    )

    # Mock path manager — real tempdirs so the narration variant
    # filesystem listing in GET /digests/{id} can read files we write.
    state.path_manager = MagicMock()
    state.path_manager.digests_dir.return_value = Path(tempfile.mkdtemp())
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

    def test_get_digest_includes_narration_variants(self, client, mock_app_state, sample_digest):
        """GET surfaces narration variants present on disk."""
        narrations_dir = mock_app_state.path_manager.narrations_dir.return_value
        for slug, target in [("short", 180), ("medium", 300), ("long", 600)]:
            json_path = narrations_dir / f"{sample_digest.id}-{slug}.json"
            json_path.write_text(
                f'{{"target_duration_seconds": {target}, '
                f'"actual_duration_seconds": {target - 10}, '
                f'"mode": "narrated", '
                f'"generated_at": "2026-05-08T07:00:00+00:00"}}',
                encoding="utf-8",
            )
            (narrations_dir / f"{sample_digest.id}-{slug}.md").write_text(
                "# briefing\n",
                encoding="utf-8",
            )

        response = client.get(f"/api/digests/{sample_digest.id}")
        assert response.status_code == 200
        data = response.json()
        narrations = data["narrations"]
        slugs = sorted(n["slug"] for n in narrations)
        assert slugs == ["long", "medium", "short"]
        # All variants share the digest id as the prefix.
        assert all(n["narration_id"].startswith(f"{sample_digest.id}-") for n in narrations)
        assert all(n["markdown_path"] for n in narrations)

    def test_get_digest_with_no_narrations_returns_empty_list(self, client, sample_digest):
        response = client.get(f"/api/digests/{sample_digest.id}")
        assert response.status_code == 200
        assert response.json()["narrations"] == []

    def test_get_digest_skips_corrupt_narration_json(self, client, mock_app_state, sample_digest):
        narrations_dir = mock_app_state.path_manager.narrations_dir.return_value
        (narrations_dir / f"{sample_digest.id}-medium.json").write_text(
            "{not valid json",
            encoding="utf-8",
        )
        response = client.get(f"/api/digests/{sample_digest.id}")
        assert response.status_code == 200
        assert response.json()["narrations"] == []


class TestNarrateDigest:
    """Tests for POST /api/digests/{digest_id}/narrate endpoint."""

    def _stub_run(self, digest_id="test-digest-id", slug="short", mode="narrated"):
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
            json_script_path=Path(f"/tmp/{digest_id}-{slug}.json"),
            markdown_path=Path(f"/tmp/{digest_id}-{slug}.md"),
        )
        return NarrationRun(digest_id=digest_id, slug=slug, content=content)

    def test_returns_503_when_runner_disabled(self, client, sample_digest):
        # Default mock_app_state.narration_runner is None.
        response = client.post(f"/api/digests/{sample_digest.id}/narrate", json={})
        assert response.status_code == 503

    def test_resolves_preset_string_into_slug(self, client, mock_app_state, sample_digest):
        mock_app_state.narration_runner = MagicMock()
        mock_app_state.narration_runner.run.return_value = self._stub_run(slug="short")
        response = client.post(
            f"/api/digests/{sample_digest.id}/narrate",
            json={"target_duration": "short"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["slug"] == "short"
        assert data["narration_id"].endswith("-short")
        kwargs = mock_app_state.narration_runner.run.call_args.kwargs
        assert kwargs["target_duration_seconds"] == 180
        assert kwargs["slug"] == "short"

    def test_int_seconds_default_to_custom_slug(self, client, mock_app_state, sample_digest):
        mock_app_state.narration_runner = MagicMock()
        mock_app_state.narration_runner.run.return_value = self._stub_run(slug="custom-450s")
        response = client.post(
            f"/api/digests/{sample_digest.id}/narrate",
            json={"target_duration": 450},
        )
        assert response.status_code == 201
        kwargs = mock_app_state.narration_runner.run.call_args.kwargs
        assert kwargs["slug"] == "custom-450s"

    def test_explicit_slug_is_honoured(self, client, mock_app_state, sample_digest):
        mock_app_state.narration_runner = MagicMock()
        mock_app_state.narration_runner.run.return_value = self._stub_run(slug="weekend")
        response = client.post(
            f"/api/digests/{sample_digest.id}/narrate",
            json={"target_duration": 300, "slug": "weekend"},
        )
        assert response.status_code == 201
        kwargs = mock_app_state.narration_runner.run.call_args.kwargs
        assert kwargs["slug"] == "weekend"

    def test_rejects_traversal_slug(self, client, mock_app_state, sample_digest):
        mock_app_state.narration_runner = MagicMock()
        response = client.post(
            f"/api/digests/{sample_digest.id}/narrate",
            json={"slug": "../etc/passwd"},
        )
        assert response.status_code == 422

    def test_404_when_digest_unknown(self, client, mock_app_state):
        mock_app_state.narration_runner = MagicMock()
        mock_app_state.digest_repository.get_by_id.return_value = None
        response = client.post("/api/digests/missing/narrate", json={})
        assert response.status_code == 404

    def test_404_when_runner_raises(self, client, mock_app_state, sample_digest):
        from thestill.services.narration import NarrationRunnerError

        mock_app_state.narration_runner = MagicMock()
        mock_app_state.narration_runner.run.side_effect = NarrationRunnerError("no resolvable episodes")
        response = client.post(f"/api/digests/{sample_digest.id}/narrate", json={})
        assert response.status_code == 404

    def test_owner_check_returns_404_for_other_user(self, client, mock_app_state, sample_digest):
        mock_app_state.narration_runner = MagicMock()
        sample_digest.user_id = "different-user"
        mock_app_state.digest_repository.get_by_id.return_value = sample_digest
        response = client.post(f"/api/digests/{sample_digest.id}/narrate", json={})
        assert response.status_code == 404


class TestGetDigestContent:
    """Tests for GET /api/digests/{digest_id}/content endpoint."""

    def test_get_content_returns_markdown(self, client, mock_app_state, sample_digest):
        """Get digest content returns markdown content."""
        # Create a test file
        content = "# Test Digest\n\nThis is test content."
        digest_file = mock_app_state.path_manager.digests_dir() / sample_digest.file_path
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
        digest_file = mock_app_state.path_manager.digests_dir() / sample_digest.file_path
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
        """Get latest when no eligible inbox items returns 404."""
        mock_app_state.digest_service.generate_for_user.return_value = None

        response = client.get("/api/digests/latest")

        assert response.status_code == 404
