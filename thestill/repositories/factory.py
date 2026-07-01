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

"""Repository backend selector (spec #44 Phase 0).

Single wiring point that returns SQLite- or Postgres-backed repositories based
on config, replacing the ~21 hardcoded ``Sqlite*Repository(db_path=…)`` call
sites across cli / web / mcp. When ``config.database_url`` is set, Postgres
implementations are returned; otherwise the SQLite path is used, so local and
self-hosted keep working with zero config change.

This slice wires the repos that have a Postgres implementation today
(``user``). As each remaining repo is ported (spec #44 Phases 1–2), add a
``make_*_repository`` here — the entry points call the factory, not the
concrete classes, so they never change again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .user_repository import UserRepository

if TYPE_CHECKING:
    from ..utils.config import Config


def uses_postgres(config: "Config") -> bool:
    """True when a Postgres DSN is configured (``DATABASE_URL`` set)."""
    return bool(getattr(config, "database_url", "") or "")


def make_user_repository(config: "Config") -> UserRepository:
    """Return the configured user repository (Postgres if ``DATABASE_URL`` set,
    else SQLite)."""
    if uses_postgres(config):
        from .postgres_user_repository import PostgresUserRepository

        return PostgresUserRepository(config.database_url)

    from .sqlite_user_repository import SqliteUserRepository

    return SqliteUserRepository(db_path=config.database_path)
