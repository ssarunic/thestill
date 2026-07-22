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

"""Spec #60 — refresh failure classification + policy units.

Per #42's consistent-mock warning these tests inject TYPED failures — real
exception instances per kind — and assert the classified kind, preserved
HTTP status, queue attribution, and the pure policy decision.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
import requests

from thestill.core.refresh_failure import (
    RefreshAction,
    RefreshFailure,
    RefreshFailureKind,
    RefreshPolicySettings,
    classify_fetch_exception,
    classify_http_status,
    decide_refresh_action,
    error_class_for_failure,
    parse_retry_after,
)
from thestill.utils.url_guard import UnsafeDestinationError, UnsafeURLError, URLResolutionError

NOW = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)
SETTINGS = RefreshPolicySettings(min_interval_seconds=900, max_interval_seconds=86400, default_interval_seconds=3600)


def _http_error(status: int, headers: dict | None = None) -> requests.HTTPError:
    response = Mock(spec=requests.Response)
    response.status_code = status
    response.headers = headers or {}
    return requests.HTTPError(f"{status} error", response=response)


# ---------------------------------------------------------------------------
# classify_fetch_exception — one case per exception in the spec's test list
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "exc, expected_kind, expected_status",
    [
        (requests.exceptions.ConnectionError("refused"), RefreshFailureKind.CONNECTIVITY, None),
        (requests.exceptions.ConnectTimeout("slow connect"), RefreshFailureKind.CONNECTIVITY, None),
        (URLResolutionError("DNS lookup failed for 'x': gaierror"), RefreshFailureKind.CONNECTIVITY, None),
        (UnsafeDestinationError("resolves to non-public address"), RefreshFailureKind.SECURITY_POLICY, None),
        (UnsafeURLError("unsplit guard refusal"), RefreshFailureKind.SECURITY_POLICY, None),
        (_http_error(401), RefreshFailureKind.AUTHENTICATION, 401),
        (_http_error(403), RefreshFailureKind.AUTHENTICATION, 403),
        (_http_error(404), RefreshFailureKind.REMOTE_GONE, 404),
        (_http_error(410), RefreshFailureKind.REMOTE_GONE, 410),
        (_http_error(429), RefreshFailureKind.REMOTE_TRANSIENT, 429),
        (_http_error(503), RefreshFailureKind.REMOTE_TRANSIENT, 503),
        (requests.exceptions.ReadTimeout("read timed out"), RefreshFailureKind.REMOTE_TRANSIENT, None),
        (requests.exceptions.ChunkedEncodingError("broken chunk"), RefreshFailureKind.CONNECTIVITY, None),
        (KeyError("our bug"), RefreshFailureKind.INTERNAL, None),
        (TypeError("our other bug"), RefreshFailureKind.INTERNAL, None),
    ],
)
def test_classify_fetch_exception(exc, expected_kind, expected_status):
    failure = classify_fetch_exception(exc)
    assert failure.kind is expected_kind
    assert failure.http_status == expected_status
    assert failure.exception  # original repr preserved


def test_unknown_exception_is_internal_and_loud():
    failure = classify_fetch_exception(RuntimeError("bug"))
    assert failure.kind is RefreshFailureKind.INTERNAL
    assert failure.is_internal is True
    assert error_class_for_failure(failure) == "fatal"


def test_unsafe_destination_never_connectivity_never_retryable():
    """Security regression (spec #60 review finding #1): an SSRF refusal must
    never classify as connectivity — that would retry a security violation."""
    for exc in (
        UnsafeDestinationError("URL targets non-public address '169.254.169.254'"),
        UnsafeURLError("disallowed scheme 'file'"),
    ):
        failure = classify_fetch_exception(exc)
        assert failure.kind is RefreshFailureKind.SECURITY_POLICY
        assert error_class_for_failure(failure) == "fatal"
        decision = decide_refresh_action(
            failure.kind, None, current_interval_seconds=900, streak_started_at=None, now=NOW, settings=SETTINGS
        )
        assert decision.action is RefreshAction.QUARANTINE
        assert decision.disabled_reason == "blocked_unsafe"


def test_dns_resolution_error_is_connectivity():
    """The other half of the UnsafeURLError split: a DNS failure is
    environmental, retryable, and attributed infra."""
    failure = classify_fetch_exception(URLResolutionError("DNS lookup failed for 'example.com'"))
    assert failure.kind is RefreshFailureKind.CONNECTIVITY
    assert error_class_for_failure(failure) == "infra"


def test_retry_error_unwraps_status_hint():
    from urllib3.exceptions import MaxRetryError, ResponseError

    inner = MaxRetryError(pool=None, url="/feed", reason=ResponseError("too many 503 error responses"))
    failure = classify_fetch_exception(requests.exceptions.RetryError(inner))
    assert failure.kind is RefreshFailureKind.REMOTE_TRANSIENT
    assert failure.http_status == 503


def test_retry_error_without_status_falls_back_to_connectivity():
    failure = classify_fetch_exception(requests.exceptions.RetryError("opaque"))
    assert failure.kind is RefreshFailureKind.CONNECTIVITY


def test_http_error_parses_retry_after_seconds():
    failure = classify_fetch_exception(_http_error(429, headers={"Retry-After": "120"}))
    assert failure.kind is RefreshFailureKind.REMOTE_TRANSIENT
    assert failure.retry_after is not None


def test_ytdlp_network_error_is_connectivity():
    """Spec #60 review finding: yt-dlp wraps socket failures in its own
    exception types — a YouTube outage must classify connectivity, not
    INTERNAL (which would DLQ the feed task without retry)."""
    from yt_dlp.utils import DownloadError

    exc = DownloadError("Unable to download webpage: <urlopen error [Errno 8] nodename nor servname provided>")
    failure = classify_fetch_exception(exc)
    assert failure.kind is RefreshFailureKind.CONNECTIVITY
    assert error_class_for_failure(failure) == "infra"


def test_ytdlp_extractor_error_is_remote_transient_not_internal():
    from yt_dlp.utils import DownloadError, ExtractorError

    for exc in (DownloadError("ERROR: Video unavailable"), ExtractorError("Unsupported extractor change")):
        failure = classify_fetch_exception(exc)
        assert failure.kind is RefreshFailureKind.REMOTE_TRANSIENT
        assert failure.is_internal is False
        assert error_class_for_failure(failure) == "item"  # backs off, never fatal-DLQ


# ---------------------------------------------------------------------------
# classify_http_status
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "status, expected",
    [
        (404, RefreshFailureKind.REMOTE_GONE),
        (410, RefreshFailureKind.REMOTE_GONE),
        (401, RefreshFailureKind.AUTHENTICATION),
        (403, RefreshFailureKind.AUTHENTICATION),
        (429, RefreshFailureKind.REMOTE_TRANSIENT),
        (500, RefreshFailureKind.REMOTE_TRANSIENT),
        (503, RefreshFailureKind.REMOTE_TRANSIENT),
        (418, RefreshFailureKind.REMOTE_TRANSIENT),  # weird status → keep trying
    ],
)
def test_classify_http_status(status, expected):
    assert classify_http_status(status) is expected


# ---------------------------------------------------------------------------
# error_class_for_failure — queue attribution (C+ table: remote 5xx is NOT
# infra; only local connectivity is)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "failure, expected",
    [
        (RefreshFailure(RefreshFailureKind.CONNECTIVITY), "infra"),
        (RefreshFailure(RefreshFailureKind.REMOTE_TRANSIENT, http_status=503), "item"),
        (RefreshFailure(RefreshFailureKind.REMOTE_TRANSIENT, http_status=429), "item"),
        (RefreshFailure(RefreshFailureKind.REMOTE_GONE, http_status=404), "item"),
        (RefreshFailure(RefreshFailureKind.REMOTE_GONE, http_status=410), "fatal"),
        (RefreshFailure(RefreshFailureKind.AUTHENTICATION, http_status=401), "item"),
        (RefreshFailure(RefreshFailureKind.INVALID_CONTENT), "item"),
        (RefreshFailure(RefreshFailureKind.SECURITY_POLICY), "fatal"),
        (RefreshFailure(RefreshFailureKind.INTERNAL, is_internal=True), "fatal"),
    ],
)
def test_error_class_for_failure(failure, expected):
    assert error_class_for_failure(failure) == expected


def test_remote_5xx_is_not_infra():
    """C+ correction: a 503 from ONE feed host must not open the fleet-wide
    refresh breaker — a few unrelated broken feeds are not a shared outage."""
    assert error_class_for_failure(RefreshFailure(RefreshFailureKind.REMOTE_TRANSIENT, http_status=503)) == "item"


# ---------------------------------------------------------------------------
# decide_refresh_action — the pure policy table
# ---------------------------------------------------------------------------
def _decide(kind, status=None, *, interval=3600, streak=None, now=NOW):
    return decide_refresh_action(
        kind, status, current_interval_seconds=interval, streak_started_at=streak, now=now, settings=SETTINGS
    )


def test_connectivity_and_remote_transient_never_quarantine():
    for kind in (RefreshFailureKind.CONNECTIVITY, RefreshFailureKind.REMOTE_TRANSIENT):
        # Even at max interval with an ancient streak — never park.
        decision = _decide(kind, interval=86400, streak=NOW - timedelta(days=365))
        assert decision.action is RefreshAction.BACKOFF
        assert decision.disabled_reason is None


def test_410_quarantines_immediately():
    decision = _decide(RefreshFailureKind.REMOTE_GONE, 410, interval=900, streak=None)
    assert decision.action is RefreshAction.QUARANTINE
    assert decision.disabled_reason == "feed_gone"


def test_404_needs_full_horizon():
    # Fresh 404 at low interval: backoff.
    assert _decide(RefreshFailureKind.REMOTE_GONE, 404).action is RefreshAction.BACKOFF
    # At max interval but streak too young: still backoff.
    assert (
        _decide(RefreshFailureKind.REMOTE_GONE, 404, interval=86400, streak=NOW - timedelta(hours=1)).action
        is RefreshAction.BACKOFF
    )
    # Old streak but interval below max: still backoff.
    assert (
        _decide(RefreshFailureKind.REMOTE_GONE, 404, interval=3600, streak=NOW - timedelta(days=7)).action
        is RefreshAction.BACKOFF
    )
    # Both conditions met: quarantine.
    decision = _decide(RefreshFailureKind.REMOTE_GONE, 404, interval=86400, streak=NOW - timedelta(days=2))
    assert decision.action is RefreshAction.QUARANTINE
    assert decision.disabled_reason == "feed_gone"


def test_invalid_content_mirrors_404_horizon():
    assert _decide(RefreshFailureKind.INVALID_CONTENT).action is RefreshAction.BACKOFF
    decision = _decide(RefreshFailureKind.INVALID_CONTENT, interval=86400, streak=NOW - timedelta(days=2))
    assert decision.action is RefreshAction.QUARANTINE
    assert decision.disabled_reason == "invalid_content"


def test_authentication_quarantines_with_actionable_reason():
    decision = _decide(RefreshFailureKind.AUTHENTICATION, 401)
    assert decision.action is RefreshAction.QUARANTINE
    assert decision.disabled_reason == "auth_required"


def test_internal_is_ignore():
    decision = _decide(RefreshFailureKind.INTERNAL)
    assert decision.action is RefreshAction.IGNORE
    assert decision.disabled_reason is None


# ---------------------------------------------------------------------------
# parse_retry_after
# ---------------------------------------------------------------------------
def test_parse_retry_after_seconds_and_http_date():
    assert parse_retry_after("120", NOW) == NOW + timedelta(seconds=120)
    parsed = parse_retry_after("Wed, 22 Jul 2026 14:00:00 GMT", NOW)
    assert parsed == datetime(2026, 7, 22, 14, 0, 0, tzinfo=timezone.utc)
    assert parse_retry_after("garbage", NOW) is None
    assert parse_retry_after(None, NOW) is None
    assert parse_retry_after("", NOW) is None
