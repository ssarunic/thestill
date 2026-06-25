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

"""Tests for :mod:`thestill.core.error_classifier`."""

import pytest

from thestill.core.audio_downloader import DownloadError
from thestill.core.error_classifier import (
    classify_and_raise,
    classify_error_class,
    is_fatal_error,
    is_infrastructure_error,
    is_transient_error,
)
from thestill.utils.exceptions import FatalError, TransientError


class _StatusResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _HttpError(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.response = _StatusResponse(status_code)


# The deterministic download guards added alongside the redirect-cap bump:
# each fails identically on every retry, so it must classify as fatal and not
# masquerade as a transient "temporary error" in the UI.
DETERMINISTIC_DOWNLOAD_GUARDS = [
    "Failed to download 'X': Refusing to download from unsafe URL: "
    "too many redirects fetching 'https://dts.podtrac.com/...' (cap=10)",
    "Refusing to download from unsafe URL: blocked private address 10.0.0.1",
    "Refusing download: server advertised 999999999 bytes, cap is 500 bytes",
]


class TestDownloadGuardClassification:
    @pytest.mark.parametrize("message", DETERMINISTIC_DOWNLOAD_GUARDS)
    def test_download_guards_are_fatal(self, message):
        exc = DownloadError(message)
        assert is_fatal_error(exc) is True
        assert is_transient_error(exc) is False

    @pytest.mark.parametrize("message", DETERMINISTIC_DOWNLOAD_GUARDS)
    def test_download_guards_raise_fatal(self, message):
        with pytest.raises(FatalError):
            classify_and_raise(DownloadError(message), context="downloading audio")


class TestTransientClassification:
    @pytest.mark.parametrize(
        "message",
        [
            "Network error downloading: Connection reset by peer",
            "Read timed out",
            "503 Server Error: Service Unavailable",
            "Too many requests, rate limit exceeded",
            "database is locked",
        ],
    )
    def test_transient_messages(self, message):
        exc = Exception(message)
        assert is_transient_error(exc) is True
        assert is_fatal_error(exc) is False

    def test_transient_http_status_raises_transient(self):
        with pytest.raises(TransientError):
            classify_and_raise(_HttpError("boom", 503))


class TestFatalClassification:
    @pytest.mark.parametrize(
        "message",
        [
            "Episode not found",
            "404 Client Error: Not Found",
            "Permission denied",
            "Unsupported codec",
        ],
    )
    def test_fatal_messages(self, message):
        assert is_fatal_error(Exception(message)) is True

    def test_fatal_http_status_raises_fatal(self):
        with pytest.raises(FatalError):
            classify_and_raise(_HttpError("nope", 404))

    def test_file_not_found_is_fatal(self):
        assert is_fatal_error(FileNotFoundError("missing.wav")) is True


class TestInfrastructureClassification:
    """Spec #49 — infra-vs-item attribution for queue auto-healing."""

    # The exact strings the 2026-06-23 outage produced, none of which match
    # the generic ``dns.*fail`` transient pattern.
    INFRA_MESSAGES = [
        "Failed to connect: [Errno 8] nodename nor servname provided, or not known",
        "transcribing X: Failed to connect: [Errno 8] nodename nor servname provided",
        "[Errno -2] Name or service not known",
        "Temporary failure in name resolution",
        "Job failed: Model selection failed (runtime_unavailable), stage=transcribe",
        "HTTPConnectionPool: Max retries exceeded with url: /v1/transcribe",
        "Connection refused",
    ]

    @pytest.mark.parametrize("message", INFRA_MESSAGES)
    def test_infra_messages_detected(self, message):
        assert is_infrastructure_error(Exception(message)) is True
        assert classify_error_class(Exception(message)) == "infra"

    @pytest.mark.parametrize(
        "message",
        [
            "Read timed out",
            "Rate limit exceeded",
            "database is locked",
            "something inexplicable",
        ],
    )
    def test_non_infra_transient_is_item(self, message):
        # Generic transient errors are per-item, not infra — they keep the
        # 3-strike budget and are NOT auto-healed.
        assert is_infrastructure_error(Exception(message)) is False
        assert classify_error_class(Exception(message)) == "item"

    @pytest.mark.parametrize(
        "message",
        ["404 Not Found", "Episode not found", "Unsupported codec"],
    )
    def test_fatal_wins_over_infra(self, message):
        # Fatal is checked first; a fatal error is never relabelled infra.
        assert classify_error_class(Exception(message)) == "fatal"

    def test_wrapped_transient_preserves_infra_signature(self):
        # The worker reclassifies the caught TransientError, whose message
        # still carries the original infra signature.
        err = TransientError("downloading audio: Failed to connect: [Errno 8] nodename nor servname provided")
        assert classify_error_class(err) == "infra"


class TestClassifyAndRaiseDefaults:
    def test_unknown_defaults_to_transient(self):
        with pytest.raises(TransientError):
            classify_and_raise(Exception("something inexplicable"))

    def test_unknown_can_default_to_fatal(self):
        with pytest.raises(FatalError):
            classify_and_raise(Exception("something inexplicable"), default_transient=False)

    def test_already_classified_passthrough(self):
        original = FatalError("already decided")
        with pytest.raises(FatalError) as caught:
            classify_and_raise(original)
        assert caught.value is original
