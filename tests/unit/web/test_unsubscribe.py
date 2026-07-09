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

"""Unit tests for the one-click unsubscribe route (spec #51)."""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from thestill.utils.unsubscribe_token import make_unsubscribe_token
from thestill.web.routes import unsubscribe

SECRET = "test-secret"


@pytest.fixture
def mock_app_state():
    state = MagicMock()
    state.config.jwt_secret_key = SECRET
    state.config.unsubscribe_secret = ""
    state.briefing_schedule_repository.set_email_enabled.return_value = True
    return state


@pytest.fixture
def client(mock_app_state):
    app = FastAPI()
    app.include_router(unsubscribe.router)
    app.dependency_overrides[unsubscribe.get_app_state] = lambda: mock_app_state
    return TestClient(app)


class TestUnsubscribeGet:
    def test_valid_token_renders_confirmation_without_mutating(self, client, mock_app_state):
        # Mail gateways (SafeLinks, Proofpoint) prefetch every GET in an
        # email body — the flag must only flip on the POST.
        token = make_unsubscribe_token("user-1", SECRET)

        response = client.get("/unsubscribe/briefings", params={"token": token})

        assert response.status_code == 200
        assert "unsubscribe" in response.text.lower()
        assert '<form method="post"' in response.text
        mock_app_state.briefing_schedule_repository.set_email_enabled.assert_not_called()

    def test_confirmation_form_posts_the_same_token(self, client):
        token = make_unsubscribe_token("user-1", SECRET)

        response = client.get("/unsubscribe/briefings", params={"token": token})

        assert f"/unsubscribe/briefings?token={token}" in response.text

    def test_tampered_token_rejected_without_touching_state(self, client, mock_app_state):
        token = make_unsubscribe_token("user-1", "some-other-secret")

        response = client.get("/unsubscribe/briefings", params={"token": token})

        assert response.status_code == 400
        mock_app_state.briefing_schedule_repository.set_email_enabled.assert_not_called()

    def test_missing_token_rejected(self, client, mock_app_state):
        response = client.get("/unsubscribe/briefings")

        assert response.status_code == 400
        mock_app_state.briefing_schedule_repository.set_email_enabled.assert_not_called()


class TestUnsubscribePost:
    def test_one_click_post_works_without_login(self, client, mock_app_state):
        # RFC 8058: mail providers POST to the List-Unsubscribe URL; the
        # confirm page's button posts here too.
        token = make_unsubscribe_token("user-1", SECRET)

        response = client.post(f"/unsubscribe/briefings?token={token}")

        assert response.status_code == 200
        assert "unsubscribed" in response.text.lower()
        mock_app_state.briefing_schedule_repository.set_email_enabled.assert_called_once_with("user-1", False)

    def test_is_idempotent(self, client):
        token = make_unsubscribe_token("user-1", SECRET)

        assert client.post(f"/unsubscribe/briefings?token={token}").status_code == 200
        assert client.post(f"/unsubscribe/briefings?token={token}").status_code == 200

    def test_tampered_token_rejected_without_touching_state(self, client, mock_app_state):
        token = make_unsubscribe_token("user-1", "some-other-secret")

        response = client.post(f"/unsubscribe/briefings?token={token}")

        assert response.status_code == 400
        mock_app_state.briefing_schedule_repository.set_email_enabled.assert_not_called()

    def test_no_schedule_row_still_confirms(self, client, mock_app_state):
        # Goal state ("no more emails") already holds; a distinct error
        # would leak account state to token guessers.
        mock_app_state.briefing_schedule_repository.set_email_enabled.return_value = False
        token = make_unsubscribe_token("user-1", SECRET)

        response = client.post(f"/unsubscribe/briefings?token={token}")

        assert response.status_code == 200

    def test_dedicated_unsubscribe_secret_wins_over_jwt_key(self, client, mock_app_state):
        # A rotated auth secret must not dead-link delivered emails: the
        # route verifies with UNSUBSCRIBE_SECRET when it is set.
        mock_app_state.config.unsubscribe_secret = "dedicated-secret"
        mock_app_state.config.jwt_secret_key = "rotated-away"
        token = make_unsubscribe_token("user-1", "dedicated-secret")

        response = client.post(f"/unsubscribe/briefings?token={token}")

        assert response.status_code == 200
        mock_app_state.briefing_schedule_repository.set_email_enabled.assert_called_once_with("user-1", False)
