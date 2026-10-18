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

"""Spec #104 §3.2–§3.3 — operator alerts and the daily digest.

``AlertService.tick`` runs every 5 minutes on the periodic runner and
evaluates six rules. A threshold trips when the reading reaches it (``>=``);
``0`` turns a rule off. Each alert kind (per user, where it is about a
user) is sent at most once an hour; the claim is an atomic upsert in
``app_settings`` (``alerts.last_sent.*``), so several workers send it once.
Delivery is never retried: an alert every channel failed is lost for the
hour, and says so in the log.

The spend breaker is the only alert that acts on its own: with
``spend_breaker_action = pause_new_feeds`` it also switches
``new_feeds_enabled`` off. It never suspends users.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Callable, Dict, List, Mapping, Optional, Protocol, Sequence

from structlog import get_logger

from ...models.user import User
from ...utils.datetime_utils import now_utc
from ...utils.duration import parse_duration
from .actor import Actor
from .alert_channels import AlertChannel, AlertMessage, deliver
from .settings_registry import ALERT_THRESHOLDS

if TYPE_CHECKING:
    from ...repositories.settings_repository import SettingsRepository
    from ...repositories.usage_event_repository import UsageEventRepository
    from ...repositories.user_directory_repository import UserDirectoryRepository
    from ...repositories.user_repository import UserRepository
    from ...utils.config import Config
    from .settings_service import SettingsService

logger = get_logger(__name__)

ALERT_HOLD_SECONDS = 3600
LAST_SENT_PREFIX = "alerts.last_sent."
DIGEST_KEY = "alerts.digest"
#: A digest slot claimed less than this ago blocks another (once a day).
DIGEST_HOLD_SECONDS = 20 * 3600
HOUR = timedelta(hours=1)
DAY = timedelta(hours=24)


class PipelineQueue(Protocol):
    """The slice of either queue manager the alerts read."""

    def get_pending_count(self) -> int: ...

    def queued_audio_durations(self, since: datetime) -> List[Optional[str]]: ...


def _seconds(raw: Optional[str]) -> int:
    """A feed duration in seconds; 0 for missing or junk (``inf`` included)."""
    try:
        return parse_duration(raw) or 0
    except (ValueError, OverflowError):
        return 0


class AlertSignals:
    """What the rules read: the repositories, and this process's
    rate-limit hits."""

    def __init__(
        self,
        *,
        usage: "UsageEventRepository",
        directory: "UserDirectoryRepository",
        queue: PipelineQueue,
        rate_limit_hits: Callable[[], Dict[str, int]],
    ) -> None:
        self._usage = usage
        self._directory = directory
        self._queue = queue
        self._rate_limit_hits = rate_limit_hits

    def counts_by_user(self, kind: str, since: datetime) -> Dict[str, int]:
        return self._usage.counts_by_user(kind, since)

    def rate_limit_hits_by_user(self) -> Dict[str, int]:
        """Last hour, this worker only: a per-worker lower bound (§3.2)."""
        return self._rate_limit_hits()

    def signups_since(self, since: datetime) -> List[User]:
        return self._directory.created_since(since)

    def pending_tasks(self) -> int:
        return self._queue.get_pending_count()

    def queued_audio_minutes(self, since: datetime) -> int:
        return sum(_seconds(raw) for raw in self._queue.queued_audio_durations(since)) // 60


@dataclass(frozen=True)
class Alert:
    #: Dedup key: the kind, plus the user for per-user alerts.
    key: str
    message: AlertMessage
    #: Runs once the alert is claimed (the spend breaker's pause).
    action: Optional[Callable[[], None]] = None


def _tripped(limits: Mapping[str, object], name: str, reading: int) -> bool:
    threshold = int(limits[name])  # type: ignore[arg-type]
    return threshold > 0 and reading >= threshold


class AlertService:
    def __init__(
        self,
        *,
        signals: AlertSignals,
        channels: Sequence[AlertChannel],
        claims: "SettingsRepository",
        settings: "SettingsService",
        users: "UserRepository",
        config: "Config",
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        self._signals = signals
        self._channels = list(channels)
        self._claims = claims
        self._settings = settings
        self._users = users
        self._config = config
        self._clock = clock

    @property
    def channel_names(self) -> List[str]:
        return [channel.name for channel in self._channels]

    def with_channels(self, channels: Sequence[AlertChannel]) -> "AlertService":
        """The same service delivering to ``channels`` (the CLI adds email
        only for its ``alerts`` commands)."""
        return AlertService(
            signals=self._signals,
            channels=channels,
            claims=self._claims,
            settings=self._settings,
            users=self._users,
            config=self._config,
            clock=self._clock,
        )

    def send(self, message: AlertMessage) -> Dict[str, bool]:
        """Deliver ``message`` to every channel; per-channel success."""
        return deliver(self._channels, message)

    def thresholds(self) -> Dict[str, object]:
        """Env defaults with the ``alerts`` runtime overrides on top."""
        values: Dict[str, object] = {name: getattr(self._config, f"alert_{name}") for name in ALERT_THRESHOLDS}
        values["spend_breaker_action"] = self._config.alert_spend_breaker_action
        values.update(self._settings.get("alerts"))
        return values

    # ----- instant alerts -----

    def evaluate(self) -> List[Alert]:
        """Every alert whose rule trips now. A rule that fails (a database
        error, a bad reading) is logged and skipped; the others still run."""
        now = self._clock()
        limits = self.thresholds()
        rules: List[Callable[[], List[Alert]]] = [
            lambda: self._per_user_alerts(now, limits),
            lambda: self._signup_spike(now, limits),
            lambda: self._feed_spike(now, limits),
            lambda: self._queue_backlog(limits),
            lambda: self._spend_breaker(now, limits),
        ]
        alerts: List[Alert] = []
        for rule in rules:
            try:
                alerts.extend(rule())
            except Exception:
                logger.exception("admin_alert_rule_failed")
        return alerts

    def _per_user_alerts(self, now: datetime, limits: Mapping[str, object]) -> List[Alert]:
        alerts = []
        for user_id, hits in self._signals.counts_by_user("quota_exceeded", now - HOUR).items():
            if _tripped(limits, "quota_pressure", hits):
                alerts.append(
                    self._user_alert("quota_pressure", user_id, hits, f"hit a quota {hits} times in the last hour")
                )
        for user_id, hits in self._signals.rate_limit_hits_by_user().items():
            if _tripped(limits, "rate_limit_pressure", hits):
                alerts.append(
                    self._user_alert(
                        "rate_limit_pressure",
                        user_id,
                        hits,
                        f"was rate-limited {hits} times in the last hour on one worker",
                    )
                )
        return alerts

    def _signup_spike(self, now: datetime, limits: Mapping[str, object]) -> List[Alert]:
        signups = len(self._signals.signups_since(now - HOUR))
        if not _tripped(limits, "signup_spike", signups):
            return []
        return [
            self._global_alert(
                "signup_spike",
                f"{signups} new accounts in the last hour",
                "Close signups: thestill settings set signup_mode closed",
                signups=signups,
            )
        ]

    def _feed_spike(self, now: datetime, limits: Mapping[str, object]) -> List[Alert]:
        feeds = sum(self._signals.counts_by_user("feed_introduced", now - HOUR).values())
        if not _tripped(limits, "feed_spike", feeds):
            return []
        return [
            self._global_alert(
                "feed_spike",
                f"{feeds} feeds were brought in in the last hour",
                "Pause new feeds: thestill settings set new_feeds_enabled false",
                feeds=feeds,
            )
        ]

    def _queue_backlog(self, limits: Mapping[str, object]) -> List[Alert]:
        pending = self._signals.pending_tasks()
        if not _tripped(limits, "queue_backlog", pending):
            return []
        return [
            self._global_alert(
                "queue_backlog",
                f"{pending} pipeline tasks are pending",
                "See the queue: thestill status",
                pending=pending,
            )
        ]

    def _spend_breaker(self, now: datetime, limits: Mapping[str, object]) -> List[Alert]:
        if not int(limits["spend_breaker_minutes"]):  # type: ignore[arg-type]
            return []  # off: don't even sum the queue
        minutes = self._signals.queued_audio_minutes(now - DAY)
        if not _tripped(limits, "spend_breaker_minutes", minutes):
            return []
        limit = limits["spend_breaker_minutes"]
        title = f"About {minutes} audio minutes were queued for transcription in the last 24 hours (limit {limit})"
        action = None
        if not self._settings.get("new_feeds_enabled"):
            hint = "New feeds are already paused. Resume: thestill settings set new_feeds_enabled true"
        elif limits["spend_breaker_action"] == "pause_new_feeds":
            hint = (
                "New feeds are now paused (new_feeds_enabled = false). Feeds that already have followers keep "
                "processing. Resume: thestill settings set new_feeds_enabled true"
            )
            action = self._pause_new_feeds
        else:
            hint = "Pause new feeds: thestill settings set new_feeds_enabled false"
        message = AlertMessage("spend_breaker", title, f"{title}.\n\n{hint}", {"minutes": minutes, "limit": limit})
        return [Alert("spend_breaker", message, action)]

    def tick(self) -> List[str]:
        """Evaluate, then send each alert not sent in the last hour.
        Returns the keys sent."""
        sent = []
        now = self._clock()
        for alert in self.evaluate():
            try:
                won = self._claims.claim(
                    LAST_SENT_PREFIX + alert.key,
                    {"kind": alert.message.kind, "title": alert.message.title},
                    now=now,
                    min_interval_seconds=ALERT_HOLD_SECONDS,
                )
            except Exception:
                logger.exception("admin_alert_claim_failed", alert=alert.message.kind)
                continue
            if not won:
                continue
            if alert.action is not None:
                try:
                    alert.action()
                except Exception:
                    logger.exception("admin_alert_action_failed", alert=alert.message.kind)
            external = {name: ok for name, ok in self.send(alert.message).items() if name != "log"}
            if external and not any(external.values()):
                logger.warning("admin_alert_undelivered", alert=alert.message.kind)
            sent.append(alert.key)
        return sent

    def send_test(self) -> Dict[str, bool]:
        """A test alert to every configured channel; per-channel success."""
        return self.send(
            AlertMessage("test", "Test alert", "This is a test alert from thestill. Alerts reach you here.")
        )

    def _user_alert(self, kind: str, user_id: str, count: int, what: str) -> Alert:
        email = self._email(user_id)
        title = f"{email} {what}"
        link = self._user_link(user_id, email)
        return Alert(
            key=f"{kind}.{user_id}",
            message=AlertMessage(kind, title, f"{title}.\n\nSuspend: {link}", {"user_id": user_id, "count": count}),
        )

    def _global_alert(self, kind: str, title: str, hint: str, **details) -> Alert:
        return Alert(key=kind, message=AlertMessage(kind, title, f"{title}.\n\n{hint}", details))

    def _pause_new_feeds(self) -> None:
        self._settings.set("new_feeds_enabled", False, actor=Actor.system("spend_breaker"))

    def _user_link(self, user_id: str, email: str) -> str:
        base = self._config.public_base_url
        return f"{base}/admin/users/{user_id}" if base else f"thestill user suspend {email} --reason ..."

    # ----- daily digest -----

    def maybe_send_digest(self) -> bool:
        """Send the digest once a day at ``ADMIN_DIGEST_HOUR`` (server-local
        time). Quiet days are skipped unless ``ADMIN_DIGEST_ALWAYS``; a
        skipped day still uses up its slot."""
        now = self._clock()
        if now.astimezone().hour != self._config.admin_digest_hour:
            return False
        if not self._claims.claim(
            DIGEST_KEY, {"date": now.date().isoformat()}, now=now, min_interval_seconds=DIGEST_HOLD_SECONDS
        ):
            return False
        digest = self.build_digest(force=self._config.admin_digest_always)
        if digest is None:
            logger.info("admin_digest_skipped_quiet")
            return False
        self.send(digest)
        return True

    def build_digest(self, *, force: bool = False) -> Optional[AlertMessage]:
        """The last 24 hours. None on a quiet day (no signups, quota hits
        or alerts) unless ``force``."""
        now = self._clock()
        since = now - DAY
        signups = self._signals.signups_since(since)
        quota_hits = self._signals.counts_by_user("quota_exceeded", since)
        alerts_sent = sorted(
            (row.updated_at, row.value.get("title", row.key))
            for row in self._claims.get_all().values()
            if row.key.startswith(LAST_SENT_PREFIX) and row.updated_at >= since
        )
        if not (signups or quota_hits or alerts_sent or force):
            return None

        lines = [f"Thestill daily digest, {now:%Y-%m-%d}", "", f"New accounts (24h): {len(signups)}"]
        lines += [f"  {u.email}  (from {u.signup_source or 'direct'})" for u in signups]
        lines += self._top("Top 5 by feeds brought in (24h)", self._signals.counts_by_user("feed_introduced", since))
        lines += self._top("Top 5 by follows (24h)", self._signals.counts_by_user("follow", since))
        lines += self._top(
            "Most rate-limited (last hour, this web worker only)", self._signals.rate_limit_hits_by_user()
        )
        lines += self._top("Quota hits (24h)", quota_hits, limit=None)
        lines += ["", "Alerts sent (24h):"]
        lines += [f"  {at:%H:%M} {title}" for at, title in alerts_sent] or ["  none"]
        changed = self._settings.non_default()
        lines += [
            "",
            f"Signup mode: {self._settings.get('signup_mode')}",
            "Switches not at their default: " + (", ".join(f"{k}={v}" for k, v in changed.items()) or "none"),
        ]
        return AlertMessage(
            "digest",
            f"Daily digest: {len(signups)} new accounts, {sum(quota_hits.values())} quota hits",
            "\n".join(lines),
            {"signups": len(signups), "quota_hits": sum(quota_hits.values()), "alerts": len(alerts_sent)},
        )

    def _top(self, heading: str, counts: Mapping[str, int], *, limit: Optional[int] = 5) -> List[str]:
        ranked = sorted(counts.items(), key=lambda kv: -kv[1])[:limit]
        return ["", f"{heading}:"] + ([f"  {self._email(uid)}: {n}" for uid, n in ranked] or ["  none"])

    def _email(self, user_id: str) -> str:
        user = self._users.get_by_id(user_id)
        return user.email if user else user_id
