"""
Repository layer for data persistence.

This module provides abstract interfaces and concrete implementations
for data access, following the Repository Pattern to separate
business logic from persistence concerns.
"""

from .digest_repository import DigestRepository
from .podcast_follower_repository import PodcastFollowerRepository
from .podcast_repository import EpisodeRepository, PodcastRepository
from .sqlite_digest_repository import SqliteDigestRepository
from .sqlite_podcast_follower_repository import SqlitePodcastFollowerRepository
from .sqlite_podcast_repository import SqlitePodcastRepository

__all__ = [
    "DigestRepository",
    "EpisodeRepository",
    "PodcastFollowerRepository",
    "PodcastRepository",
    "SqliteDigestRepository",
    "SqlitePodcastFollowerRepository",
    "SqlitePodcastRepository",
]
