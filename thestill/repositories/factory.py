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

"""Repository backend selector (spec #44).

Single wiring point that returns SQLite- or Postgres-backed persistence based
on config, replacing the hardcoded ``Sqlite*Repository(db_path=…)`` call sites
across cli / web / mcp. When ``config.database_url`` is set, Postgres
implementations are returned (after a one-time idempotent schema bootstrap);
otherwise the SQLite path is used, so local and self-hosted keep working with
zero config change.

Everything is lazy-imported per backend: a SQLite-only install never imports
psycopg, and vice versa.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from .briefing_repository import BriefingRepository
from .briefing_schedule_repository import BriefingScheduleRepository
from .inbox_repository import InboxRepository
from .podcast_follower_repository import PodcastFollowerRepository
from .user_repository import UserRepository

if TYPE_CHECKING:
    from ..utils.config import Config


def uses_postgres(config: "Config") -> bool:
    """True when a Postgres DSN is configured (``DATABASE_URL`` set)."""
    return bool(getattr(config, "database_url", "") or "")


_schema_lock = threading.Lock()
_schema_ready: set[str] = set()


def _ensure_pg_schema(dsn: str, config: Optional["Config"] = None) -> None:
    """One-time idempotent typed-schema bootstrap per DSN per process.

    The pgvector column width is resolved from the configured embedding
    model via ``search.base.embedding_dim_for`` so a non-384-dim model gets
    a matching schema instead of insert-time dimension errors.
    """
    with _schema_lock:
        if dsn in _schema_ready:
            return
        from ..search.base import DEFAULT_EMBEDDING_MODEL, embedding_dim_for
        from .postgres_schema import ensure_schema

        model = getattr(config, "embedding_model", None) or DEFAULT_EMBEDDING_MODEL
        ensure_schema(dsn, embedding_dim=embedding_dim_for(model))
        _schema_ready.add(dsn)


@dataclass
class RepositoryBundle:
    """Everything the entry points need, backend-resolved once.

    ``podcast`` satisfies both PodcastRepository and EpisodeRepository (both
    concrete classes implement the two ABCs). ``entity`` / ``pending_ops`` /
    ``queue_manager`` are typed loosely because their interfaces live in
    their own modules (EntityRepository / PendingOperationsRepository) and the
    queue is duck-typed by the worker.
    """

    backend: str  # "sqlite" | "postgres"
    podcast: Any
    user: UserRepository
    follower: PodcastFollowerRepository
    inbox: InboxRepository
    briefing: BriefingRepository
    briefing_schedule: BriefingScheduleRepository
    pending_ops: Any
    entity: Any
    queue_manager: Any


def make_repositories(config: "Config") -> RepositoryBundle:
    """Return the full backend-resolved persistence bundle."""
    if uses_postgres(config):
        dsn = config.database_url
        _ensure_pg_schema(dsn, config)

        from ..core.postgres_queue_manager import PostgresQueueManager
        from .postgres_briefing_repository import PostgresBriefingRepository
        from .postgres_briefing_schedule_repository import PostgresBriefingScheduleRepository
        from .postgres_entity_repository import PostgresEntityRepository
        from .postgres_inbox_repository import PostgresInboxRepository
        from .postgres_pending_operations_repository import PostgresPendingOperationsRepository
        from .postgres_podcast_follower_repository import PostgresPodcastFollowerRepository
        from .postgres_podcast_repository import PostgresPodcastRepository
        from .postgres_user_repository import PostgresUserRepository

        return RepositoryBundle(
            backend="postgres",
            podcast=PostgresPodcastRepository(dsn),
            user=PostgresUserRepository(dsn),
            follower=PostgresPodcastFollowerRepository(dsn),
            inbox=PostgresInboxRepository(dsn),
            briefing=PostgresBriefingRepository(dsn),
            briefing_schedule=PostgresBriefingScheduleRepository(dsn),
            pending_ops=PostgresPendingOperationsRepository(dsn),
            entity=PostgresEntityRepository(dsn),
            queue_manager=PostgresQueueManager(dsn),
        )

    db_path = str(config.database_path)
    from ..core.queue_manager import QueueManager
    from .sqlite_briefing_repository import SqliteBriefingRepository
    from .sqlite_briefing_schedule_repository import SqliteBriefingScheduleRepository
    from .sqlite_entity_repository import SqliteEntityRepository
    from .sqlite_inbox_repository import SqliteInboxRepository
    from .sqlite_pending_operations_repository import SqlitePendingOperationsRepository
    from .sqlite_podcast_follower_repository import SqlitePodcastFollowerRepository
    from .sqlite_podcast_repository import SqlitePodcastRepository
    from .sqlite_user_repository import SqliteUserRepository

    return RepositoryBundle(
        backend="sqlite",
        podcast=SqlitePodcastRepository(db_path=db_path),
        user=SqliteUserRepository(db_path=db_path),
        follower=SqlitePodcastFollowerRepository(db_path=db_path),
        inbox=SqliteInboxRepository(db_path=db_path),
        briefing=SqliteBriefingRepository(db_path=db_path),
        briefing_schedule=SqliteBriefingScheduleRepository(db_path=db_path),
        pending_ops=SqlitePendingOperationsRepository(db_path=db_path),
        entity=SqliteEntityRepository(db_path=db_path),
        queue_manager=QueueManager(db_path),
    )


def make_user_repository(config: "Config") -> UserRepository:
    """Return just the configured user repository (kept for callers that
    only need auth; prefers the bundle for full wiring)."""
    if uses_postgres(config):
        _ensure_pg_schema(config.database_url, config)
        from .postgres_user_repository import PostgresUserRepository

        return PostgresUserRepository(config.database_url)

    from .sqlite_user_repository import SqliteUserRepository

    return SqliteUserRepository(db_path=config.database_path)


def make_search_backend(config: "Config", embedding_model: Any) -> Any:
    """Return the configured SearchBackend (pgvector or sqlite-vec)."""
    if uses_postgres(config):
        _ensure_pg_schema(config.database_url, config)
        from ..search.pgvector_client import PgVectorBackend

        return PgVectorBackend(dsn=config.database_url, embedding_model=embedding_model)

    from ..search.sqlite_vec_client import SqliteVecBackend

    return SqliteVecBackend(db_path=str(config.database_path), embedding_model=embedding_model)


def make_chunk_writer(config: "Config", embedding_model: Any) -> Any:
    """Return the configured chunk writer (pgvector or sqlite-vec)."""
    if uses_postgres(config):
        _ensure_pg_schema(config.database_url, config)
        from ..core.postgres_chunk_writer import PostgresChunkWriter

        return PostgresChunkWriter(dsn=config.database_url, embedding_model=embedding_model)

    from ..core.chunk_writer import ChunkWriter

    return ChunkWriter(db_path=str(config.database_path), embedding_model=embedding_model)


def make_queue_manager(config: "Config") -> Any:
    """Return the configured queue manager (SKIP LOCKED on Postgres)."""
    if uses_postgres(config):
        _ensure_pg_schema(config.database_url, config)
        from ..core.postgres_queue_manager import PostgresQueueManager

        return PostgresQueueManager(config.database_url)

    from ..core.queue_manager import QueueManager

    return QueueManager(str(config.database_path))
