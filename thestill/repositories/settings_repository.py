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

"""Spec #104 — runtime settings persistence (``app_settings``).

One row per key, value stored as JSON. A key with no row falls back to its
environment default; ``SettingsService`` owns that rule and the cache.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Optional


@dataclass(frozen=True)
class StoredSetting:
    key: str
    value: Any
    updated_at: datetime
    updated_by: Optional[str]


class SettingsRepository(ABC):
    @abstractmethod
    def get_all(self) -> Dict[str, StoredSetting]:
        """Every stored row, keyed by setting key."""

    @abstractmethod
    def upsert(self, key: str, value: Any, *, updated_by: Optional[str], updated_at: datetime) -> None:
        """Insert or replace one setting. ``value`` must be JSON-serialisable."""

    @abstractmethod
    def modify(
        self,
        key: str,
        change: Callable[[Optional[Any]], Any],
        *,
        updated_by: Optional[str],
        updated_at: datetime,
    ) -> Any:
        """Atomic read-modify-write of one key against the database, never a
        cache: ``change(current)`` (``None`` when there is no row) returns
        the value to store, inside one locked transaction. A raise from
        ``change`` leaves the row as it was. Returns the stored value."""

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Remove a row so the key reverts to its env default. True if a row went."""
