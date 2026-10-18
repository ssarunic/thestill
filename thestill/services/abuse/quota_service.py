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

"""Spec #104 §2.2 — per-user quotas on the actions that cost money.

Check and record are one atomic step: ``reserve`` counts the user's
reserved and committed ledger rows under a lock and inserts a reservation,
or records a ``quota_exceeded`` row and raises ``QuotaExceededError``. The
action then commits the reservation once money is spent, or leaves the
``with`` block without committing to release it::

    with reserve_quota(quota, user_id, "translations_per_day", subject_id=episode.id) as reservation:
        summary = translator.translate(...)   # the paid call
        reservation.commit()

Follows don't reserve: their quotas (the ``follow`` lock group) are checked
inside ``follow_with_gate``'s transaction through ``follow_limits``.

Quotas apply only in multi-user mode; admins are exempt (their usage is
still recorded, so the console shows it).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Callable, Dict, Optional

from structlog import get_logger

from ...models.user import User
from ...repositories.podcast_follower_repository import FollowFacts
from ...repositories.usage_event_repository import FOLLOW_LOCK_GROUP, UsageEventRepository, UsageRecord
from ...utils.datetime_utils import now_utc
from .errors import QuotaExceededError
from .quotas import DAY, QUOTAS

if TYPE_CHECKING:
    from ...repositories.user_repository import UserRepository
    from ...utils.config import Config
    from .settings_service import SettingsService

logger = get_logger(__name__)


def retry_after_seconds(frees_at: Optional[datetime], window: timedelta, now: datetime) -> int:
    """Seconds until ``frees_at`` leaves the window, at least 1 (the full
    window when unknown)."""
    if frees_at is None:
        return int(window.total_seconds())
    return max(1, int((frees_at + window - now).total_seconds()))


class Reservation:
    """One reserved ledger row. Leaving the ``with`` block without
    ``commit()`` — an exception, a pause — releases it."""

    def __init__(
        self,
        usage: UsageEventRepository,
        event_id: str,
        *,
        user_id: str,
        kind: str,
        clock: Callable[[], datetime],
    ):
        self._usage = usage
        self._event_id = event_id
        self._user_id = user_id
        self._kind = kind
        self._clock = clock
        self._settled = False

    def commit(self, subject_id: Optional[str] = None) -> None:
        """Money was spent: count it, even if what follows fails."""
        if self._settle("committed", subject_id):
            return
        # Housekeeping expired the reservation while the paid call ran
        # longer than QUOTA_RESERVATION_TTL_SECONDS: the spend still counts.
        logger.warning("quota_commit_after_expiry", user_id=self._user_id, kind=self._kind)
        self._usage.record(self._user_id, [UsageRecord(self._kind, subject_id)], now=self._clock())

    def _settle(self, state: str, subject_id: Optional[str]) -> bool:
        """Settle once. True when this call settled the reserved row (or a
        previous call already handled it)."""
        if self._settled:
            return True
        self._settled = True
        return self._usage.settle(self._event_id, state, now=self._clock(), subject_id=subject_id)

    def __enter__(self) -> "Reservation":
        return self

    def __exit__(self, *_exc) -> bool:
        self._settle("released", None)
        return False


class _NoReservation(Reservation):
    """Nothing to settle: quotas don't apply (single-user, no user)."""

    def __init__(self) -> None:
        pass

    def commit(self, subject_id: Optional[str] = None) -> None:
        pass

    def _settle(self, state: str, subject_id: Optional[str]) -> bool:
        return True


NO_RESERVATION = _NoReservation()


@dataclass(frozen=True)
class FollowLimits:
    """One user's follow quotas, resolved outside the follow transaction."""

    total: int
    per_day: int
    introduced_per_day: int
    enforce: bool
    now: datetime

    @property
    def window_start(self) -> datetime:
        return self.now - DAY

    def breach(self, facts: FollowFacts, *, introducing: bool) -> Optional[QuotaExceededError]:
        """The first quota this follow would exceed, or None. The tight
        limit on feeds a user brings in is checked first; a follow of a
        feed others follow never counts against it.

        ``Retry-After`` uses the oldest row in the window, so after an
        override lowers a limit below current use it is a lower bound."""
        checks = [
            ("follows_per_day", self.per_day, facts.follows_in_window, facts.oldest_follow_at),
            ("follows_total", self.total, facts.user_follows_total, None),
        ]
        if introducing:
            checks.insert(
                0,
                (
                    "feeds_introduced_per_day",
                    self.introduced_per_day,
                    facts.introduced_in_window,
                    facts.oldest_introduced_at,
                ),
            )
        for quota, limit, used, oldest in checks:
            if limit and used >= limit:
                window = QUOTAS[quota].window
                retry = None if window is None else retry_after_seconds(oldest, window, self.now)
                return QuotaExceededError(quota, limit, retry)
        return None


class QuotaService:
    def __init__(
        self,
        usage: UsageEventRepository,
        users: "UserRepository",
        settings: "SettingsService",
        config: "Config",
        *,
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        self._usage = usage
        self._users = users
        self._settings = settings
        self._config = config
        self._clock = clock

    @property
    def enforced(self) -> bool:
        return bool(self._config.quota_enforce)

    def limit_for(self, user_id: str, quota: str) -> int:
        """The user's limit: a ``quota_overrides`` entry, else the env
        default. 0 means unlimited."""
        override = self._settings.get("quota_overrides").get(user_id, {}).get(quota)
        return override if override is not None else int(getattr(self._config, QUOTAS[quota].config_attr))

    def _applies_to(self, user_id: Optional[str]) -> Optional[User]:
        """The user quotas apply to, or None (single-user, unknown user)."""
        if not self._config.multi_user or user_id is None:
            return None
        return self._users.get_by_id(user_id)

    def reserve(self, user_id: Optional[str], quota: str, *, subject_id: Optional[str] = None) -> Reservation:
        """Atomically check ``quota`` and reserve one use of it. Raises
        ``QuotaExceededError`` (after recording the refusal) at the limit."""
        spec = QUOTAS[quota]
        if spec.lock_group == FOLLOW_LOCK_GROUP:
            raise ValueError(f"{quota} is checked inside follow_with_gate, not reserved")
        user = self._applies_to(user_id)
        if user is None:
            return NO_RESERVATION
        limit = 0 if user.is_admin else self.limit_for(user.id, quota)
        now = self._clock()
        result = self._usage.reserve(
            user.id,
            spec.kind,
            quota=quota,
            limit=limit if self.enforced else 0,
            since=now - spec.window,
            lock_group=spec.lock_group,
            subject_id=subject_id,
            now=now,
        )
        if result.event_id is None:
            logger.info("quota_exceeded", user_id=user.id, quota=quota, limit=limit)
            raise QuotaExceededError(quota, limit, retry_after_seconds(result.frees_at, spec.window, now))
        if limit and result.count >= limit:
            logger.warning("quota_would_refuse", user_id=user.id, quota=quota, limit=limit, count=result.count)
        return Reservation(self._usage, result.event_id, user_id=user.id, kind=spec.kind, clock=self._clock)

    def follow_limits(self, user_id: str) -> Optional[FollowLimits]:
        """Limits for ``follow_with_gate``, or None when quotas don't apply
        (then no ledger rows are written either)."""
        user = self._applies_to(user_id)
        if user is None:
            return None
        exempt = user.is_admin
        return FollowLimits(
            total=0 if exempt else self.limit_for(user.id, "follows_total"),
            per_day=0 if exempt else self.limit_for(user.id, "follows_per_day"),
            introduced_per_day=0 if exempt else self.limit_for(user.id, "feeds_introduced_per_day"),
            enforce=self.enforced,
            now=self._clock(),
        )

    def check_follow(self, user_id: str, *, follows_now: int, introducing: bool) -> None:
        """Read-only precheck for an add path, before it writes a podcast
        row: raise the refusal the follow itself would raise. Records
        nothing — the follow's own check is authoritative."""
        limits = self.follow_limits(user_id)
        if limits is None or not limits.enforce:
            return
        follows, oldest_follow = self._usage.window(user_id, "follow", limits.window_start)
        introduced, oldest_introduced = self._usage.window(user_id, "feed_introduced", limits.window_start)
        facts = FollowFacts(
            podcast_followers=0 if introducing else 1,
            user_follows_total=follows_now,
            follows_in_window=follows,
            introduced_in_window=introduced,
            oldest_follow_at=oldest_follow,
            oldest_introduced_at=oldest_introduced,
        )
        breach = limits.breach(facts, introducing=introducing)
        if breach is not None:
            raise breach

    def usage_last_day(self, user_id: str) -> Dict[str, int]:
        """Counted ledger rows per kind in the last 24h (CLI, console)."""
        return self._usage.counts_by_kind(user_id, self._clock() - DAY)


def reserve_quota(
    quota: Optional[QuotaService], user_id: Optional[str], name: str, *, subject_id: Optional[str] = None
) -> Reservation:
    """``quota.reserve(...)``, or a no-op reservation for a service built
    without quotas (hand-built fixtures, the CLI)."""
    return quota.reserve(user_id, name, subject_id=subject_id) if quota is not None else NO_RESERVATION
