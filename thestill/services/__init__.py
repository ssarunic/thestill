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
Service layer for thestill.me

This package contains business logic services that can be used by multiple
presentation layers (CLI, MCP server, web API, etc.)
"""

from .batch_processor import BatchQueueResult, BatchQueueService, QueuedEpisode
from .digest_generator import DigestContent, DigestEpisodeInfo, DigestGenerator, DigestStats
from .digest_selector import DigestEpisodeSelector, DigestSelectionCriteria, DigestSelectionResult
from .follower_service import (
    AlreadyFollowingError,
    FollowerService,
    FollowerServiceError,
    NotFollowingError,
    PodcastNotFoundError,
)
from .podcast_service import EpisodeWithIndex, PodcastService, PodcastWithIndex
from .refresh_service import RefreshResult, RefreshService
from .stats_service import ActivityItem, StatsService, SystemStats

__all__ = [
    "BatchQueueResult",
    "BatchQueueService",
    "QueuedEpisode",
    "DigestContent",
    "DigestEpisodeInfo",
    "DigestGenerator",
    "DigestStats",
    "DigestEpisodeSelector",
    "DigestSelectionCriteria",
    "DigestSelectionResult",
    "PodcastService",
    "PodcastWithIndex",
    "EpisodeWithIndex",
    "RefreshService",
    "RefreshResult",
    "StatsService",
    "SystemStats",
    "ActivityItem",
    "FollowerService",
    "FollowerServiceError",
    "AlreadyFollowingError",
    "NotFollowingError",
    "PodcastNotFoundError",
]
