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

"""Spec #104 §1.3 — runtime settings with env defaults and a short cache.

Reads go through an in-process cache with a 15-second TTL, cleared on this
process's own writes; with several workers a change reaches all of them
within the TTL. A key with no row falls back to its env default, so an
operator can pin a value in ``.env`` and still override it at runtime.

Switches are enforced where the cost starts: services call
``require(feature)`` right before the paid call (spec §1.3).
"""

import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from structlog import get_logger

from ...repositories.settings_repository import SettingsRepository, StoredSetting
from ...utils.datetime_utils import now_utc
from .actor import Actor
from .errors import FeaturePausedError, ReadOnlyError
from .settings_registry import FEATURES, SETTINGS, get_spec

if TYPE_CHECKING:
    from ...utils.config import Config
    from .audit_service import AuditLog

logger = get_logger(__name__)

DEFAULT_TTL_SECONDS = 15.0


@dataclass(frozen=True)
class EffectiveSetting:
    key: str
    value: Any
    #: ``runtime`` when a row overrides the env default, else ``env``.
    source: str
    default: Any
    description: str
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None

    @property
    def is_default(self) -> bool:
        return self.value == self.default


class SettingsService:
    def __init__(
        self,
        repository: SettingsRepository,
        config: "Config",
        audit: Optional["AuditLog"] = None,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._repo = repository
        self._config = config
        self._audit = audit
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._rows: Optional[Dict[str, StoredSetting]] = None
        self._loaded_at = 0.0
        # Bumped by every invalidate, so a read that started before a write
        # can't store its (older) rows over the write.
        self._generation = 0

    # ----- reads -----

    def _stored(self) -> Dict[str, StoredSetting]:
        with self._lock:
            if self._rows is not None and self._clock() - self._loaded_at < self._ttl:
                return self._rows
            generation = self._generation
        try:
            rows = self._repo.get_all()
        except Exception:
            # Keep serving the last good snapshot rather than silently
            # dropping every runtime override (a forgotten read_only must
            # not lift because the database blinked).
            logger.exception("settings_read_failed", have_cached=self._rows is not None)
            if self._rows is not None:
                return self._rows
            raise
        with self._lock:
            if generation == self._generation:
                self._rows = rows
                self._loaded_at = self._clock()
        return rows

    def effective(self, key: str) -> EffectiveSetting:
        spec = get_spec(key)
        try:
            default = spec.env_default(self._config)
        except ValueError as exc:
            logger.error("setting_env_default_invalid", key=key, error=str(exc))
            default = spec.failsafe
        row = self._stored().get(key)
        if row is not None:
            try:
                value = spec.validate(row.value)
            except ValueError as exc:
                logger.error("setting_row_invalid", key=key, error=str(exc))
            else:
                return EffectiveSetting(
                    key, value, "runtime", default, spec.description, row.updated_at, row.updated_by
                )
        return EffectiveSetting(key, default, "env", default, spec.description)

    def get(self, key: str) -> Any:
        return self.effective(key).value

    def all_effective(self) -> List[EffectiveSetting]:
        return [self.effective(key) for key in SETTINGS]

    def non_default(self) -> Dict[str, Any]:
        """Settings whose value differs from the env default — the admin
        banner and the daily digest show these so a forgotten switch gets
        noticed."""
        return {s.key: s.value for s in self.all_effective() if not s.is_default}

    # ----- feature switches -----

    def refusal(self, feature: str) -> Optional[FeaturePausedError]:
        """The error a paused ``feature`` raises, or None when allowed.
        ``read_only`` pauses every feature, whatever its own switch says."""
        if feature not in FEATURES:
            raise KeyError(f"unknown feature {feature!r}")
        if self.get("read_only"):
            return ReadOnlyError()
        if not self.get(f"{feature}_enabled"):
            return FeaturePausedError(feature)
        return None

    def allowed(self, feature: str) -> bool:
        return self.refusal(feature) is None

    def require(self, feature: str) -> None:
        error = self.refusal(feature)
        if error is not None:
            logger.info("feature_paused_refused", feature=feature, cause=type(error).__name__)
            raise error

    # ----- writes (audited) -----

    def set(
        self,
        key: str,
        value: Any,
        *,
        actor: Actor,
        audit: bool = True,
    ) -> EffectiveSetting:
        """Validate, store and audit one setting. Raises ``KeyError`` for an
        unknown key and ``ValueError`` for a bad value. ``audit=False`` is
        for a caller that records its own, narrower entry (a quota override
        audits one user's limit, not the whole map)."""
        spec = get_spec(key)
        normalised = spec.validate(value)
        old = self.effective(key)
        self._repo.upsert(key, normalised, updated_by=actor.user_id, updated_at=now_utc())
        self.invalidate()
        if audit and self._audit is not None:
            self._audit.record(actor, "setting.set", key=key, old=old.value, new=normalised)
        # Values stay out of the log: the allowlist holds email addresses.
        # The audit log keeps them.
        logger.info("setting_changed", key=key, actor=actor.kind)
        return self.effective(key)

    def modify(self, key: str, change: Callable[[Any], Any], *, actor: Actor, audit: bool = True) -> Any:
        """Atomic read-modify-write against the database, not this cache:
        ``change(current)`` gets the stored value (or the env default) and
        returns the new one, which is validated before it is written. For a
        shared map (``quota_overrides``) edited from several processes, so
        one edit can't overwrite another made in the last 15 seconds."""
        spec = get_spec(key)
        before: Dict[str, Any] = {}

        def validated(current: Any) -> Any:
            before["value"] = spec.validate(current) if current is not None else spec.env_default(self._config)
            return spec.validate(change(before["value"]))

        new = self._repo.modify(key, validated, updated_by=actor.user_id, updated_at=now_utc())
        self.invalidate()
        if audit and self._audit is not None:
            self._audit.record(actor, "setting.set", key=key, old=before["value"], new=new)
        logger.info("setting_changed", key=key, actor=actor.kind)
        return new

    def reset(self, key: str, *, actor: Actor) -> EffectiveSetting:
        """Delete the runtime row so ``key`` reverts to its env default."""
        old = self.effective(key)  # also rejects an unknown key
        removed = self._repo.delete(key)
        self.invalidate()
        if removed and self._audit is not None:
            self._audit.record(actor, "setting.set", key=key, old=old.value, new=old.default, reset=True)
        return self.effective(key)

    def invalidate(self) -> None:
        with self._lock:
            self._rows = None
            self._generation += 1
