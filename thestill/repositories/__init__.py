"""
Repository layer for data persistence.

This module provides abstract interfaces and concrete implementations
for data access, following the Repository Pattern to separate
business logic from persistence concerns.
"""

from .podcast_repository import EpisodeRepository, PodcastRepository
from .sqlite_podcast_repository import SqlitePodcastRepository

__all__ = [
    "PodcastRepository",
    "EpisodeRepository",
    "SqlitePodcastRepository",
]
