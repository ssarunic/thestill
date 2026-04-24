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

"""Regression tests for spec #25 items 3.5 (log redaction) and 3.9 (CRLF)."""

import pytest

from thestill.utils.log_safety import (
    log_safety_processor,
    redact_mapping,
    sanitize_control_chars,
)


class TestRedactMapping:
    @pytest.mark.parametrize(
        "key",
        [
            "token",
            "auth_token",
            "Authorization",
            "X-Api-Key",
            "password",
            "client_secret",
            "set-cookie",
            "SESSION_ID",
            "oauth_code",
            "state",  # OAuth state
        ],
    )
    def test_sensitive_keys_redacted(self, key):
        out = redact_mapping({key: "s3cret"})
        assert out[key] == "[redacted]"

    def test_non_sensitive_keys_passed_through(self):
        out = redact_mapping({"podcast_title": "Tech News", "episode_id": "abc"})
        assert out == {"podcast_title": "Tech News", "episode_id": "abc"}

    def test_nested_dicts_walked(self):
        out = redact_mapping(
            {"webhook_metadata": {"token": "xyz", "episode_id": "abc"}},
        )
        assert out["webhook_metadata"]["token"] == "[redacted]"
        assert out["webhook_metadata"]["episode_id"] == "abc"

    def test_list_of_dicts_walked(self):
        out = redact_mapping({"items": [{"api_key": "k1"}, {"label": "ok"}]})
        assert out["items"][0]["api_key"] == "[redacted]"
        assert out["items"][1]["label"] == "ok"

    def test_non_string_keys_pass_through_unchanged(self):
        out = redact_mapping({42: "number-keyed"})
        assert out[42] == "number-keyed"


class TestSanitizeControlChars:
    def test_crlf_escaped(self):
        out = sanitize_control_chars("title\r\n{\"injected\":true}")
        assert "\r" not in out
        assert "\n" not in out
        assert "\\x0d" in out or "\\x0a" in out

    def test_null_byte_escaped(self):
        out = sanitize_control_chars("before\x00after")
        assert "\x00" not in out
        assert "\\x00" in out

    def test_c1_control_chars_escaped(self):
        out = sanitize_control_chars("x\x85y")
        assert "\x85" not in out

    def test_tab_preserved(self):
        # Tab is harmless in console + JSON logs.
        assert sanitize_control_chars("a\tb") == "a\tb"

    def test_non_string_untouched(self):
        assert sanitize_control_chars(42) == 42
        assert sanitize_control_chars(None) is None
        assert sanitize_control_chars(["a", 1]) == ["a", 1]


class TestProcessor:
    def test_processor_redacts_and_sanitises_in_place(self):
        event = {
            "event": "webhook_received",
            "token": "supersecret",
            "podcast_title": "Evil\r\n{\"forged\":true}",
            "nested": {"password": "hunter2", "count": 3},
        }
        out = log_safety_processor(None, "info", event)
        assert out["token"] == "[redacted]"
        assert out["nested"]["password"] == "[redacted]"
        assert "\r" not in out["podcast_title"]
        assert "\n" not in out["podcast_title"]
        assert out["nested"]["count"] == 3
