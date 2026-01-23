# Copyright 2025 thestill.me
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

"""
Database factory for creating repository instances.

Supports both SQLite (local development) and PostgreSQL (cloud deployment).
Selection is based on DATABASE_URL environment variable:
- If DATABASE_URL starts with "postgresql://", use PostgreSQL
- Otherwise, use SQLite with DATABASE_PATH or default location

Usage:
    from thestill.repositories.database import create_repositories

    repos = create_repositories(config)
    podcast_repo = repos.podcast
    user_repo = repos.user
    follower_repo = repos.follower
"""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from .podcast_follower_repository import PodcastFollowerRepository
from .podcast_repository import EpisodeRepository, PodcastRepository
from .user_repository import UserRepository

if TYPE_CHECKING:
    from ..utils.config import Config

logger = logging.getLogger(__name__)


@dataclass
class Repositories:
    """Container for all repository instances."""

    podcast: PodcastRepository
    user: UserRepository
    follower: PodcastFollowerRepository

    @property
    def episode(self) -> EpisodeRepository:
        """Episode repository (same instance as podcast repository)."""
        # PodcastRepository implementations also implement EpisodeRepository
        return self.podcast  # type: ignore


def get_database_type(database_url: str) -> str:
    """
    Determine database type from URL.

    Args:
        database_url: Database connection string or path

    Returns:
        'postgresql' or 'sqlite'
    """
    if not database_url:
        return "sqlite"

    # Parse URL scheme
    parsed = urlparse(database_url)

    if parsed.scheme in ("postgresql", "postgres"):
        return "postgresql"
    elif parsed.scheme == "sqlite":
        return "sqlite"
    elif parsed.scheme == "":
        # No scheme = file path = SQLite
        return "sqlite"
    else:
        logger.warning(f"Unknown database scheme '{parsed.scheme}', defaulting to SQLite")
        return "sqlite"


def create_repositories(config: "Config") -> Repositories:
    """
    Create repository instances based on configuration.

    Args:
        config: Application configuration

    Returns:
        Repositories container with all repository instances

    Raises:
        ImportError: If PostgreSQL driver not installed when needed
        ValueError: If database configuration is invalid
    """
    database_url = config.database_url or config.database_path
    db_type = get_database_type(database_url)

    logger.info(f"Initializing {db_type} database backend")

    if db_type == "postgresql":
        return _create_postgres_repositories(database_url)
    else:
        return _create_sqlite_repositories(config.database_path)


def _create_sqlite_repositories(db_path: str) -> Repositories:
    """Create SQLite repository instances."""
    from .sqlite_podcast_follower_repository import SqlitePodcastFollowerRepository
    from .sqlite_podcast_repository import SqlitePodcastRepository
    from .sqlite_user_repository import SqliteUserRepository

    podcast_repo = SqlitePodcastRepository(db_path=db_path)
    user_repo = SqliteUserRepository(db_path=db_path)
    follower_repo = SqlitePodcastFollowerRepository(db_path=db_path)

    logger.info(f"Using SQLite database: {db_path}")

    return Repositories(
        podcast=podcast_repo,
        user=user_repo,
        follower=follower_repo,
    )


def _create_postgres_repositories(database_url: str) -> Repositories:
    """Create PostgreSQL repository instances."""
    try:
        from .postgres_podcast_follower_repository import PostgresPodcastFollowerRepository
        from .postgres_podcast_repository import PostgresPodcastRepository
        from .postgres_user_repository import PostgresUserRepository
    except ImportError as e:
        raise ImportError(
            "PostgreSQL support requires psycopg2. Install with: pip install psycopg2-binary"
        ) from e

    podcast_repo = PostgresPodcastRepository(database_url=database_url)
    user_repo = PostgresUserRepository(database_url=database_url)
    follower_repo = PostgresPodcastFollowerRepository(database_url=database_url)

    # Mask password in log
    parsed = urlparse(database_url)
    safe_url = f"{parsed.scheme}://{parsed.username}:***@{parsed.hostname}:{parsed.port}{parsed.path}"
    logger.info(f"Using PostgreSQL database: {safe_url}")

    return Repositories(
        podcast=podcast_repo,
        user=user_repo,
        follower=follower_repo,
    )
