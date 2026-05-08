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

"""Unit tests for the per-user briefing API endpoints (spec #36)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from thestill.models.briefing import Briefing
from thestill.models.user import User
from thestill.services.briefing_service import BriefingNotFoundError
from thestill.web.routes import api_briefings


@pytest.fixture
def mock_user():
    return User(id="user-1", email="alice@example.com", name="Alice")


def _briefing(
    *,
    briefing_id: str = "00000000-0000-0000-0000-000000000001",
    user_id: str = "user-1",
    script_path: str | None = None,
    listened_at: datetime | None = None,
) -> Briefing:
    base = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    return Briefing(
        id=briefing_id,
        user_id=user_id,
        cursor_from=base,
        cursor_to=base + timedelta(hours=1),
        episode_count=3,
        script_path=script_path,
        created_at=base + timedelta(hours=1),
        listened_at=listened_at,
    )


@pytest.fixture
def mock_app_state():
    state = MagicMock()
    state.briefing_service = MagicMock()
    state.briefing_repository = MagicMock()
    return state


@pytest.fixture
def test_app(mock_app_state, mock_user):
    app = FastAPI()
    app.include_router(api_briefings.router, prefix="/api/briefings")
    app.dependency_overrides[api_briefings.get_app_state] = lambda: mock_app_state
    app.dependency_overrides[api_briefings.require_auth] = lambda: mock_user
    return app


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


# ============================================================================
# GET /api/briefings/latest
# ============================================================================


class TestGetLatest:
    def test_returns_briefing_when_service_emits_one(self, client, mock_app_state):
        mock_app_state.briefing_service.generate_for_user.return_value = _briefing()

        response = client.get("/api/briefings/latest")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["id"] == "00000000-0000-0000-0000-000000000001"
        assert data["user_id"] == "user-1"
        assert data["episode_count"] == 3

    def test_404_when_service_returns_none(self, client, mock_app_state):
        mock_app_state.briefing_service.generate_for_user.return_value = None

        response = client.get("/api/briefings/latest")

        assert response.status_code == 404

    def test_passes_current_user_to_service(self, client, mock_app_state, mock_user):
        mock_app_state.briefing_service.generate_for_user.return_value = _briefing()

        client.get("/api/briefings/latest")

        mock_app_state.briefing_service.generate_for_user.assert_called_once_with(mock_user.id)


# ============================================================================
# GET /api/briefings/{briefing_id}
# ============================================================================


class TestGetBriefing:
    def test_returns_briefing_for_owner(self, client, mock_app_state):
        mock_app_state.briefing_repository.get.return_value = _briefing()

        response = client.get("/api/briefings/00000000-0000-0000-0000-000000000001")

        assert response.status_code == 200
        assert response.json()["id"] == "00000000-0000-0000-0000-000000000001"

    def test_404_when_briefing_missing(self, client, mock_app_state):
        mock_app_state.briefing_repository.get.return_value = None

        response = client.get("/api/briefings/missing-id")

        assert response.status_code == 404

    def test_404_for_other_users_briefing(self, client, mock_app_state):
        """Cross-user access returns 404 (not 403) to avoid id enumeration."""
        mock_app_state.briefing_repository.get.return_value = _briefing(user_id="other-user")

        response = client.get("/api/briefings/00000000-0000-0000-0000-000000000001")

        assert response.status_code == 404


# ============================================================================
# GET /api/briefings/{briefing_id}/script
# ============================================================================


class TestGetScript:
    def test_returns_markdown_body(self, client, mock_app_state, tmp_path):
        script_file = tmp_path / "script.md"
        script_file.write_text("# Today's briefing\n\nContent.\n", encoding="utf-8")
        mock_app_state.briefing_repository.get.return_value = _briefing(script_path=str(script_file))

        response = client.get("/api/briefings/00000000-0000-0000-0000-000000000001/script")

        assert response.status_code == 200
        assert response.json()["markdown"] == "# Today's briefing\n\nContent.\n"

    def test_404_when_script_path_null(self, client, mock_app_state):
        mock_app_state.briefing_repository.get.return_value = _briefing(script_path=None)

        response = client.get("/api/briefings/00000000-0000-0000-0000-000000000001/script")

        assert response.status_code == 404

    def test_404_when_file_missing_on_disk(self, client, mock_app_state, tmp_path):
        nonexistent = tmp_path / "missing.md"
        mock_app_state.briefing_repository.get.return_value = _briefing(script_path=str(nonexistent))

        response = client.get("/api/briefings/00000000-0000-0000-0000-000000000001/script")

        assert response.status_code == 404

    def test_404_for_other_users_script(self, client, mock_app_state, tmp_path):
        script_file = tmp_path / "script.md"
        script_file.write_text("hi", encoding="utf-8")
        mock_app_state.briefing_repository.get.return_value = _briefing(
            script_path=str(script_file),
            user_id="other-user",
        )

        response = client.get("/api/briefings/00000000-0000-0000-0000-000000000001/script")

        assert response.status_code == 404


# ============================================================================
# POST /api/briefings/{briefing_id}/listened
# ============================================================================


class TestMarkListened:
    def test_marks_listened_for_owner(self, client, mock_app_state):
        listened_at = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
        mock_app_state.briefing_repository.get.return_value = _briefing()
        mock_app_state.briefing_service.mark_listened.return_value = _briefing(listened_at=listened_at)

        response = client.post("/api/briefings/00000000-0000-0000-0000-000000000001/listened")

        assert response.status_code == 200
        assert response.json()["listened_at"] is not None

    def test_404_when_briefing_missing(self, client, mock_app_state):
        mock_app_state.briefing_repository.get.return_value = None

        response = client.post("/api/briefings/missing-id/listened")

        assert response.status_code == 404

    def test_404_for_other_users_briefing(self, client, mock_app_state):
        mock_app_state.briefing_repository.get.return_value = _briefing(user_id="other-user")

        response = client.post("/api/briefings/00000000-0000-0000-0000-000000000001/listened")

        assert response.status_code == 404
        # Service is never invoked for cross-user attempts.
        mock_app_state.briefing_service.mark_listened.assert_not_called()

    def test_404_when_service_raises_not_found(self, client, mock_app_state):
        """Race between ownership check and update is surfaced as 404."""
        mock_app_state.briefing_repository.get.return_value = _briefing()
        mock_app_state.briefing_service.mark_listened.side_effect = BriefingNotFoundError("gone")

        response = client.post("/api/briefings/00000000-0000-0000-0000-000000000001/listened")

        assert response.status_code == 404
