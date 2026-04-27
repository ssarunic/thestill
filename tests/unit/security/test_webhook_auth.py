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

"""Regression tests for spec #25, item 1.3 — webhook fail-closed + replay guard."""

import hashlib
import hmac
import time

import pytest

from thestill.web.routes import webhooks as webhook_route

SECRET = "shared-secret"
BODY = b'{"hello":"world"}'


def _signature_header(secret: str, body: bytes, *, skew: int = 0, version: str = "v0") -> str:
    timestamp = str(int(time.time()) + skew)
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},{version}={mac}"


class TestVerifySignature:
    def test_missing_secret_returns_false(self):
        assert webhook_route._verify_signature(BODY, _signature_header(SECRET, BODY), "") is False

    def test_missing_header_returns_false(self):
        assert webhook_route._verify_signature(BODY, "", SECRET) is False

    def test_valid_signature_accepted(self):
        header = _signature_header(SECRET, BODY)
        assert webhook_route._verify_signature(BODY, header, SECRET) is True

    def test_tampered_body_rejected(self):
        header = _signature_header(SECRET, BODY)
        assert webhook_route._verify_signature(b'{"hello":"attacker"}', header, SECRET) is False

    def test_wrong_secret_rejected(self):
        header = _signature_header(SECRET, BODY)
        assert webhook_route._verify_signature(BODY, header, "wrong-secret") is False

    def test_unparseable_header_rejected(self):
        # No '=' separators at all.
        assert webhook_route._verify_signature(BODY, "garbage-header", SECRET) is False

    def test_header_without_version_rejected(self):
        ts = str(int(time.time()))
        # Timestamp but no v<N>= pair.
        assert webhook_route._verify_signature(BODY, f"t={ts}", SECRET) is False

    def test_header_without_timestamp_rejected(self):
        # Signature but no t=.
        mac = hmac.new(SECRET.encode(), b"0." + BODY, hashlib.sha256).hexdigest()
        assert webhook_route._verify_signature(BODY, f"v0={mac}", SECRET) is False

    def test_replay_outside_window_rejected(self):
        """Signed 10 minutes ago — must be refused even with a valid signature."""
        stale = _signature_header(SECRET, BODY, skew=-600)
        assert webhook_route._verify_signature(BODY, stale, SECRET) is False

    def test_future_timestamp_outside_window_rejected(self):
        """Signed 10 minutes in the future — also refused."""
        future = _signature_header(SECRET, BODY, skew=600)
        assert webhook_route._verify_signature(BODY, future, SECRET) is False

    def test_non_integer_timestamp_rejected(self):
        mac = hmac.new(SECRET.encode(), b"not-a-ts." + BODY, hashlib.sha256).hexdigest()
        assert webhook_route._verify_signature(BODY, f"t=not-a-ts,v0={mac}", SECRET) is False

    @pytest.mark.parametrize("version", ["v0", "v1", "v2", "v9"])
    def test_supports_multiple_versions(self, version):
        header = _signature_header(SECRET, BODY, version=version)
        assert webhook_route._verify_signature(BODY, header, SECRET) is True


class TestNoSecretFailClosedConstant:
    """The route must have an explicit dev-override env var — not default-open."""

    def test_dev_override_env_var_defined(self):
        # Prevent accidental removal of the opt-in name, which the pre-deploy
        # checklist (spec #26) also greps for.
        assert webhook_route._DEV_ALLOW_UNSIGNED_ENV == "DEV_ALLOW_UNSIGNED_WEBHOOKS"

    def test_clock_skew_constant_reasonable(self):
        # Between 1 minute and 15 minutes — tight enough to block replay, loose
        # enough to survive normal clock drift.
        assert 60 <= webhook_route._MAX_WEBHOOK_CLOCK_SKEW_SECONDS <= 900


# Spec #25 item 4.4: webhook payloads on disk should be owner-only.
class TestWebhookPayloadFileMode:
    def test_saved_file_is_chmod_0600(self, tmp_path):
        import os
        import stat

        path = webhook_route._save_webhook_result(
            webhook_dir=tmp_path,
            transcription_id="abc123",
            data={"foo": "bar"},
        )
        assert path.exists()
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600, f"expected 0o600, got {oct(mode)}"
