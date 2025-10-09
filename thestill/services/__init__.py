"""
Service layer for thestill.ai

This package contains business logic services that can be used by multiple
presentation layers (CLI, MCP server, web API, etc.)
"""

from .podcast_service import PodcastService, PodcastWithIndex, EpisodeWithIndex
from .stats_service import StatsService, SystemStats

__all__ = [
    'PodcastService',
    'PodcastWithIndex',
    'EpisodeWithIndex',
    'StatsService',
    'SystemStats',
]
