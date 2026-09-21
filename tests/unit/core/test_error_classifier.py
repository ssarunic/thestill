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

import json

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


class TestProgrammingErrorsAreFatal:
    """2026-09-21: the storage-root guard raised a ``ValueError`` whose text
    matched no pattern, so it defaulted to transient - three pointless
    retries and "this may be a temporary issue" on an error no retry could
    ever fix. A programming error re-runs the same code on the same input.
    """

    @pytest.mark.parametrize(
        "exc",
        [
            ValueError("some value our code rejected"),
            TypeError("unsupported operand type(s) for +: 'int' and 'str'"),
            KeyError("episode_id"),
            IndexError("list index out of range"),
            AttributeError("'NoneType' object has no attribute 'slug'"),
            AssertionError("invariant broken"),
            NotImplementedError("backend does not implement this"),
        ],
    )
    def test_programming_errors_raise_fatal_instead_of_defaulting_to_transient(self, exc):
        with pytest.raises(FatalError):
            classify_and_raise(exc, context="downloading audio for X")

    @pytest.mark.parametrize(
        "exc",
        [
            json.JSONDecodeError("Expecting value", "<html>502 Bad Gateway</html>", 0),
            UnicodeDecodeError("utf-8", b"\xff\xfe", 0, 1, "invalid start byte"),
            ValueError("connection reset by peer"),  # transient pattern wins over the type
            TypeError("request timed out"),
        ],
    )
    def test_network_shaped_failures_keep_their_retries(self, exc):
        """The type rule must not make the classifier trigger-happy: a garbled
        or truncated response is worth fetching again, even though
        ``JSONDecodeError`` is a ``ValueError``."""
        with pytest.raises(TransientError):
            classify_and_raise(exc, context="fetching feed")

    def test_malformed_llm_output_keeps_its_retries(self):
        """pydantic's ValidationError is a ValueError, but a fresh LLM sample
        may validate, so it is not a programming error."""
        from pydantic import BaseModel, ValidationError

        class Summary(BaseModel):
            title: str

        with pytest.raises(ValidationError) as excinfo:
            Summary.model_validate({"headline": "wrong key"})
        with pytest.raises(TransientError):
            classify_and_raise(excinfo.value, context="summarizing")

    def test_unknown_non_programming_errors_still_default_to_transient(self):
        class SomethingOdd(Exception):
            pass

        with pytest.raises(TransientError):
            classify_and_raise(SomethingOdd("never seen before"))


class TestTodaysThreeYouTubeErrors:
    """The three real failures from the 2026-09-21 YouTube import, verbatim."""

    def test_dalston_html_400_is_fatal(self):
        exc = _HttpError(
            '[400] {"error":{"code":"invalid_request","message":"Unsupported content type: text/html; '
            'charset=utf-8. Expected audio file (MP3, WAV, FLAC, OGG, M4A, etc.)"}}',
            400,
        )
        with pytest.raises(FatalError):
            classify_and_raise(exc, context="transcribing Deep Dive into LLMs like ChatGPT")

    def test_ytdlp_403_is_fatal_it_needs_a_ytdlp_bump_not_a_retry(self):
        exc = Exception("YouTube download failed: ERROR: unable to download video data: HTTP Error 403: Forbidden")
        with pytest.raises(FatalError):
            classify_and_raise(exc, context="downloading audio")

    def test_storage_root_violation_is_fatal_end_to_end(self, tmp_path):
        """Through the real guard, not a hand-built exception."""
        from pathlib import Path

        from thestill.utils.exceptions import StoragePathError
        from thestill.utils.path_manager import PathManager

        manager = PathManager(str(tmp_path / "data"))
        outside = Path("/var/folders/3l/T/thestill_download_x/Andrej_Karpathy_Deep_Dive_7xTGNNLPyMI.m4a")
        with pytest.raises(StoragePathError) as excinfo:
            manager.to_relative(outside)
        assert isinstance(excinfo.value, ValueError)  # what callers and docs expected before

        with pytest.raises(FatalError) as classified:
            classify_and_raise(excinfo.value, context="downloading audio for Deep Dive")
        assert classified.value is excinfo.value  # already classified: passed through untouched
