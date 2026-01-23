"""
Repository layer for data persistence.

This module provides abstract interfaces and concrete implementations
for data access, following the Repository Pattern to separate
business logic from persistence concerns.

Supports both SQLite (local development) and PostgreSQL (cloud deployment).
Use create_repositories() factory function to get the appropriate backend.
"""

from .database import Repositories, create_repositories, get_database_type
from .podcast_follower_repository import PodcastFollowerRepository
from .podcast_repository import EpisodeRepository, PodcastRepository
from .sqlite_podcast_follower_repository import SqlitePodcastFollowerRepository
from .sqlite_podcast_repository import SqlitePodcastRepository
from .user_repository import UserRepository

__all__ = [
    # Factory
    "create_repositories",
    "get_database_type",
    "Repositories",
    # Interfaces
    "PodcastRepository",
    "EpisodeRepository",
    "PodcastFollowerRepository",
    "UserRepository",
    # SQLite implementations
    "SqlitePodcastRepository",
    "SqlitePodcastFollowerRepository",
]
