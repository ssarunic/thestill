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

"""Spec #104 §1.1, §1.4, §1.5 — the operator's actions on one account.

Shared by ``thestill user ...`` / ``thestill admin ...`` and the admin API
(Phase 4), so both enforce the same rules and write the same audit entries.

Every multi-step action is ordered so a crash part-way leaves a state the
same command can be re-run from: a ban sets the status first (the user's
next request is refused at once) and derives what to cancel from the
durable ``suspended_follows`` rows; a reinstate sets the status last.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Protocol, Tuple

from structlog import get_logger

from ...models.user import User, UserStatus
from ...repositories.audit_log_repository import AuditEntry
from ...repositories.user_directory_repository import UserSort, UserSummary
from ...repositories.user_repository import LastActiveAdminError
from ...utils.datetime_utils import now_utc
from .actor import Actor
from .errors import AdminActionError

if TYPE_CHECKING:
    from ...repositories.mcp_token_repository import McpTokenRepository
    from ...repositories.podcast_follower_repository import PodcastFollowerRepository
    from ...repositories.podcast_repository import PodcastRepository
    from ...repositories.suspended_follow_repository import SuspendedFollowRepository
    from ...repositories.user_directory_repository import UserDirectoryRepository
    from ...repositories.user_repository import UserRepository
    from .audit_service import AuditLog
    from .quota_service import QuotaService
    from .settings_service import SettingsService

logger = get_logger(__name__)


class QueuedTaskCanceller(Protocol):
    """The slice of either queue manager this service needs."""

    def cancel_queued_for_podcasts(self, podcast_ids: List[str]) -> int: ...


def _last_admin() -> AdminActionError:
    return AdminActionError("last_admin", "This is the last active admin. Grant admin to someone else first.")


def revocation_instant(now: datetime) -> datetime:
    """``tokens_valid_after`` for a revocation at ``now``.

    A JWT's ``iat`` has whole-second precision, so a token issued earlier
    in the same second carries the same ``iat`` as one issued just after.
    Rounding up to the next whole second rejects both: safe, at the cost
    of a re-login inside that second also being rejected."""
    return now.replace(microsecond=0) + timedelta(seconds=1)


@dataclass(frozen=True)
class UserFootprint:
    follows: int
    #: Follows moved aside by a ban.
    suspended_follows: int
    #: Feeds this user brought in (spec #104 §1.5).
    feeds_brought_in: int
    #: Counted ledger rows per kind in the last 24h (Phase 2).
    usage_24h: Dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class FollowInfo:
    podcast_id: str
    title: str
    slug: str
    image_url: Optional[str]
    #: This user brought the feed in (§1.5).
    introduced: bool
    #: Moved aside by a ban.
    suspended: bool
    #: Other users following it now.
    other_followers: int


@dataclass(frozen=True)
class UserDetail:
    user: User
    usage_24h: Dict[str, int]
    usage_7d: Dict[str, int]
    #: Per kind, one count per UTC day for the last 7 days, oldest first.
    daily_7d: Dict[str, List[int]]
    follows: List[FollowInfo]
    quota_limits: Dict[str, int]
    quota_overrides: Dict[str, int]
    history: List[AuditEntry]


@dataclass(frozen=True)
class BanResult:
    user: User
    follows_moved: int
    #: Feeds the ban left without an active follower.
    feeds_orphaned: List[str]
    tasks_cancelled: int
    #: Feeds dropped by ``remove_introduced_feeds=True``.
    feeds_removed: List[str]


@dataclass(frozen=True)
class ReinstateResult:
    user: User
    follows_restored: int
    #: Feeds that had no follower until this reinstate.
    feeds_reactivated: List[str]


@dataclass(frozen=True)
class RemovedFeeds:
    user: User
    feeds_removed: List[str]
    tasks_cancelled: int


class AccountAdminService:
    def __init__(
        self,
        *,
        users: "UserRepository",
        directory: "UserDirectoryRepository",
        followers: "PodcastFollowerRepository",
        suspended: "SuspendedFollowRepository",
        mcp_tokens: "McpTokenRepository",
        podcasts: "PodcastRepository",
        queue: QueuedTaskCanceller,
        audit: "AuditLog",
        multi_user: bool,
        settings: Optional["SettingsService"] = None,
        quota: Optional["QuotaService"] = None,
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        self._users = users
        self._directory = directory
        self._followers = followers
        self._suspended = suspended
        self._mcp_tokens = mcp_tokens
        self._podcasts = podcasts
        self._queue = queue
        self._audit = audit
        self._multi_user = multi_user
        self._settings = settings
        self._quota = quota
        self._clock = clock

    # ----- reads -----

    def list_users(
        self,
        *,
        status: Optional[UserStatus] = None,
        q: Optional[str] = None,
        sort: UserSort = "created",
        limit: int = 50,
        offset: int = 0,
    ) -> List[UserSummary]:
        self._require_multi_user()
        return self._directory.list_users(
            status=status, q=q, sort=sort, limit=limit, offset=offset, usage_since=self._clock() - timedelta(hours=24)
        )

    def count_users(self, *, status: Optional[UserStatus] = None, q: Optional[str] = None) -> int:
        self._require_multi_user()
        return self._directory.count_users(status=status, q=q)

    def audit_page(
        self, *, action: Optional[str] = None, target_user_id: Optional[str] = None, limit: int = 50, offset: int = 0
    ) -> Tuple[List[AuditEntry], int, Dict[str, str]]:
        """A page of the audit log, its total, and the emails of the users
        it names (one query)."""
        self._require_multi_user()
        entries = self._audit.entries(action=action, target_user_id=target_user_id, limit=limit, offset=offset)
        emails = self._directory.emails_by_id(
            [e.actor_user_id for e in entries if e.actor_user_id]
            + [e.target_user_id for e in entries if e.target_user_id]
        )
        return entries, self._audit.count(action=action, target_user_id=target_user_id), emails

    def footprint(self, user_id: str) -> UserFootprint:
        return UserFootprint(
            follows=len(self._followers.get_followed_podcast_ids(user_id)),
            suspended_follows=len(self._suspended.list_podcast_ids(user_id)),
            feeds_brought_in=len(self._followers.introduced_podcast_ids(user_id)),
            usage_24h=self._quota.usage_last_day(user_id) if self._quota is not None else {},
        )

    def detail(self, user_id: str) -> UserDetail:
        """Everything the console's user page shows."""
        self._require_multi_user()
        user = self._load(user_id)
        introduced = set(self._followers.introduced_podcast_ids(user.id))
        active = self._followers.get_followed_podcast_ids(user.id)
        follows: List[FollowInfo] = []
        for podcast_id, suspended in [(pid, False) for pid in active] + [
            (pid, True) for pid in self._suspended.list_podcast_ids(user.id)
        ]:
            podcast = self._podcasts.get_by_id(podcast_id)
            if podcast is None:
                continue
            followers = self._followers.count_by_podcast(podcast_id)
            follows.append(
                FollowInfo(
                    podcast_id=podcast_id,
                    title=podcast.title,
                    slug=podcast.slug,
                    image_url=podcast.image_url,
                    introduced=podcast_id in introduced,
                    suspended=suspended,
                    other_followers=followers - (0 if suspended else 1),
                )
            )
        follows.sort(key=lambda f: (not f.introduced, f.title.lower()))
        now = self._clock()
        quota = self._quota
        return UserDetail(
            user=user,
            usage_24h=quota.usage_since(user.id, now - timedelta(hours=24)) if quota else {},
            usage_7d=quota.usage_since(user.id, now - timedelta(days=7)) if quota else {},
            daily_7d=quota.daily_usage(user.id, 7) if quota else {},
            follows=follows,
            quota_limits=quota.effective_limits(user.id) if quota else {},
            quota_overrides=quota.overrides_for(user.id) if quota else {},
            history=self._audit.entries(target_user_id=user.id, limit=100),
        )

    def resolve_user(self, email_or_id: str) -> User:
        """Find a user by email (case-insensitive) or id."""
        self._require_multi_user()
        key = email_or_id.strip()
        user = self._users.get_by_email(key) or self._users.get_by_email(key.lower()) or self._users.get_by_id(key)
        if user is None:
            raise AdminActionError("not_found", f"No user matches {email_or_id!r}.")
        return user

    # ----- status -----

    def suspend(self, user_id: str, *, reason: str, actor: Actor) -> User:
        """Block sign-in and every request. Follows are kept, so lifting it
        restores the account exactly. (Suspending a banned user keeps the
        ban's moved-aside follows aside; a reinstate restores them.)"""
        target, reason = self._disable(user_id, UserStatus.SUSPENDED, reason, actor)
        self._audit.record(actor, "user.suspend", target_user_id=target.id, reason=reason, old=target.status.value)
        return self._load(target.id)

    def ban(self, user_id: str, *, reason: str, actor: Actor, remove_introduced_feeds: bool = False) -> BanResult:
        """Block the account *and* stop the spending it caused: the user's
        follows move aside, so feeds only they followed leave the follower
        gate and their queued tasks are cancelled. Running tasks finish."""
        target, reason = self._disable(user_id, UserStatus.BANNED, reason, actor)
        # Measured before the move: earliest follow across both tables.
        introduced = self._followers.introduced_podcast_ids(target.id)
        moved = self._suspended.move_follows_aside(target.id, now=self._clock())
        # From the durable rows, not just this run's move, so re-running a
        # ban that crashed after the move still cancels the orphaned work.
        orphaned = [
            pid for pid in self._suspended.list_podcast_ids(target.id) if self._followers.count_by_podcast(pid) == 0
        ]
        cancelled = self._queue.cancel_queued_for_podcasts(orphaned)
        removed: List[str] = []
        if remove_introduced_feeds:
            removed, more = self._remove_introduced(target.id, introduced)
            cancelled += more
        self._audit.record(
            actor,
            "user.ban",
            target_user_id=target.id,
            reason=reason,
            old=target.status.value,
            follows_moved=len(moved),
            feeds_orphaned=len(orphaned),
            tasks_cancelled=cancelled,
            introduced_feeds_removed=len(removed),
        )
        return BanResult(
            user=self._load(target.id),
            follows_moved=len(moved),
            feeds_orphaned=orphaned,
            tasks_cancelled=cancelled,
            feeds_removed=removed,
        )

    def reinstate(self, user_id: str, *, actor: Actor, reason: Optional[str] = None) -> ReinstateResult:
        """Lift a suspension or ban. Follows a ban moved aside come back with
        their original ``created_at``, bypassing the follow gate (an admin
        chose it); each feed's schedule is re-armed like a follow does."""
        self._require_multi_user()
        target = self._load(user_id)
        self._guard_target(target, actor)
        # Whatever is moved aside comes back, whatever the current status:
        # a ban followed by a suspend must not strand the follows.
        aside = self._suspended.list_podcast_ids(target.id)
        reactivated = [pid for pid in aside if self._followers.count_by_podcast(pid) == 0]
        restored = self._suspended.restore_follows(target.id) if aside else []
        for podcast_id in restored:
            try:
                self._podcasts.reschedule_unscheduled_feed(podcast_id)
            except Exception:
                logger.exception("reinstate_reschedule_failed", podcast_id=podcast_id)
        # Status last: a crash above leaves the user disabled and re-runnable.
        self._users.apply_status(
            target.id, UserStatus.ACTIVE, reason=reason, changed_by=actor.user_id, changed_at=self._clock()
        )
        self._audit.record(
            actor,
            "user.reinstate",
            target_user_id=target.id,
            reason=reason,
            old=target.status.value,
            follows_restored=len(restored),
            reactivated_feeds=len(reactivated),
        )
        return ReinstateResult(
            user=self._load(target.id), follows_restored=len(restored), feeds_reactivated=reactivated
        )

    def set_status(
        self,
        user_id: str,
        status: UserStatus,
        *,
        reason: Optional[str],
        actor: Actor,
        remove_introduced_feeds: bool = False,
    ) -> User:
        """The console's single status action: suspend, ban or reinstate."""
        if status == UserStatus.SUSPENDED:
            return self.suspend(user_id, reason=reason or "", actor=actor)
        if status == UserStatus.BANNED:
            return self.ban(
                user_id, reason=reason or "", actor=actor, remove_introduced_feeds=remove_introduced_feeds
            ).user
        return self.reinstate(user_id, actor=actor, reason=reason).user

    def _disable(self, user_id: str, status: UserStatus, reason: Optional[str], actor: Actor) -> Tuple[User, str]:
        """Shared first step of suspend and ban: validate, set the status,
        revoke every session and the MCP token. Returns the target as it
        was before, and the cleaned reason."""
        self._require_multi_user()
        target = self._load(user_id)
        cleaned = (reason or "").strip()
        if not cleaned:
            raise AdminActionError("reason_required", "Give a reason; it is kept in the audit log.")
        self._guard_target(target, actor)
        now = self._clock()
        try:
            self._users.apply_status(
                target.id,
                status,
                reason=cleaned,
                changed_by=actor.user_id,
                changed_at=now,
                tokens_valid_after=revocation_instant(now),
                keep_an_active_admin=True,
            )
        except LastActiveAdminError:
            raise _last_admin() from None
        self._mcp_tokens.revoke(target.id, revoked_at=now)
        return target, cleaned

    # ----- sessions -----

    def logout_everywhere(self, user_id: str, *, actor: Actor) -> User:
        """Reject every JWT issued so far and revoke the MCP token."""
        self._require_multi_user()
        target = self._load(user_id)
        now = self._clock()
        self._users.set_tokens_valid_after(target.id, revocation_instant(now))
        self._mcp_tokens.revoke(target.id, revoked_at=now)
        self._audit.record(actor, "user.logout_everywhere", target_user_id=target.id)
        return self._load(target.id)

    # ----- feeds -----

    def remove_introduced_feeds(self, user_id: str, *, actor: Actor) -> RemovedFeeds:
        """Drop the user's follow of every feed they brought in that no
        other active user follows now, and cancel those feeds' queued tasks.
        Podcast rows, episodes and anything processed stay (deleting them
        is a separate, explicit ``thestill remove``)."""
        self._require_multi_user()
        target = self._load(user_id)
        removed, cancelled = self._remove_introduced(target.id, self._followers.introduced_podcast_ids(target.id))
        self._audit.record(
            actor,
            "user.remove_introduced_feeds",
            target_user_id=target.id,
            feeds_removed=len(removed),
            tasks_cancelled=cancelled,
        )
        return RemovedFeeds(user=target, feeds_removed=removed, tasks_cancelled=cancelled)

    def _remove_introduced(self, user_id: str, introduced: List[str]) -> Tuple[List[str], int]:
        # Only feeds the user still follows (or has moved aside): the ledger
        # remembers feeds they brought in and later dropped.
        following = set(self._followers.get_followed_podcast_ids(user_id)) | set(
            self._suspended.list_podcast_ids(user_id)
        )
        removable = [pid for pid in introduced if pid in following and not self._other_active_followers(pid, user_id)]
        for podcast_id in removable:
            self._followers.remove(user_id, podcast_id)
        self._suspended.discard(user_id, removable)
        return removable, self._queue.cancel_queued_for_podcasts(removable)

    def _other_active_followers(self, podcast_id: str, user_id: str) -> bool:
        for follower_id in self._followers.get_follower_user_ids(podcast_id):
            if follower_id == user_id:
                continue
            other = self._users.get_by_id(follower_id)
            if other is not None and other.is_active:
                return True
        return False

    # ----- quotas -----

    def set_quota_override(self, user_id: str, quota: str, limit: Optional[int], *, actor: Actor) -> Dict[str, int]:
        """Raise or lower one user's ``quota`` (``None`` reverts to the env
        default; 0 means unlimited). Returns the user's overrides."""
        target = self._load_for_overrides(user_id)

        def edit(mine: Dict[str, int]) -> Dict[str, int]:
            if limit is None:
                mine.pop(quota, None)
            else:
                mine[quota] = limit
            return mine

        old, stored = self._edit_overrides(target.id, edit, actor)
        self._audit.record(
            actor, "user.quota_override", target_user_id=target.id, quota=quota, old=old.get(quota), new=limit
        )
        return stored

    def replace_quota_overrides(self, user_id: str, overrides: Dict[str, int], *, actor: Actor) -> Dict[str, int]:
        """Set all of one user's overrides at once (the console's form); an
        empty map clears them."""
        target = self._load_for_overrides(user_id)
        old, stored = self._edit_overrides(target.id, lambda _mine: dict(overrides), actor)
        self._audit.record(actor, "user.quota_override", target_user_id=target.id, old=old, new=dict(overrides))
        return stored

    def _load_for_overrides(self, user_id: str) -> User:
        self._require_multi_user()
        if self._settings is None:
            raise AdminActionError("unavailable", "Quota overrides need the settings service.")
        return self._load(user_id)

    def _edit_overrides(
        self, user_id: str, edit: Callable[[Dict[str, int]], Dict[str, int]], actor: Actor
    ) -> Tuple[Dict[str, int], Dict[str, int]]:
        """Apply ``edit`` to one user's limits inside a locked read-modify-
        write of the stored map, never the cached one, so a concurrent edit
        of another user's limits is kept. Returns ``(old, new)`` limits."""
        seen: Dict[str, Dict[str, int]] = {}

        def change(current: Dict[str, Dict[str, int]]) -> Dict[str, Dict[str, int]]:
            everyone = {uid: dict(limits) for uid, limits in current.items()}
            seen["old"] = everyone.pop(user_id, {})
            mine = edit(dict(seen["old"]))
            if mine:
                everyone[user_id] = mine
            return everyone

        try:
            stored = self._settings.modify("quota_overrides", change, actor=actor, audit=False)
        except ValueError as exc:
            raise AdminActionError("invalid_quota", str(exc)) from exc
        return seen["old"], stored.get(user_id, {})

    # ----- admin role -----

    def set_admin(self, user_id: str, is_admin: bool, *, actor: Actor) -> User:
        return self.grant_admin(user_id, actor=actor) if is_admin else self.revoke_admin(user_id, actor=actor)

    def grant_admin(self, user_id: str, *, actor: Actor) -> User:
        self._require_multi_user()
        target = self._load(user_id)
        if not target.is_admin:
            self._users.set_admin(target.id, True)
            self._audit.record(actor, "admin.grant", target_user_id=target.id)
        return self._load(target.id)

    def revoke_admin(self, user_id: str, *, actor: Actor) -> User:
        self._require_multi_user()
        target = self._load(user_id)
        if target.is_admin:
            self._guard_target(target, actor)
            try:
                self._users.set_admin(target.id, False, keep_an_active_admin=True)
            except LastActiveAdminError:
                raise _last_admin() from None
            self._audit.record(actor, "admin.revoke", target_user_id=target.id)
        return self._load(target.id)

    # ----- guards -----

    def _require_multi_user(self) -> None:
        if not self._multi_user:
            raise AdminActionError("single_user_mode", "User administration needs multi-user mode (MULTI_USER=true).")

    def _guard_target(self, target: User, actor: Actor) -> None:
        """An admin can't act on their own account. (The last active admin
        can't be disabled or demoted either: the repository checks that in
        the same transaction as the change.)"""
        if actor.user_id is not None and actor.user_id == target.id:
            raise AdminActionError("cannot_modify_self", "You can't change your own account status or admin role.")

    def _load(self, user_id: str) -> User:
        user = self._users.get_by_id(user_id)
        if user is None:
            raise AdminActionError("not_found", f"No user with id {user_id!r}.")
        return user
