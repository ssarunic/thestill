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
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from thestill.models.briefing import Briefing
from thestill.models.briefing_schedule import BriefingFrequency
from thestill.models.podcast import Episode, Podcast
from thestill.models.user import User
from thestill.services.briefing_service import BriefingNotFoundError, Deferred
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
    # Spec #84: the lazy path is the default only while no scheduler runs.
    # A bare MagicMock reads as "scheduler running + enabled schedule", so
    # pin the pre-#84 shape here and opt into the scheduled shape per test.
    state.briefing_scheduler = None
    state.briefing_schedule_repository = MagicMock()
    state.briefing_schedule_repository.get.return_value = None
    return state


@pytest.fixture
def test_app(mock_app_state, mock_user):
    app = FastAPI()
    # Mount like app.py does: router-level require_auth (default-deny).
    app.include_router(
        api_briefings.router, prefix="/api/briefings", dependencies=[Depends(api_briefings.require_auth)]
    )
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

        mock_app_state.briefing_service.generate_for_user.assert_called_once_with(mock_user.id, force=False)

    def test_202_when_readiness_gate_defers(self, client, mock_app_state):
        deadline = datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc)
        mock_app_state.briefing_service.generate_for_user.return_value = Deferred(3, deadline)

        response = client.get("/api/briefings/latest")

        assert response.status_code == 202
        assert response.json()["briefing_pending"] == {
            "pending_count": 3,
            "deadline": deadline.isoformat(),
        }

    def test_force_query_skips_gate(self, client, mock_app_state, mock_user):
        mock_app_state.briefing_service.generate_for_user.return_value = _briefing()

        response = client.get("/api/briefings/latest?force=true")

        assert response.status_code == 200
        mock_app_state.briefing_service.generate_for_user.assert_called_once_with(mock_user.id, force=True)


class TestGetLatestScheduledOnly:
    """Spec #84: with the scheduler running, opening the inbox is a read."""

    NEXT = datetime(2026, 9, 25, 7, 0, tzinfo=timezone.utc)

    @pytest.fixture(autouse=True)
    def scheduler_running(self, mock_app_state):
        mock_app_state.briefing_scheduler = MagicMock()

    def test_enabled_schedule_returns_latest_without_generating(self, client, mock_app_state):
        mock_app_state.briefing_schedule_repository.get.return_value = _schedule(next_run_at=self.NEXT)
        mock_app_state.briefing_service.latest_for_user.return_value = _briefing()

        response = client.get("/api/briefings/latest")

        assert response.status_code == 200
        assert response.json()["id"] == "00000000-0000-0000-0000-000000000001"
        assert response.json()["next_run_at"] == self.NEXT.isoformat()
        mock_app_state.briefing_service.generate_for_user.assert_not_called()

    def test_first_edition_is_still_generated_lazily(self, client, mock_app_state, mock_user):
        mock_app_state.briefing_schedule_repository.get.return_value = _schedule(next_run_at=self.NEXT)
        mock_app_state.briefing_service.latest_for_user.return_value = None
        mock_app_state.briefing_service.generate_for_user.return_value = _briefing()

        response = client.get("/api/briefings/latest")

        assert response.status_code == 200
        assert response.json()["next_run_at"] == self.NEXT.isoformat()
        mock_app_state.briefing_service.generate_for_user.assert_called_once_with(mock_user.id, force=False)

    def test_force_still_generates(self, client, mock_app_state, mock_user):
        mock_app_state.briefing_schedule_repository.get.return_value = _schedule(next_run_at=self.NEXT)
        mock_app_state.briefing_service.latest_for_user.return_value = _briefing()
        mock_app_state.briefing_service.generate_for_user.return_value = _briefing(
            briefing_id="00000000-0000-0000-0000-000000000002"
        )

        response = client.get("/api/briefings/latest?force=true")

        assert response.status_code == 200
        assert response.json()["id"] == "00000000-0000-0000-0000-000000000002"
        mock_app_state.briefing_service.generate_for_user.assert_called_once_with(mock_user.id, force=True)

    def test_disabled_schedule_falls_back_to_lazy(self, client, mock_app_state, mock_user):
        mock_app_state.briefing_schedule_repository.get.return_value = _schedule(enabled=False)
        mock_app_state.briefing_service.generate_for_user.return_value = _briefing()

        response = client.get("/api/briefings/latest")

        assert response.status_code == 200
        assert "next_run_at" not in response.json()
        mock_app_state.briefing_service.latest_for_user.assert_not_called()
        mock_app_state.briefing_service.generate_for_user.assert_called_once_with(mock_user.id, force=False)

    def test_no_schedule_and_tz_seeds_daily_default(self, client, mock_app_state):
        mock_app_state.briefing_schedule_repository.get.return_value = None
        mock_app_state.briefing_service.latest_for_user.return_value = _briefing()

        response = client.get("/api/briefings/latest?tz=Europe/London")

        assert response.status_code == 200
        upserted = mock_app_state.briefing_schedule_repository.upsert.call_args.args[0]
        assert upserted.user_id == "user-1"
        assert upserted.frequency is BriefingFrequency.DAILY
        assert upserted.hour_local == 8
        assert upserted.timezone_name == "Europe/London"
        assert upserted.enabled is True
        assert upserted.next_run_at is not None
        assert upserted.next_run_at > datetime.now(timezone.utc)
        assert response.json()["next_run_at"] == upserted.next_run_at.isoformat()
        mock_app_state.briefing_service.generate_for_user.assert_not_called()

    def test_no_schedule_without_tz_stays_lazy(self, client, mock_app_state, mock_user):
        mock_app_state.briefing_schedule_repository.get.return_value = None
        mock_app_state.briefing_service.generate_for_user.return_value = _briefing()

        response = client.get("/api/briefings/latest")

        assert response.status_code == 200
        mock_app_state.briefing_schedule_repository.upsert.assert_not_called()
        mock_app_state.briefing_service.generate_for_user.assert_called_once_with(mock_user.id, force=False)

    def test_unknown_tz_does_not_seed_or_break(self, client, mock_app_state, mock_user):
        mock_app_state.briefing_schedule_repository.get.return_value = None
        mock_app_state.briefing_service.generate_for_user.return_value = _briefing()

        response = client.get("/api/briefings/latest?tz=Mars/Olympus")

        assert response.status_code == 200
        mock_app_state.briefing_schedule_repository.upsert.assert_not_called()
        mock_app_state.briefing_service.generate_for_user.assert_called_once_with(mock_user.id, force=False)

    def test_scheduler_off_ignores_schedule(self, client, mock_app_state, mock_user):
        mock_app_state.briefing_scheduler = None
        mock_app_state.briefing_schedule_repository.get.return_value = _schedule(next_run_at=self.NEXT)
        mock_app_state.briefing_service.generate_for_user.return_value = _briefing()

        response = client.get("/api/briefings/latest?tz=Europe/London")

        assert response.status_code == 200
        assert "next_run_at" not in response.json()
        mock_app_state.briefing_schedule_repository.upsert.assert_not_called()
        mock_app_state.briefing_service.generate_for_user.assert_called_once_with(mock_user.id, force=False)


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
        # a provider is configured — and the scheduler that runs the
        # delivery pass is on, so opting in is allowed.
        mock_app_state.briefing_scheduler = MagicMock()
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

    def test_omitted_email_enabled_preserves_stored_opt_in(self, client, mock_app_state):
        # A pre-#51 client (or a UI whose capability probe failed) PUTs
        # without the field — that must not wipe the user's opt-in.
        mock_app_state.briefing_schedule_repository.get.return_value = _schedule(email_enabled=True)

        response = client.put(
            "/api/briefings/schedule",
            json={"frequency": "daily", "hour_local": 9, "timezone": "Europe/Zagreb", "enabled": True},
        )

        assert response.status_code == 200
        saved = mock_app_state.briefing_schedule_repository.upsert.call_args.args[0]
        assert saved.email_enabled is True

    def test_explicit_false_overrides_stored_opt_in(self, client, mock_app_state):
        mock_app_state.briefing_schedule_repository.get.return_value = _schedule(email_enabled=True)

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
        saved = mock_app_state.briefing_schedule_repository.upsert.call_args.args[0]
        assert saved.email_enabled is False

    def test_opt_in_rejected_when_scheduler_off(self, client, mock_app_state):
        # The delivery pass runs on the briefing scheduler tick; without
        # it an accepted opt-in would silently never send.
        mock_app_state.briefing_scheduler = None

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


# ============================================================================
# GET /api/briefings/{id}/episodes
# ============================================================================


def _podcast(podcast_id: str, title: str, image_url: str | None = None) -> Podcast:
    return Podcast(
        id=podcast_id,
        rss_url="https://example.com/feed.xml",
        title=title,
        description="",
        image_url=image_url,
    )


def _episode(
    episode_id: str,
    title: str,
    *,
    image_url: str | None = None,
    duration: int | None = None,
    summary_preview: str | None = None,
    summary_path: str | None = None,
) -> Episode:
    return Episode(
        id=episode_id,
        external_id=f"guid-{episode_id}",
        title=title,
        description="",
        audio_url="https://example.com/audio.mp3",
        pub_date=datetime(2026, 5, 1, 6, 0, tzinfo=timezone.utc),
        duration=duration,
        image_url=image_url,
        summary_preview=summary_preview,
        summary_path=summary_path,
    )


class TestGetBriefingEpisodes:
    URL = "/api/briefings/00000000-0000-0000-0000-000000000001/episodes"

    def test_groups_episodes_by_podcast_in_delivery_order(self, client, mock_app_state):
        briefing = _briefing()
        mock_app_state.briefing_repository.get.return_value = briefing
        mock_app_state.inbox_repository.list_episode_ids_in_window.return_value = ["ep-1", "ep-2", "ep-3"]
        show_a = _podcast("pod-a", "Show A", image_url="https://img/a.jpg")
        show_b = _podcast("pod-b", "Show B")
        mock_app_state.repository.get_episodes_by_ids.return_value = {
            "ep-1": (show_a, _episode("ep-1", "First", duration=3725, summary_preview="Gist one.")),
            "ep-2": (show_b, _episode("ep-2", "Second", image_url="https://img/ep2.jpg", summary_preview="")),
            "ep-3": (show_a, _episode("ep-3", "Third", summary_path="third.md", summary_preview="Gist three.")),
        }

        response = client.get(self.URL)

        assert response.status_code == 200
        data = response.json()
        assert data["briefing_id"] == briefing.id
        assert data["episode_count"] == 3
        assert [p["title"] for p in data["podcasts"]] == ["Show A", "Show B"]
        show_a_out = data["podcasts"][0]
        assert show_a_out["image_url"] == "https://img/a.jpg"
        assert [e["id"] for e in show_a_out["episodes"]] == ["ep-1", "ep-3"]
        first = show_a_out["episodes"][0]
        assert first["title"] == "First"
        assert first["duration"] == 3725
        assert first["duration_formatted"] == "1:02:05"
        assert first["summary_preview"] == "Gist one."
        assert first["summary_available"] is False
        assert first["pub_date"] == "2026-05-01T06:00:00+00:00"
        second = data["podcasts"][1]["episodes"][0]
        assert second["image_url"] == "https://img/ep2.jpg"
        assert second["summary_preview"] is None  # "" (nothing extractable) renders as no preview
        assert second["duration_formatted"] is None
        assert show_a_out["episodes"][1]["summary_available"] is True

        # Same window the narration runner resolves: still-eligible rows plus
        # rows read after the briefing was cut.
        mock_app_state.inbox_repository.list_episode_ids_in_window.assert_called_once_with(
            "user-1",
            since=briefing.cursor_from,
            until=briefing.cursor_to,
            states=("unread", "saved"),
            read_since=briefing.created_at,
        )
        mock_app_state.repository.get_episodes_by_ids.assert_called_once_with(["ep-1", "ep-2", "ep-3"])

    def test_skips_episodes_deleted_since_render(self, client, mock_app_state):
        mock_app_state.briefing_repository.get.return_value = _briefing()
        mock_app_state.inbox_repository.list_episode_ids_in_window.return_value = ["ep-1", "gone"]
        mock_app_state.repository.get_episodes_by_ids.return_value = {
            "ep-1": (_podcast("pod-a", "Show A"), _episode("ep-1", "First", summary_preview="x")),
        }

        response = client.get(self.URL)

        assert response.status_code == 200
        data = response.json()
        assert data["episode_count"] == 1
        assert [e["id"] for e in data["podcasts"][0]["episodes"]] == ["ep-1"]

    def test_empty_window_returns_no_podcasts(self, client, mock_app_state):
        mock_app_state.briefing_repository.get.return_value = _briefing()
        mock_app_state.inbox_repository.list_episode_ids_in_window.return_value = []
        mock_app_state.repository.get_episodes_by_ids.return_value = {}

        response = client.get(self.URL)

        assert response.status_code == 200
        assert response.json() == {
            **response.json(),
            "podcasts": [],
            "episode_count": 0,
        }

    def test_404_when_briefing_missing(self, client, mock_app_state):
        mock_app_state.briefing_repository.get.return_value = None

        response = client.get(self.URL)

        assert response.status_code == 404
        mock_app_state.inbox_repository.list_episode_ids_in_window.assert_not_called()

    def test_404_when_briefing_belongs_to_another_user(self, client, mock_app_state):
        mock_app_state.briefing_repository.get.return_value = _briefing(user_id="someone-else")

        response = client.get(self.URL)

        assert response.status_code == 404
        mock_app_state.inbox_repository.list_episode_ids_in_window.assert_not_called()
