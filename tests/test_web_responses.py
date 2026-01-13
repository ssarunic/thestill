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
Unit tests for web response helpers.
"""

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from thestill.web.responses import api_response, bad_request, conflict, not_found, paginated_response


class TestApiResponse:
    """Tests for api_response helper."""

    def test_basic_response_has_status_and_timestamp(self):
        """Response includes status and timestamp."""
        result = api_response({})

        assert result["status"] == "ok"
        assert "timestamp" in result
        # Verify timestamp is valid ISO format
        datetime.fromisoformat(result["timestamp"])

    def test_response_includes_data_fields(self):
        """Data fields are spread into response."""
        result = api_response({"user": "alice", "count": 5})

        assert result["user"] == "alice"
        assert result["count"] == 5
        assert result["status"] == "ok"

    def test_custom_status(self):
        """Custom status can be provided."""
        result = api_response({"message": "created"}, status="created")

        assert result["status"] == "created"

    def test_timestamp_is_utc(self):
        """Timestamp should be in UTC."""
        before = datetime.now(timezone.utc)
        result = api_response({})
        after = datetime.now(timezone.utc)

        timestamp = datetime.fromisoformat(result["timestamp"])
        assert before <= timestamp <= after

    def test_nested_data(self):
        """Nested data structures are preserved."""
        data = {
            "podcast": {
                "title": "Test Podcast",
                "episodes": [{"id": 1}, {"id": 2}],
            }
        }
        result = api_response(data)

        assert result["podcast"]["title"] == "Test Podcast"
        assert len(result["podcast"]["episodes"]) == 2


class TestPaginatedResponse:
    """Tests for paginated_response helper."""

    def test_basic_pagination_fields(self):
        """Response includes all pagination fields."""
        items = [{"id": 1}, {"id": 2}]
        result = paginated_response(items, total=10, offset=0, limit=5)

        assert result["status"] == "ok"
        assert "timestamp" in result
        assert result["items"] == items
        assert result["count"] == 2
        assert result["total"] == 10
        assert result["offset"] == 0
        assert result["limit"] == 5

    def test_has_more_when_more_items_exist(self):
        """has_more is True when more items exist."""
        items = [{"id": 1}, {"id": 2}]
        result = paginated_response(items, total=10, offset=0, limit=5)

        assert result["has_more"] is True
        assert result["next_offset"] == 5

    def test_has_more_false_on_last_page(self):
        """has_more is False on last page."""
        items = [{"id": 9}, {"id": 10}]
        result = paginated_response(items, total=10, offset=8, limit=5)

        assert result["has_more"] is False
        assert result["next_offset"] is None

    def test_has_more_false_when_exact_fit(self):
        """has_more is False when items exactly fill total."""
        items = [{"id": 1}, {"id": 2}, {"id": 3}]
        result = paginated_response(items, total=3, offset=0, limit=5)

        assert result["has_more"] is False
        assert result["next_offset"] is None

    def test_custom_items_key(self):
        """Custom items_key changes the key name."""
        items = [{"id": 1}]
        result = paginated_response(items, total=1, offset=0, limit=10, items_key="podcasts")

        assert "podcasts" in result
        assert "items" not in result
        assert result["podcasts"] == items

    def test_empty_items(self):
        """Empty items list is handled correctly."""
        result = paginated_response([], total=0, offset=0, limit=10)

        assert result["items"] == []
        assert result["count"] == 0
        assert result["total"] == 0
        assert result["has_more"] is False

    def test_next_offset_calculation(self):
        """next_offset is calculated correctly for middle pages."""
        items = [{"id": 6}, {"id": 7}, {"id": 8}, {"id": 9}, {"id": 10}]
        result = paginated_response(items, total=20, offset=5, limit=5)

        assert result["next_offset"] == 10
        assert result["has_more"] is True


class TestNotFound:
    """Tests for not_found helper."""

    def test_raises_404(self):
        """Raises HTTPException with 404 status."""
        with pytest.raises(HTTPException) as exc_info:
            not_found("Podcast", "my-slug")

        assert exc_info.value.status_code == 404

    def test_includes_resource_and_identifier(self):
        """Error detail includes resource type and identifier."""
        with pytest.raises(HTTPException) as exc_info:
            not_found("Episode", "episode-123")

        assert "Episode not found: episode-123" in exc_info.value.detail


class TestBadRequest:
    """Tests for bad_request helper."""

    def test_raises_400(self):
        """Raises HTTPException with 400 status."""
        with pytest.raises(HTTPException) as exc_info:
            bad_request("Invalid date format")

        assert exc_info.value.status_code == 400

    def test_includes_message(self):
        """Error detail includes the message."""
        with pytest.raises(HTTPException) as exc_info:
            bad_request("Field 'name' is required")

        assert exc_info.value.detail == "Field 'name' is required"


class TestConflict:
    """Tests for conflict helper."""

    def test_raises_409(self):
        """Raises HTTPException with 409 status."""
        with pytest.raises(HTTPException) as exc_info:
            conflict("Task already running")

        assert exc_info.value.status_code == 409

    def test_includes_message(self):
        """Error detail includes the message."""
        with pytest.raises(HTTPException) as exc_info:
            conflict("Resource already exists")

        assert exc_info.value.detail == "Resource already exists"
