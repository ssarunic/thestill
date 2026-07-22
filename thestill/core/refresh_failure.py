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

"""Spec #60 — refresh failure classification and policy.

One vocabulary for what went wrong during a feed refresh and what to do
about it, shared by the fetch layer (:mod:`media_source`), the feed manager,
the queued handler, the worker, and both repository backends. Pure — no I/O,
no DB, no requests calls beyond ``isinstance`` checks — so every function
here is unit-testable in isolation (mirrors :mod:`error_classifier`).

The module deliberately separates three concerns the 2026-07-15 incident
conflated:

- **Classification** (:func:`classify_fetch_exception`,
  :func:`classify_http_status`): what KIND of failure was this?
- **Queue attribution** (:func:`error_class_for_failure`): how should the
  task queue treat it (spec #49 ``'fatal' | 'infra' | 'item'``)? Explicit —
  never re-derived from a message string downstream.
- **Feed policy** (:func:`decide_refresh_action`): what happens to the
  podcast row? Only a definitive "this feed is gone" signal may quarantine;
  connectivity NEVER parks. This is the ONE place the parking bias lives.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, List, Optional, Tuple

import requests

from ..utils.datetime_utils import ensure_utc, now_utc
from ..utils.url_guard import UnsafeDestinationError, UnsafeURLError, URLResolutionError

try:  # yt-dlp is a hard dependency, but keep the pure module importable without it
    from yt_dlp.utils import YoutubeDLError as _YoutubeDLError
except ImportError:  # pragma: no cover
    _YoutubeDLError = ()  # isinstance(x, ()) is always False

# Network-shaped substrings inside a yt-dlp error message — yt-dlp wraps the
# underlying socket/urllib failure into DownloadError text, so structural
# isinstance checks can't see it. Kept conservative (same spirit as
# error_classifier.INFRA_PATTERNS): unmatched yt-dlp errors classify
# REMOTE_TRANSIENT (keep-trying), never INTERNAL/fatal.
_YTDLP_NETWORK_SIGNATURES = (
    "getaddrinfo",
    "nodename nor servname",
    "name or service not known",
    "temporary failure in name resolution",
    "errno 8",
    "unable to download webpage",
    "connection refused",
    "connection reset",
    "timed out",
    "network is unreachable",
)

if TYPE_CHECKING:
    from ..models.podcast import Episode, Podcast


class RefreshFailureKind(str, Enum):
    """What kind of failure a refresh attempt hit (spec #60 taxonomy)."""

    CONNECTIVITY = "connectivity"  # host never reached: DNS, conn refused/timeout
    REMOTE_TRANSIENT = "remote_transient"  # host reached, unhappy: 429/5xx, read timeout
    REMOTE_GONE = "remote_gone"  # host says feed is not there: 410 (definitive), 404 (probable)
    AUTHENTICATION = "authentication"  # 401/403 — user action required
    INVALID_CONTENT = "invalid_content"  # reachable but bad body (bozo/malformed)
    SECURITY_POLICY = "security_policy"  # SSRF-guard refusal — never retried
    INTERNAL = "internal"  # OUR bug — loud, never condemns the feed


@dataclass(frozen=True)
class RefreshFailure:
    """Structured outcome of a failed refresh attempt (replaces ``had_error``)."""

    kind: RefreshFailureKind
    http_status: Optional[int] = None  # real status, never coerced to 0
    retry_after: Optional[datetime] = None  # parsed from Retry-After when present
    exception: str = ""  # original repr/str, for logs + last_refresh_error
    is_internal: bool = False  # True → raise loudly, never condemn the feed


@dataclass(frozen=True)
class RefreshAttemptResult:
    """Structured result of ``_refresh_single_podcast`` (replaces the 8-tuple).

    ``failure is None`` means success. All other fields mirror the old
    positional tuple's semantics (see the feed manager's docstring).
    """

    podcast: "Podcast"
    new_episodes: List["Episode"] = field(default_factory=list)
    conditional_hit: bool = False
    headers_rotated: bool = False
    image_rows: List[Tuple[str, str, Optional[str]]] = field(default_factory=list)
    audio_rows: List[Tuple[str, str, str, Optional[str]]] = field(default_factory=list)
    # Spec #62 — (podcast_id, external_id, AlternateEnclosure) observation
    # rows for the whole feed window (new + already-tracked episodes).
    alt_enclosure_rows: List[Tuple[str, str, Any]] = field(default_factory=list)
    source: Optional[Any] = None
    failure: Optional[RefreshFailure] = None

    @property
    def had_error(self) -> bool:
        return self.failure is not None


class RefreshAction(str, Enum):
    """What the feed-policy layer does to the podcast row."""

    BACKOFF = "backoff"  # lengthen interval / honor Retry-After; never park
    QUARANTINE = "quarantine"  # next_refresh_at = NULL + refresh_disabled_reason
    IGNORE = "ignore"  # internal bug — stamp visibility fields only


@dataclass(frozen=True)
class RefreshDecision:
    action: RefreshAction
    disabled_reason: Optional[str] = None  # feed_gone | blocked_unsafe | auth_required | invalid_content


@dataclass(frozen=True)
class RefreshPolicySettings:
    """AIMD clamps passed INTO the repository (never read from global config
    inside repo code — the caller owns configuration)."""

    min_interval_seconds: int
    max_interval_seconds: int
    default_interval_seconds: int

    @classmethod
    def from_config(cls) -> "RefreshPolicySettings":
        """Build from the process env config — the one place the two writers
        (handler per-attempt write, worker INTERNAL fallback) agree on."""
        from ..utils.config import (
            get_default_refresh_interval_seconds,
            get_refresh_max_interval_seconds,
            get_refresh_min_interval_seconds,
        )

        return cls(
            min_interval_seconds=get_refresh_min_interval_seconds(),
            max_interval_seconds=get_refresh_max_interval_seconds(),
            default_interval_seconds=get_default_refresh_interval_seconds(),
        )


def resolve_streak_state(
    prior_kind: Optional[str],
    prior_consecutive: int,
    streak_started_at: Optional[datetime],
    new_kind: RefreshFailureKind,
    now: datetime,
) -> Tuple[datetime, int]:
    """Shared streak bookkeeping for ``record_refresh_failure`` (both repos).

    A change in failure kind restarts the streak — a feed that was 404ing
    and is now unreachable is a NEW problem, not more of the old one.
    Returns ``(streak_anchor, consecutive_before)``: the wall-clock streak
    start to persist/judge against and the pre-increment failure count.
    """
    kind_changed = prior_kind is not None and prior_kind != new_kind.value
    if kind_changed or streak_started_at is None:
        return now, (0 if kind_changed else prior_consecutive)
    return streak_started_at, prior_consecutive


def compute_backoff_interval(current_interval_seconds: int, settings: RefreshPolicySettings) -> int:
    """Failure backoff: lengthen ×1.5, clamped to the AIMD bounds (both repos)."""
    return max(
        settings.min_interval_seconds,
        min(settings.max_interval_seconds, int(current_interval_seconds * 1.5)),
    )


def parse_retry_after(raw: Optional[str], now: datetime) -> Optional[datetime]:
    """Parse an HTTP ``Retry-After`` header (delta-seconds or HTTP-date)."""
    if not raw:
        return None
    try:
        return now + timedelta(seconds=int(raw))
    except ValueError:
        try:
            return ensure_utc(parsedate_to_datetime(raw))
        except (TypeError, ValueError):
            return None


def classify_http_status(status: int) -> RefreshFailureKind:
    """Map a real HTTP status to a failure kind. Policy (404 vs 410) is
    applied later — classification only records what the server said."""
    if status in (404, 410):
        return RefreshFailureKind.REMOTE_GONE
    if status in (401, 403):
        return RefreshFailureKind.AUTHENTICATION
    if status == 429 or 500 <= status < 600:
        return RefreshFailureKind.REMOTE_TRANSIENT
    # Any other unexpected non-success status: the host answered with
    # something we can't use — lean "keep trying" (spec bias), not "gone".
    return RefreshFailureKind.REMOTE_TRANSIENT


def _status_from_retry_error(exc: requests.exceptions.RetryError) -> Optional[int]:
    """Best-effort unwrap of the final status from an exhausted urllib3 retry.

    Defence-in-depth only: the RSS session sets ``raise_on_status=False``
    (spec #60 transport fix) so exhausted 5xx normally surfaces as a plain
    ``HTTPError`` with a response, not a ``RetryError``.
    """
    reason = getattr(getattr(exc, "args", [None])[0] if exc.args else None, "reason", None)
    # urllib3 ResponseError message looks like "too many 503 error responses".
    match = re.search(r"too many (\d{3}) error responses", str(reason)) if reason is not None else None
    return int(match.group(1)) if match else None


def classify_fetch_exception(exc: BaseException) -> RefreshFailure:
    """Classify an exception from the fetch/parse/refresh path structurally.

    Ordering matters: security refusals are checked before connectivity so a
    forbidden destination can never fall into a retry bucket, and the
    catch-all default is INTERNAL (our bug, loud) — never a network guess.
    """
    # 1. SSRF guard — most specific first, generic base last.
    if isinstance(exc, UnsafeDestinationError):
        return RefreshFailure(RefreshFailureKind.SECURITY_POLICY, exception=repr(exc))
    if isinstance(exc, URLResolutionError):
        return RefreshFailure(RefreshFailureKind.CONNECTIVITY, exception=repr(exc))
    if isinstance(exc, UnsafeURLError):
        # Unsplit/unknown guard refusal (e.g. TooManyRedirects) — conservative
        # per spec: NEVER connectivity.
        return RefreshFailure(RefreshFailureKind.SECURITY_POLICY, exception=repr(exc))

    # 2. HTTPError carries the real response — read it directly.
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        status = exc.response.status_code
        retry_after = parse_retry_after(exc.response.headers.get("Retry-After"), now_utc())
        return RefreshFailure(classify_http_status(status), status, retry_after, repr(exc))

    # 3. Exhausted-retry wrapper (should be rare post-raise_on_status=False).
    if isinstance(exc, requests.exceptions.RetryError):
        status = _status_from_retry_error(exc)
        if status is not None:
            return RefreshFailure(classify_http_status(status), status, None, repr(exc))
        return RefreshFailure(RefreshFailureKind.CONNECTIVITY, exception=repr(exc))

    # 4. Host never reached.
    if isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.ConnectTimeout)):
        return RefreshFailure(RefreshFailureKind.CONNECTIVITY, exception=repr(exc))

    # 5. Host reached but slow.
    if isinstance(exc, requests.exceptions.ReadTimeout):
        return RefreshFailure(RefreshFailureKind.REMOTE_TRANSIENT, exception=repr(exc))

    # 6. Remaining requests-level failures (protocol errors, chunked-decode…)
    #    — the transport misbehaved, keep trying.
    if isinstance(exc, requests.RequestException):
        return RefreshFailure(RefreshFailureKind.CONNECTIVITY, exception=repr(exc))

    # 7. yt-dlp failures (spec #60 review finding): a YouTube-source refresh
    #    raises yt-dlp's own types, not requests'. Network-shaped messages
    #    are connectivity; everything else yt-dlp raises (extractor changes,
    #    "video unavailable", rate limits) is the remote side misbehaving —
    #    REMOTE_TRANSIENT, never INTERNAL/fatal, so it backs off and stays
    #    scheduled instead of DLQ-ing the feed task without retry.
    if isinstance(exc, _YoutubeDLError):
        msg = str(exc).lower()
        if any(sig in msg for sig in _YTDLP_NETWORK_SIGNATURES):
            return RefreshFailure(RefreshFailureKind.CONNECTIVITY, exception=repr(exc))
        return RefreshFailure(RefreshFailureKind.REMOTE_TRANSIENT, exception=repr(exc))

    # 8. Everything else is OUR bug — loud, never condemns the feed.
    return RefreshFailure(RefreshFailureKind.INTERNAL, exception=repr(exc), is_internal=True)


def error_class_for_failure(failure: RefreshFailure) -> str:
    """Queue attribution (spec #49 seam) — explicit, never message-matched.

    Only LOCAL environmental failures are ``infra``: a 503 from one feed
    host is that host's problem, not a shared-dependency outage — promoting
    remote 5xx to infra would let a few unrelated broken feeds open the
    fleet-wide refresh circuit breaker (spec #60 design review).
    """
    if failure.kind is RefreshFailureKind.CONNECTIVITY:
        return "infra"
    if failure.kind is RefreshFailureKind.REMOTE_GONE and failure.http_status == 410:
        return "fatal"
    if failure.kind in (RefreshFailureKind.SECURITY_POLICY, RefreshFailureKind.INTERNAL):
        return "fatal"
    # remote_transient (incl. 429/5xx), remote_gone(404), authentication,
    # invalid_content: per-feed problems with the normal retry budget.
    return "item"


def decide_refresh_action(
    kind: RefreshFailureKind,
    http_status: Optional[int],
    *,
    current_interval_seconds: int,
    streak_started_at: Optional[datetime],
    now: datetime,
    settings: RefreshPolicySettings,
) -> RefreshDecision:
    """Pure feed policy — the ONE place the parking bias lives (spec #60).

    Decisive kinds quarantine on first sight (410, SSRF refusal, auth).
    Horizon-gated kinds (404 / invalid_content) quarantine only when the
    feed has already backed off to the AIMD max AND the failure streak has
    persisted for at least one full max interval of wall-clock time — i.e.
    "quietly dead", never "briefly missing during a deploy". Connectivity
    and remote-transient failures NEVER park.
    """
    if kind is RefreshFailureKind.INTERNAL:
        return RefreshDecision(RefreshAction.IGNORE)
    if kind is RefreshFailureKind.SECURITY_POLICY:
        return RefreshDecision(RefreshAction.QUARANTINE, "blocked_unsafe")
    if kind is RefreshFailureKind.AUTHENTICATION:
        return RefreshDecision(RefreshAction.QUARANTINE, "auth_required")
    if kind is RefreshFailureKind.REMOTE_GONE and http_status == 410:
        return RefreshDecision(RefreshAction.QUARANTINE, "feed_gone")
    if kind in (RefreshFailureKind.REMOTE_GONE, RefreshFailureKind.INVALID_CONTENT):
        horizon = timedelta(seconds=settings.max_interval_seconds)
        streak = ensure_utc(streak_started_at)
        persisted_long_enough = streak is not None and (now - streak) >= horizon
        if current_interval_seconds >= settings.max_interval_seconds and persisted_long_enough:
            reason = "feed_gone" if kind is RefreshFailureKind.REMOTE_GONE else "invalid_content"
            return RefreshDecision(RefreshAction.QUARANTINE, reason)
        return RefreshDecision(RefreshAction.BACKOFF)
    # CONNECTIVITY / REMOTE_TRANSIENT — never park.
    return RefreshDecision(RefreshAction.BACKOFF)
