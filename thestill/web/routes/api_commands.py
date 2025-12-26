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
Command API endpoints for thestill.me web UI.

Provides endpoints for executing CLI-like commands (refresh, download, etc.)
with concurrency protection and progress tracking.
"""

import logging
import threading
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from ..dependencies import AppState, get_app_state
from ..task_manager import TaskStatus, TaskType

logger = logging.getLogger(__name__)

router = APIRouter()


# Request/Response models


class RefreshRequest(BaseModel):
    """Request body for the refresh command."""

    podcast_id: Optional[str] = None
    max_episodes: Optional[int] = None
    dry_run: bool = False


class RefreshResponse(BaseModel):
    """Response for a successful refresh command initiation."""

    status: str
    message: str
    task_type: str


class AddPodcastRequest(BaseModel):
    """Request body for the add podcast command."""

    url: str


class AddPodcastResponse(BaseModel):
    """Response for a successful add podcast command initiation."""

    status: str
    message: str
    task_type: str


class TaskStatusResponse(BaseModel):
    """Response for task status queries."""

    task_type: str
    status: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    progress: int = 0
    message: str = ""
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


# Background task functions


def run_add_podcast_task(
    state: AppState,
    url: str,
) -> None:
    """
    Execute the add podcast task in the background.

    This function runs in a separate thread and updates the task manager
    with progress and results.

    Args:
        state: Application state with services
        url: URL of the podcast to add (RSS, Apple Podcasts, or YouTube)
    """
    task_manager = state.task_manager

    try:
        task_manager.update_progress(TaskType.ADD_PODCAST, 10, "Validating URL...")
        task_manager.update_progress(TaskType.ADD_PODCAST, 30, "Fetching podcast feed...")

        # Execute the add podcast
        podcast = state.podcast_service.add_podcast(url)

        if podcast is None:
            task_manager.fail_task(
                TaskType.ADD_PODCAST,
                "Failed to add podcast. It may already exist or the URL may be invalid.",
            )
            return

        task_manager.update_progress(TaskType.ADD_PODCAST, 90, "Finalizing...")

        # Build result summary
        result_data = {
            "podcast_title": podcast.title,
            "podcast_id": podcast.id,
            "rss_url": str(podcast.rss_url),
            "episodes_count": len(podcast.episodes),
        }

        # Complete the task
        message = f"Added podcast: {podcast.title}"
        task_manager.complete_task(TaskType.ADD_PODCAST, result=result_data, message=message)

    except ValueError as e:
        # URL validation error
        task_manager.fail_task(TaskType.ADD_PODCAST, str(e))
    except Exception as e:
        logger.exception("Add podcast task failed")
        task_manager.fail_task(TaskType.ADD_PODCAST, str(e))


def run_refresh_task(
    state: AppState,
    podcast_id: Optional[str] = None,
    max_episodes: Optional[int] = None,
    dry_run: bool = False,
) -> None:
    """
    Execute the refresh task in the background.

    This function runs in a separate thread and updates the task manager
    with progress and results.

    Args:
        state: Application state with services
        podcast_id: Optional podcast ID to filter refresh
        max_episodes: Maximum episodes per podcast
        dry_run: If True, don't persist changes
    """
    task_manager = state.task_manager

    try:
        task_manager.update_progress(TaskType.REFRESH, 10, "Fetching podcast feeds...")

        # Get max_episodes_per_podcast from config if not specified
        max_episodes_per_podcast = max_episodes or state.config.max_episodes_per_podcast

        # Execute the refresh
        result = state.refresh_service.refresh(
            podcast_id=podcast_id,
            max_episodes=max_episodes,
            max_episodes_per_podcast=max_episodes_per_podcast,
            dry_run=dry_run,
        )

        task_manager.update_progress(TaskType.REFRESH, 90, "Processing results...")

        # Build result summary
        result_data = {
            "total_episodes": result.total_episodes,
            "podcasts_refreshed": len(result.episodes_by_podcast),
            "dry_run": dry_run,
            "episodes_by_podcast": [
                {"podcast": podcast.title, "new_episodes": len(episodes)}
                for podcast, episodes in result.episodes_by_podcast
            ],
        }

        if result.podcast_filter_applied:
            result_data["podcast_filter"] = result.podcast_filter_applied

        # Complete the task
        message = f"Discovered {result.total_episodes} new episode(s)"
        if dry_run:
            message += " (dry run)"
        task_manager.complete_task(TaskType.REFRESH, result=result_data, message=message)

    except ValueError as e:
        # Podcast not found or similar validation error
        task_manager.fail_task(TaskType.REFRESH, str(e))
    except Exception as e:
        logger.exception("Refresh task failed")
        task_manager.fail_task(TaskType.REFRESH, str(e))


# API endpoints


@router.post("/refresh", response_model=RefreshResponse)
async def refresh_feeds(
    request: RefreshRequest,
    background_tasks: BackgroundTasks,
    state: AppState = Depends(get_app_state),
) -> RefreshResponse:
    """
    Refresh podcast feeds and discover new episodes.

    This endpoint starts a background refresh task. Only one refresh can run at a time.
    Use GET /api/commands/refresh/status to check progress.

    Args:
        request: Refresh parameters (podcast_id, max_episodes, dry_run)
        background_tasks: FastAPI background tasks
        state: Application state with services

    Returns:
        RefreshResponse with task status

    Raises:
        HTTPException 409: If a refresh is already running
    """
    task_manager = state.task_manager

    # Try to start the task
    task = task_manager.start_task(TaskType.REFRESH, "Initializing refresh...")
    if task is None:
        # Task already running
        current = task_manager.get_task(TaskType.REFRESH)
        raise HTTPException(
            status_code=409,
            detail={
                "error": "Refresh already in progress",
                "started_at": current.started_at.isoformat() if current and current.started_at else None,
                "progress": current.progress if current else 0,
                "message": current.message if current else "",
            },
        )

    # Run refresh in background thread (not async task) for thread-safety with SQLite
    thread = threading.Thread(
        target=run_refresh_task,
        args=(state, request.podcast_id, request.max_episodes, request.dry_run),
        daemon=True,
    )
    thread.start()

    return RefreshResponse(
        status="started",
        message="Refresh task started. Use GET /api/commands/refresh/status to check progress.",
        task_type=TaskType.REFRESH.value,
    )


@router.get("/refresh/status", response_model=TaskStatusResponse)
async def get_refresh_status(
    state: AppState = Depends(get_app_state),
) -> TaskStatusResponse:
    """
    Get the status of the current or last refresh task.

    Returns:
        TaskStatusResponse with current task status, progress, and results
    """
    task_manager = state.task_manager
    task = task_manager.get_task(TaskType.REFRESH)

    if task is None:
        return TaskStatusResponse(
            task_type=TaskType.REFRESH.value,
            status="none",
            message="No refresh has been run yet",
        )

    return TaskStatusResponse(
        task_type=task.task_type.value,
        status=task.status.value,
        started_at=task.started_at.isoformat() if task.started_at else None,
        completed_at=task.completed_at.isoformat() if task.completed_at else None,
        progress=task.progress,
        message=task.message,
        result=task.result,
        error=task.error,
    )


@router.get("/status")
async def get_all_tasks_status(
    state: AppState = Depends(get_app_state),
) -> Dict[str, Any]:
    """
    Get the status of all tracked command tasks.

    Returns:
        Dictionary mapping task types to their status
    """
    task_manager = state.task_manager
    return {
        "tasks": task_manager.get_all_tasks(),
    }


# Add Podcast endpoints


@router.post("/add", response_model=AddPodcastResponse)
async def add_podcast(
    request: AddPodcastRequest,
    state: AppState = Depends(get_app_state),
) -> AddPodcastResponse:
    """
    Add a new podcast to tracking.

    This endpoint starts a background task to add a podcast. Only one add can run at a time.
    Use GET /api/commands/add/status to check progress.

    Args:
        request: Add podcast parameters (url)
        state: Application state with services

    Returns:
        AddPodcastResponse with task status

    Raises:
        HTTPException 409: If an add podcast task is already running
    """
    task_manager = state.task_manager

    # Try to start the task
    task = task_manager.start_task(TaskType.ADD_PODCAST, "Initializing...")
    if task is None:
        # Task already running
        current = task_manager.get_task(TaskType.ADD_PODCAST)
        raise HTTPException(
            status_code=409,
            detail={
                "error": "Add podcast already in progress",
                "started_at": current.started_at.isoformat() if current and current.started_at else None,
                "progress": current.progress if current else 0,
                "message": current.message if current else "",
            },
        )

    # Run add podcast in background thread for thread-safety with SQLite
    thread = threading.Thread(
        target=run_add_podcast_task,
        args=(state, request.url),
        daemon=True,
    )
    thread.start()

    return AddPodcastResponse(
        status="started",
        message="Add podcast task started. Use GET /api/commands/add/status to check progress.",
        task_type=TaskType.ADD_PODCAST.value,
    )


@router.get("/add/status", response_model=TaskStatusResponse)
async def get_add_podcast_status(
    state: AppState = Depends(get_app_state),
) -> TaskStatusResponse:
    """
    Get the status of the current or last add podcast task.

    Returns:
        TaskStatusResponse with current task status, progress, and results
    """
    task_manager = state.task_manager
    task = task_manager.get_task(TaskType.ADD_PODCAST)

    if task is None:
        return TaskStatusResponse(
            task_type=TaskType.ADD_PODCAST.value,
            status="none",
            message="No add podcast task has been run yet",
        )

    return TaskStatusResponse(
        task_type=task.task_type.value,
        status=task.status.value,
        started_at=task.started_at.isoformat() if task.started_at else None,
        completed_at=task.completed_at.isoformat() if task.completed_at else None,
        progress=task.progress,
        message=task.message,
        result=task.result,
        error=task.error,
    )
