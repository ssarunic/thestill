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


# ============================================================================
# GET /api/briefings (paginated history)
# ============================================================================


class TestListBriefings:
    def test_lists_history_newest_first(self, client, mock_app_state):
        rows = [
            _briefing(briefing_id="00000000-0000-0000-0000-000000000002"),
            _briefing(briefing_id="00000000-0000-0000-0000-000000000001"),
        ]
        mock_app_state.briefing_repository.list_for_user.return_value = rows
        mock_app_state.briefing_repository.count_for_user.return_value = 5

        response = client.get("/api/briefings?limit=2&offset=0")

        assert response.status_code == 200
        body = response.json()
        assert [b["id"] for b in body["briefings"]] == [
            "00000000-0000-0000-0000-000000000002",
            "00000000-0000-0000-0000-000000000001",
        ]
        assert body["total"] == 5
        assert body["has_more"] is True
        assert body["next_offset"] == 2
        kwargs = mock_app_state.briefing_repository.list_for_user.call_args
        assert kwargs.args[0] == "user-1"
        assert kwargs.kwargs == {"limit": 2, "offset": 0}

    def test_clamps_limit_and_offset(self, client, mock_app_state):
        mock_app_state.briefing_repository.list_for_user.return_value = []
        mock_app_state.briefing_repository.count_for_user.return_value = 0

        response = client.get("/api/briefings?limit=9999&offset=-5")

        assert response.status_code == 200
        kwargs = mock_app_state.briefing_repository.list_for_user.call_args.kwargs
        assert kwargs == {"limit": 100, "offset": 0}

    def test_empty_history(self, client, mock_app_state):
        mock_app_state.briefing_repository.list_for_user.return_value = []
        mock_app_state.briefing_repository.count_for_user.return_value = 0

        response = client.get("/api/briefings")

        body = response.json()
        assert body["briefings"] == []
        assert body["has_more"] is False
        assert body["next_offset"] is None


# ============================================================================
# POST /api/briefings/{briefing_id}/narrate (spec #33, rekeyed on digest
# retirement)
# ============================================================================


class TestNarrateBriefing:
    def _run(self, briefing_id="00000000-0000-0000-0000-000000000001", slug="medium"):
        run = MagicMock()
        run.briefing_id = briefing_id
        run.narration_id = f"{briefing_id}-{slug}"
        run.content.mode = "narrated"
        run.content.stats.target_duration_seconds = 300
        run.content.stats.actual_duration_seconds = 290.0
        run.content.stats.quote_count = 3
        run.content.stats.fallback_reason = None
        run.json_path = None
        run.markdown_path = None
        return run

    def test_narrates_owned_briefing(self, client, mock_app_state):
        mock_app_state.briefing_repository.get.return_value = _briefing()
        mock_app_state.config.narration_default_duration_seconds = 300
        mock_app_state.narration_runner.run.return_value = self._run()

        response = client.post(
            "/api/briefings/00000000-0000-0000-0000-000000000001/narrate",
            json={"target_duration": "medium"},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["briefing_id"] == "00000000-0000-0000-0000-000000000001"
        assert body["narration_id"].endswith("-medium")
        kwargs = mock_app_state.narration_runner.run.call_args.kwargs
        assert kwargs["briefing_id"] == "00000000-0000-0000-0000-000000000001"

    def test_503_when_narration_disabled(self, client, mock_app_state):
        mock_app_state.briefing_repository.get.return_value = _briefing()
        mock_app_state.narration_runner = None

        response = client.post(
            "/api/briefings/00000000-0000-0000-0000-000000000001/narrate",
            json={},
        )

        assert response.status_code == 503

    def test_404_for_other_users_briefing(self, client, mock_app_state):
        mock_app_state.briefing_repository.get.return_value = _briefing(user_id="other-user")

        response = client.post(
            "/api/briefings/00000000-0000-0000-0000-000000000001/narrate",
            json={},
        )

        assert response.status_code == 404
        mock_app_state.narration_runner.run.assert_not_called()


# ============================================================================
# GET/PUT /api/briefings/schedule (spec #50)
# ============================================================================


def _schedule(**overrides):
    from thestill.models.briefing_schedule import BriefingSchedule

    defaults = dict(
        user_id="user-1",
        frequency="daily",
        hour_local=8,
        weekday=None,
        timezone_name="Europe/Zagreb",
        enabled=True,
        next_run_at=datetime(2026, 7, 8, 6, 0, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return BriefingSchedule(**defaults)


class TestGetSchedule:
    def test_returns_schedule(self, client, mock_app_state):
        mock_app_state.briefing_schedule_repository.get.return_value = _schedule()

        response = client.get("/api/briefings/schedule")

        assert response.status_code == 200
        body = response.json()
        assert body["frequency"] == "daily"
        assert body["hour_local"] == 8
        assert body["timezone"] == "Europe/Zagreb"
        assert body["next_run_at"] == "2026-07-08T06:00:00+00:00"

    def test_404_when_never_configured(self, client, mock_app_state):
        mock_app_state.briefing_schedule_repository.get.return_value = None

        response = client.get("/api/briefings/schedule")

        assert response.status_code == 404

    def test_schedule_path_not_swallowed_by_briefing_id_route(self, client, mock_app_state):
        """Literal /schedule must win over /{briefing_id}."""
        mock_app_state.briefing_schedule_repository.get.return_value = _schedule()

        client.get("/api/briefings/schedule")

        mock_app_state.briefing_repository.get.assert_not_called()


class TestPutSchedule:
    def test_upserts_and_echoes_next_run(self, client, mock_app_state):
        mock_app_state.briefing_schedule_repository.get.return_value = None

        response = client.put(
            "/api/briefings/schedule",
            json={"frequency": "daily", "hour_local": 8, "timezone": "Europe/Zagreb", "enabled": True},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["enabled"] is True
        assert body["next_run_at"] is not None
        saved = mock_app_state.briefing_schedule_repository.upsert.call_args.args[0]
        assert saved.user_id == "user-1"
        assert saved.next_run_at is not None

    def test_weekly_requires_weekday(self, client, mock_app_state):
        response = client.put(
            "/api/briefings/schedule",
            json={"frequency": "weekly", "hour_local": 8, "timezone": "Europe/Zagreb", "enabled": True},
        )

        assert response.status_code == 422
        mock_app_state.briefing_schedule_repository.upsert.assert_not_called()

    def test_invalid_timezone_rejected(self, client, mock_app_state):
        response = client.put(
            "/api/briefings/schedule",
            json={"frequency": "daily", "hour_local": 8, "timezone": "Mars/Olympus_Mons", "enabled": True},
        )

        assert response.status_code == 422
        mock_app_state.briefing_schedule_repository.upsert.assert_not_called()

    def test_hour_out_of_range_rejected(self, client, mock_app_state):
        response = client.put(
            "/api/briefings/schedule",
            json={"frequency": "daily", "hour_local": 24, "timezone": "Europe/Zagreb", "enabled": True},
        )

        assert response.status_code == 422

    def test_disable_parks_next_run(self, client, mock_app_state):
        mock_app_state.briefing_schedule_repository.get.return_value = _schedule()

        response = client.put(
            "/api/briefings/schedule",
            json={"frequency": "daily", "hour_local": 8, "timezone": "Europe/Zagreb", "enabled": False},
        )

        assert response.status_code == 200
        assert response.json()["next_run_at"] is None
        saved = mock_app_state.briefing_schedule_repository.upsert.call_args.args[0]
        assert saved.next_run_at is None

    def test_update_preserves_created_at(self, client, mock_app_state):
        existing = _schedule()
        mock_app_state.briefing_schedule_repository.get.return_value = existing

        client.put(
            "/api/briefings/schedule",
            json={"frequency": "weekly", "hour_local": 7, "weekday": 0, "timezone": "Europe/Zagreb", "enabled": True},
        )

        saved = mock_app_state.briefing_schedule_repository.upsert.call_args.args[0]
        assert saved.created_at == existing.created_at


class TestScheduleEmailDelivery:
    """Spec #51: the ``email_enabled`` flag on GET/PUT /schedule."""

    def test_get_includes_email_enabled(self, client, mock_app_state):
        mock_app_state.briefing_schedule_repository.get.return_value = _schedule(email_enabled=True)

        response = client.get("/api/briefings/schedule")

        assert response.status_code == 200
        assert response.json()["email_enabled"] is True

    def test_put_round_trips_email_enabled(self, client, mock_app_state):
        # mock_app_state's briefing_delivery_service is a MagicMock — i.e.
        # a provider is configured — so opting in is allowed.
        mock_app_state.briefing_schedule_repository.get.return_value = None

        response = client.put(
            "/api/briefings/schedule",
            json={
                "frequency": "daily",
                "hour_local": 8,
                "timezone": "Europe/Zagreb",
                "enabled": True,
                "email_enabled": True,
            },
        )

        assert response.status_code == 200
        assert response.json()["email_enabled"] is True
        saved = mock_app_state.briefing_schedule_repository.upsert.call_args.args[0]
        assert saved.email_enabled is True

    def test_email_enabled_defaults_false(self, client, mock_app_state):
        mock_app_state.briefing_schedule_repository.get.return_value = None

        response = client.put(
            "/api/briefings/schedule",
            json={"frequency": "daily", "hour_local": 8, "timezone": "Europe/Zagreb", "enabled": True},
        )

        assert response.status_code == 200
        assert response.json()["email_enabled"] is False

    def test_opt_in_rejected_when_provider_none(self, client, mock_app_state):
        mock_app_state.briefing_delivery_service = None

        response = client.put(
            "/api/briefings/schedule",
            json={
                "frequency": "daily",
                "hour_local": 8,
                "timezone": "Europe/Zagreb",
                "enabled": True,
                "email_enabled": True,
            },
        )

        assert response.status_code == 422
        mock_app_state.briefing_schedule_repository.upsert.assert_not_called()

    def test_opt_out_allowed_when_provider_none(self, client, mock_app_state):
        mock_app_state.briefing_delivery_service = None
        mock_app_state.briefing_schedule_repository.get.return_value = None

        response = client.put(
            "/api/briefings/schedule",
            json={
                "frequency": "daily",
                "hour_local": 8,
                "timezone": "Europe/Zagreb",
                "enabled": True,
                "email_enabled": False,
            },
        )

        assert response.status_code == 200
