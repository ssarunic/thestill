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

"""Unit tests for signed unsubscribe tokens (spec #51)."""

import base64
import json

import pytest

from thestill.utils.unsubscribe_token import make_unsubscribe_token, verify_unsubscribe_token

SECRET = "test-secret"


class TestRoundTrip:
    def test_valid_token_returns_user_id(self):
        token = make_unsubscribe_token("user-123", SECRET)
        assert verify_unsubscribe_token(token, SECRET) == "user-123"

    def test_token_is_url_safe(self):
        token = make_unsubscribe_token("user-123", SECRET)
        assert all(c.isalnum() or c in "-_." for c in token)

    def test_empty_secret_refuses_to_sign(self):
        with pytest.raises(ValueError):
            make_unsubscribe_token("user-123", "")


class TestRejection:
    def test_wrong_secret(self):
        token = make_unsubscribe_token("user-123", SECRET)
        assert verify_unsubscribe_token(token, "other-secret") is None

    def test_tampered_payload(self):
        token = make_unsubscribe_token("user-123", SECRET)
        _, signature = token.split(".")
        forged_payload = base64.urlsafe_b64encode(json.dumps({"u": "victim-456"}).encode()).rstrip(b"=").decode()
        assert verify_unsubscribe_token(f"{forged_payload}.{signature}", SECRET) is None

    def test_garbage_inputs(self):
        for bad in ["", "no-dot", "a.b.c", "not!base64.###", make_unsubscribe_token("u", SECRET)[:-4]]:
            assert verify_unsubscribe_token(bad, SECRET) is None

    def test_empty_secret_verifies_nothing(self):
        token = make_unsubscribe_token("user-123", SECRET)
        assert verify_unsubscribe_token(token, "") is None

    def test_auth_jwt_is_not_a_valid_unsubscribe_token(self):
        # The unsubscribe format must never accept a login JWT signed with
        # the same secret (three dot-separated segments).
        import jwt as pyjwt

        jwt_token = pyjwt.encode({"sub": "user-123"}, SECRET, algorithm="HS256")
        assert verify_unsubscribe_token(jwt_token, SECRET) is None
