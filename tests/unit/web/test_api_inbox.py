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

"""Unit tests for the per-user inbox API endpoints."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from thestill.models.inbox import InboxEntry, InboxItem, PodcastInboxSummary
from thestill.models.podcast import Episode
from thestill.models.user import User
from thestill.services.inbox_service import InboxEntryNotFoundError, InvalidInboxStateError
from thestill.web.routes import api_inbox


@pytest.fixture
def mock_user():
    return User(id="user-1", email="alice@example.com", name="Alice")


def _episode(episode_id: str = "ep-1") -> Episode:
    return Episode(
        id=episode_id,
        podcast_id="pod-1",
        external_id=episode_id,
        title="Test Episode",
        description="d",
        audio_url="https://example.com/a.mp3",
    )


def _item(*, episode_id: str = "ep-1", state: str = "unread", delivered_at: datetime) -> InboxItem:
    return InboxItem(
        entry=InboxEntry(
            user_id="user-1",
            episode_id=episode_id,
            source="follow_new",
            state=state,
            delivered_at=delivered_at,
        ),
        episode=_episode(episode_id),
        podcast=PodcastInboxSummary(id="pod-1", title="P", slug="p", image_url=None),
    )


@pytest.fixture
def mock_app_state():
    state = MagicMock()
    state.inbox_service = MagicMock()
    return state


@pytest.fixture
def test_app(mock_app_state, mock_user):
    app = FastAPI()
    app.include_router(api_inbox.router, prefix="/api/inbox")
    app.dependency_overrides[api_inbox.get_app_state] = lambda: mock_app_state
    app.dependency_overrides[api_inbox.require_auth] = lambda: mock_user
    return app


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


# ============================================================================
# GET /api/inbox
# ============================================================================


class TestListInbox:
    def test_returns_empty_when_no_items(self, client, mock_app_state):
        mock_app_state.inbox_service.list.return_value = []

        response = client.get("/api/inbox")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["items"] == []
        assert data["count"] == 0
        assert data["next_before"] is None

    def test_returns_items_with_episode_and_podcast(self, client, mock_app_state):
        delivered = datetime(2026, 5, 1, tzinfo=timezone.utc)
        mock_app_state.inbox_service.list.return_value = [_item(delivered_at=delivered)]

        response = client.get("/api/inbox")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        item = data["items"][0]
        assert item["entry"]["episode_id"] == "ep-1"
        assert item["episode"]["title"] == "Test Episode"
        assert item["podcast"]["slug"] == "p"

    def test_passes_state_filter_through(self, client, mock_app_state):
        mock_app_state.inbox_service.list.return_value = []

        response = client.get("/api/inbox?state=saved")

        assert response.status_code == 200
        kwargs = mock_app_state.inbox_service.list.call_args.kwargs
        assert kwargs["state"] == "saved"

    def test_rejects_invalid_state(self, client, mock_app_state):
        from thestill.services.inbox_service import InvalidInboxStateError

        mock_app_state.inbox_service.list.side_effect = InvalidInboxStateError("bad")

        response = client.get("/api/inbox?state=archived")

        assert response.status_code == 400

    def test_passes_before_cursor_through(self, client, mock_app_state):
        mock_app_state.inbox_service.list.return_value = []
        cursor = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)

        response = client.get("/api/inbox", params={"before": cursor.isoformat()})

        assert response.status_code == 200
        kwargs = mock_app_state.inbox_service.list.call_args.kwargs
        assert kwargs["before"] == cursor

    def test_rejects_malformed_before_cursor(self, client):
        response = client.get("/api/inbox?before=not-a-date")
        assert response.status_code == 400

    def test_rejects_out_of_range_limit(self, client):
        assert client.get("/api/inbox?limit=0").status_code == 400
        assert client.get("/api/inbox?limit=10000").status_code == 400

    def test_next_before_set_when_full_page_returned(self, client, mock_app_state):
        base = datetime(2026, 5, 1, tzinfo=timezone.utc)
        items = [_item(episode_id=f"ep-{i}", delivered_at=base + timedelta(hours=i)) for i in range(3)]
        mock_app_state.inbox_service.list.return_value = items

        response = client.get("/api/inbox?limit=3")

        assert response.status_code == 200
        data = response.json()
        assert data["next_before"] == items[-1].entry.delivered_at.isoformat()

    def test_next_before_null_when_partial_page(self, client, mock_app_state):
        base = datetime(2026, 5, 1, tzinfo=timezone.utc)
        mock_app_state.inbox_service.list.return_value = [_item(delivered_at=base)]

        response = client.get("/api/inbox?limit=50")

        assert response.json()["next_before"] is None


# ============================================================================
# GET /api/inbox/unread-count
# ============================================================================


class TestUnreadCount:
    def test_returns_count_from_service(self, client, mock_app_state):
        mock_app_state.inbox_service.unread_count.return_value = 7

        response = client.get("/api/inbox/unread-count")

        assert response.status_code == 200
        assert response.json()["unread_count"] == 7
        mock_app_state.inbox_service.unread_count.assert_called_once_with("user-1")


# ============================================================================
# POST /api/inbox/{episode_id}/read
# ============================================================================


class TestMarkInboxRead:
    def test_marks_and_returns_true_when_row_transitioned(self, client, mock_app_state):
        mock_app_state.inbox_service.mark_read.return_value = True

        response = client.post("/api/inbox/ep-1/read")

        assert response.status_code == 200
        assert response.json()["marked"] is True
        mock_app_state.inbox_service.mark_read.assert_called_once_with("user-1", "ep-1")

    def test_no_row_or_non_unread_returns_200_with_marked_false(self, client, mock_app_state):
        # The episode page fires this blindly for every summary view; a
        # missing inbox row must be a quiet no-op, not a 404.
        mock_app_state.inbox_service.mark_read.return_value = False

        response = client.post("/api/inbox/never-delivered/read")

        assert response.status_code == 200
        assert response.json()["marked"] is False


# ============================================================================
# POST /api/inbox/{episode_id}/state
# ============================================================================


class TestSetInboxState:
    def test_sets_state_and_returns_entry(self, client, mock_app_state):
        delivered = datetime(2026, 5, 1, tzinfo=timezone.utc)
        updated = InboxEntry(
            user_id="user-1",
            episode_id="ep-1",
            source="follow_new",
            state="saved",
            delivered_at=delivered,
            state_changed_at=delivered,
        )
        mock_app_state.inbox_service.mark_state.return_value = updated

        response = client.post("/api/inbox/ep-1/state", json={"state": "saved"})

        assert response.status_code == 200
        data = response.json()
        assert data["entry"]["state"] == "saved"
        assert data["entry"]["episode_id"] == "ep-1"
        mock_app_state.inbox_service.mark_state.assert_called_once_with("user-1", "ep-1", "saved")

    def test_invalid_state_returns_400(self, client, mock_app_state):
        mock_app_state.inbox_service.mark_state.side_effect = InvalidInboxStateError("bad")

        response = client.post("/api/inbox/ep-1/state", json={"state": "archived"})

        assert response.status_code == 400

    def test_missing_entry_returns_404(self, client, mock_app_state):
        mock_app_state.inbox_service.mark_state.side_effect = InboxEntryNotFoundError("none")

        response = client.post("/api/inbox/missing/state", json={"state": "read"})

        assert response.status_code == 404

    def test_missing_body_returns_422(self, client):
        response = client.post("/api/inbox/ep-1/state", json={})
        # FastAPI's Pydantic validation rejects with 422 on missing field.
        assert response.status_code == 422
