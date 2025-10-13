"""
Repository layer for data persistence.

This module provides abstract interfaces and concrete implementations
for data access, following the Repository Pattern to separate
business logic from persistence concerns.
"""

from .json_podcast_repository import JsonPodcastRepository
from .podcast_repository import EpisodeRepository, PodcastRepository

__all__ = [
    "PodcastRepository",
    "EpisodeRepository",
    "JsonPodcastRepository",
]
