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
FastAPI dependency injection for thestill.me web server.

This module provides dependency functions and the AppState class for
injecting services into route handlers.

Usage:
    from fastapi import Depends
    from thestill.web.dependencies import get_app_state, AppState

    @router.get("/podcasts")
    async def list_podcasts(state: AppState = Depends(get_app_state)):
        return state.podcast_service.list_podcasts()
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import Request

if TYPE_CHECKING:
    from ..core.feed_manager import PodcastFeedManager
    from ..core.progress_store import ProgressStore
    from ..core.queue_manager import QueueManager
    from ..core.task_worker import TaskWorker
    from ..repositories.sqlite_podcast_repository import SqlitePodcastRepository
    from ..services import PodcastService, RefreshService, StatsService
    from ..utils.config import Config
    from ..utils.path_manager import PathManager
    from .task_manager import TaskManager


@dataclass
class AppState:
    """
    Application state container for dependency injection.

    This class mirrors the CLIContext pattern from cli.py, providing
    typed access to all shared services and configuration.

    Attributes:
        config: Application configuration
        path_manager: Centralized path management
        repository: SQLite podcast repository
        feed_manager: Feed manager for RSS operations
        podcast_service: Podcast management service
        refresh_service: Refresh service for feed discovery
        stats_service: Statistics service
        task_manager: Task manager for long-running operations
        queue_manager: SQLite task queue manager
        task_worker: Background task worker
        progress_store: In-memory progress store for real-time updates
    """

    config: "Config"
    path_manager: "PathManager"
    repository: "SqlitePodcastRepository"
    feed_manager: "PodcastFeedManager"
    podcast_service: "PodcastService"
    refresh_service: "RefreshService"
    stats_service: "StatsService"
    task_manager: "TaskManager"
    queue_manager: "QueueManager"
    task_worker: "TaskWorker"
    progress_store: "ProgressStore"


def get_app_state(request: Request) -> AppState:
    """
    FastAPI dependency to get the application state.

    This function retrieves the AppState from the request's app instance,
    allowing route handlers to access services via dependency injection.

    Args:
        request: FastAPI request object

    Returns:
        AppState instance with all services

    Example:
        @router.get("/status")
        async def get_status(state: AppState = Depends(get_app_state)):
            return state.stats_service.get_stats()
    """
    return request.app.state.app_state
