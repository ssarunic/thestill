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
from typing import TYPE_CHECKING, Optional

from fastapi import Depends, HTTPException, Request

if TYPE_CHECKING:
    from ..core.feed_manager import PodcastFeedManager
    from ..core.progress_store import ProgressStore
    from ..core.queue_manager import QueueManager
    from ..core.task_worker import TaskWorker
    from ..models.user import User
    from ..repositories.digest_repository import DigestRepository
    from ..repositories.podcast_follower_repository import PodcastFollowerRepository
    from ..repositories.sqlite_podcast_repository import SqlitePodcastRepository
    from ..repositories.user_repository import UserRepository
    from ..services import FollowerService, PodcastService, RefreshService, StatsService
    from ..services.auth_service import AuthService
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
        user_repository: User persistence repository
        auth_service: Authentication service
        follower_repository: Podcast follower relationship repository
        follower_service: Follower management service
        digest_repository: Digest persistence repository
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
    user_repository: "UserRepository"
    auth_service: "AuthService"
    follower_repository: "PodcastFollowerRepository"
    follower_service: "FollowerService"
    digest_repository: "DigestRepository"


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


# Cookie name for authentication token
AUTH_COOKIE_NAME = "auth_token"


def _get_token_from_request(request: Request) -> Optional[str]:
    """Extract auth token from cookie or Authorization header."""
    # First try cookie
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if token:
        return token

    # Fall back to Authorization header (for API clients)
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:]

    return None


def get_current_user(
    request: Request,
    state: AppState = Depends(get_app_state),
) -> Optional["User"]:
    """
    FastAPI dependency to get the current user (optional).

    In single-user mode, always returns the default user.
    In multi-user mode, returns the authenticated user or None.

    Args:
        request: FastAPI request object
        state: Application state

    Returns:
        User if authenticated, None otherwise

    Example:
        @router.get("/items")
        async def list_items(user: Optional[User] = Depends(get_current_user)):
            if user:
                return {"items": [...], "user": user.email}
            return {"items": [...]}
    """
    token = _get_token_from_request(request)
    return state.auth_service.get_current_user(token)


def require_auth(
    request: Request,
    state: AppState = Depends(get_app_state),
) -> "User":
    """
    FastAPI dependency that requires authentication.

    In single-user mode, always returns the default user.
    In multi-user mode, requires a valid JWT token.

    Args:
        request: FastAPI request object
        state: Application state

    Returns:
        Authenticated User

    Raises:
        HTTPException: 401 if not authenticated in multi-user mode

    Example:
        @router.post("/items")
        async def create_item(user: User = Depends(require_auth)):
            return {"created_by": user.email}
    """
    token = _get_token_from_request(request)
    user = state.auth_service.get_current_user(token)

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user
