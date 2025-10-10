# Copyright 2025 thestill.ai
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
