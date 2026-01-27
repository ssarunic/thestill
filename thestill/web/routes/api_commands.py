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

import asyncio
import json
import threading
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from structlog import get_logger

from ...core.queue_manager import QueueManager, Task, TaskStage
from ...core.queue_manager import TaskStatus as QueueTaskStatus
from ...core.queue_manager import get_next_stage
from ...models.podcast import EpisodeState
from ...models.user import User
from ..dependencies import AppState, get_app_state, require_auth
from ..task_manager import TaskStatus, TaskType

logger = get_logger(__name__)

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
    user_id: str,
) -> None:
    """
    Execute the add podcast task in the background.

    This function runs in a separate thread and updates the task manager
    with progress and results. After adding the podcast, it automatically
    refreshes the feed to discover episodes and follows the podcast for the user.

    Args:
        state: Application state with services
        url: URL of the podcast to add (RSS, Apple Podcasts, or YouTube)
        user_id: ID of the user who is adding/following the podcast
    """
    task_manager = state.task_manager

    try:
        task_manager.update_progress(TaskType.ADD_PODCAST, 10, "Validating URL...")
        task_manager.update_progress(TaskType.ADD_PODCAST, 30, "Fetching podcast feed...")

        # Get count of podcasts before adding (to detect if it's new)
        podcasts_before = len(state.repository.get_all())

        # Execute the add podcast (returns existing if already exists)
        podcast = state.podcast_service.add_podcast(url)

        if podcast is None:
            task_manager.fail_task(
                TaskType.ADD_PODCAST,
                "Failed to add podcast. The URL may be invalid.",
            )
            return

        # Check if this was a newly added podcast or an existing one
        podcasts_after = len(state.repository.get_all())
        is_new_podcast = podcasts_after > podcasts_before

        if is_new_podcast:
            task_manager.update_progress(TaskType.ADD_PODCAST, 60, "Discovering episodes...")

            # Refresh only for newly added podcasts to discover episodes
            max_episodes_per_podcast = state.config.max_episodes_per_podcast
            result = state.refresh_service.refresh(
                podcast_id=str(podcast.id),
                max_episodes_per_podcast=max_episodes_per_podcast,
            )
        else:
            # Podcast already exists - follow immediately (skip slow refresh)
            task_manager.update_progress(TaskType.ADD_PODCAST, 50, "Following existing podcast...")

            # Create a dummy result for the response (no refresh needed for existing podcast)
            from thestill.services.refresh_service import RefreshResult

            result = RefreshResult(total_episodes=0, episodes_by_podcast=[])

            logger.info(f"Re-following existing podcast: {podcast.title}")

        task_manager.update_progress(TaskType.ADD_PODCAST, 80, "Following podcast...")

        # Auto-follow the podcast for the user
        try:
            logger.info(f"Attempting to follow podcast {podcast.id} for user {user_id}")
            state.follower_service.follow(user_id, podcast.id)
            followed = True
            logger.info(f"Successfully followed podcast {podcast.id}")
        except Exception as follow_error:
            # May already be following - that's fine
            logger.warning(f"Follow error (may already be following): {follow_error}")
            followed = state.follower_service.is_following(user_id, podcast.id)

        task_manager.update_progress(TaskType.ADD_PODCAST, 90, "Finalizing...")

        # Re-fetch podcast to get updated episode count
        updated_podcast = state.repository.get_by_url(str(podcast.rss_url))
        episodes_count = len(updated_podcast.episodes) if updated_podcast else 0

        # Build result summary
        result_data = {
            "podcast_title": podcast.title,
            "podcast_id": podcast.id,
            "podcast_slug": podcast.slug,
            "rss_url": str(podcast.rss_url),
            "episodes_count": episodes_count,
            "episodes_discovered": result.total_episodes,
            "is_following": followed,
        }

        # Complete the task
        message = f"Following: {podcast.title} ({episodes_count} episodes)"
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
        task_manager.update_progress(TaskType.REFRESH, 5, "Starting feed refresh...")

        # Get max_episodes_per_podcast from config if not specified
        max_episodes_per_podcast = max_episodes or state.config.max_episodes_per_podcast

        # Progress callback that maps podcast iteration to 5-90% range
        def on_progress(current_idx: int, total: int, podcast_title: str) -> None:
            if total > 0:
                # Map progress from 5% to 90% (leaving room for start/finish)
                pct = 5 + int((current_idx / total) * 85)
                task_manager.update_progress(
                    TaskType.REFRESH, pct, f"Fetching {podcast_title} ({current_idx + 1}/{total})..."
                )

        # Execute the refresh
        result = state.refresh_service.refresh(
            podcast_id=podcast_id,
            max_episodes=max_episodes,
            max_episodes_per_podcast=max_episodes_per_podcast,
            dry_run=dry_run,
            progress_callback=on_progress,
        )

        task_manager.update_progress(TaskType.REFRESH, 95, "Processing results...")

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
    user: User = Depends(require_auth),
) -> AddPodcastResponse:
    """
    Add a new podcast to tracking and follow it.

    This endpoint starts a background task to add a podcast and automatically
    follow it for the current user. Only one add can run at a time.
    Use GET /api/commands/add/status to check progress.

    Requires authentication.

    Args:
        request: Add podcast parameters (url)
        state: Application state with services
        user: Authenticated user

    Returns:
        AddPodcastResponse with task status

    Raises:
        HTTPException 401: If not authenticated
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
        args=(state, request.url, user.id),
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


# ============================================================================
# Queue-based Pipeline Task Endpoints
# ============================================================================


class QueueTaskRequest(BaseModel):
    """Request body for queuing a pipeline task."""

    podcast_slug: str
    episode_slug: str


class QueueTaskResponse(BaseModel):
    """Response for task queue operations."""

    task_id: str
    status: str
    message: str
    stage: str
    episode_id: str
    episode_title: str


class QueuedTaskStatusResponse(BaseModel):
    """Response for queued task status queries."""

    task_id: str
    episode_id: str
    stage: str
    status: str
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class QueueStatusResponse(BaseModel):
    """Response for queue status queries."""

    pending_count: int
    worker_running: bool
    current_task: Optional[Dict[str, Any]] = None
    stats: Dict[str, int]


class RunPipelineRequest(BaseModel):
    """Request body for running the full pipeline."""

    podcast_slug: str
    episode_slug: str
    target_state: str = "summarized"  # Target state to reach (default: full pipeline)


class RunPipelineResponse(BaseModel):
    """Response for pipeline execution."""

    task_id: str
    status: str
    message: str
    starting_stage: str
    target_state: str
    episode_id: str
    episode_title: str


def _get_starting_stage(episode_state: EpisodeState) -> Optional[TaskStage]:
    """
    Get the next pipeline stage for an episode based on its current state.

    Args:
        episode_state: Current episode state

    Returns:
        Next TaskStage to execute, or None if episode is already summarized
    """
    state_to_stage = {
        EpisodeState.DISCOVERED: TaskStage.DOWNLOAD,
        EpisodeState.DOWNLOADED: TaskStage.DOWNSAMPLE,
        EpisodeState.DOWNSAMPLED: TaskStage.TRANSCRIBE,
        EpisodeState.TRANSCRIBED: TaskStage.CLEAN,
        EpisodeState.CLEANED: TaskStage.SUMMARIZE,
        EpisodeState.SUMMARIZED: None,  # Already at final state
    }
    return state_to_stage.get(episode_state)


def _validate_episode_for_stage(
    state: AppState, podcast_slug: str, episode_slug: str, required_state: EpisodeState, stage: TaskStage
) -> tuple:
    """
    Validate episode exists and is in the correct state for the requested stage.

    Args:
        state: Application state
        podcast_slug: Podcast slug
        episode_slug: Episode slug
        required_state: Required episode state for this operation
        stage: Pipeline stage being requested

    Returns:
        Tuple of (podcast, episode)

    Raises:
        HTTPException: If validation fails
    """
    # Get podcast and episode by slugs
    result = state.repository.get_episode_by_slug(podcast_slug, episode_slug)
    if not result:
        raise HTTPException(status_code=404, detail=f"Episode not found: {podcast_slug}/{episode_slug}")

    podcast, episode = result

    # Validate state
    if episode.state != required_state:
        raise HTTPException(
            status_code=400,
            detail=f"Episode is in {episode.state.value} state, expected {required_state.value} for {stage.value}",
        )

    # Check for existing pending/processing task
    if state.queue_manager.has_pending_task(episode.id, stage):
        raise HTTPException(
            status_code=409,
            detail=f"A {stage.value} task is already queued or processing for this episode",
        )

    return podcast, episode


@router.post("/download", response_model=QueueTaskResponse)
async def queue_download(
    request: QueueTaskRequest,
    state: AppState = Depends(get_app_state),
) -> QueueTaskResponse:
    """
    Queue a download task for an episode.

    The episode must be in DISCOVERED state.

    Args:
        request: Podcast and episode slugs
        state: Application state

    Returns:
        QueueTaskResponse with task ID

    Raises:
        HTTPException 404: If podcast or episode not found
        HTTPException 400: If episode is not in DISCOVERED state
        HTTPException 409: If task already queued
    """
    podcast, episode = _validate_episode_for_stage(
        state, request.podcast_slug, request.episode_slug, EpisodeState.DISCOVERED, TaskStage.DOWNLOAD
    )

    task = state.queue_manager.add_task(episode.id, TaskStage.DOWNLOAD)

    return QueueTaskResponse(
        task_id=task.id,
        status="queued",
        message=f"Download queued for {episode.title}",
        stage=TaskStage.DOWNLOAD.value,
        episode_id=episode.id,
        episode_title=episode.title,
    )


@router.post("/downsample", response_model=QueueTaskResponse)
async def queue_downsample(
    request: QueueTaskRequest,
    state: AppState = Depends(get_app_state),
) -> QueueTaskResponse:
    """
    Queue a downsample task for an episode.

    The episode must be in DOWNLOADED state.
    """
    podcast, episode = _validate_episode_for_stage(
        state, request.podcast_slug, request.episode_slug, EpisodeState.DOWNLOADED, TaskStage.DOWNSAMPLE
    )

    task = state.queue_manager.add_task(episode.id, TaskStage.DOWNSAMPLE)

    return QueueTaskResponse(
        task_id=task.id,
        status="queued",
        message=f"Downsample queued for {episode.title}",
        stage=TaskStage.DOWNSAMPLE.value,
        episode_id=episode.id,
        episode_title=episode.title,
    )


@router.post("/transcribe", response_model=QueueTaskResponse)
async def queue_transcribe(
    request: QueueTaskRequest,
    state: AppState = Depends(get_app_state),
) -> QueueTaskResponse:
    """
    Queue a transcription task for an episode.

    The episode must be in DOWNSAMPLED state.
    """
    podcast, episode = _validate_episode_for_stage(
        state, request.podcast_slug, request.episode_slug, EpisodeState.DOWNSAMPLED, TaskStage.TRANSCRIBE
    )

    task = state.queue_manager.add_task(episode.id, TaskStage.TRANSCRIBE)

    return QueueTaskResponse(
        task_id=task.id,
        status="queued",
        message=f"Transcription queued for {episode.title}",
        stage=TaskStage.TRANSCRIBE.value,
        episode_id=episode.id,
        episode_title=episode.title,
    )


@router.post("/clean", response_model=QueueTaskResponse)
async def queue_clean(
    request: QueueTaskRequest,
    state: AppState = Depends(get_app_state),
) -> QueueTaskResponse:
    """
    Queue a transcript cleaning task for an episode.

    The episode must be in TRANSCRIBED state.
    """
    podcast, episode = _validate_episode_for_stage(
        state, request.podcast_slug, request.episode_slug, EpisodeState.TRANSCRIBED, TaskStage.CLEAN
    )

    task = state.queue_manager.add_task(episode.id, TaskStage.CLEAN)

    return QueueTaskResponse(
        task_id=task.id,
        status="queued",
        message=f"Transcript cleaning queued for {episode.title}",
        stage=TaskStage.CLEAN.value,
        episode_id=episode.id,
        episode_title=episode.title,
    )


@router.post("/summarize", response_model=QueueTaskResponse)
async def queue_summarize(
    request: QueueTaskRequest,
    state: AppState = Depends(get_app_state),
) -> QueueTaskResponse:
    """
    Queue a summarization task for an episode.

    The episode must be in CLEANED state.
    """
    podcast, episode = _validate_episode_for_stage(
        state, request.podcast_slug, request.episode_slug, EpisodeState.CLEANED, TaskStage.SUMMARIZE
    )

    task = state.queue_manager.add_task(episode.id, TaskStage.SUMMARIZE)

    return QueueTaskResponse(
        task_id=task.id,
        status="queued",
        message=f"Summarization queued for {episode.title}",
        stage=TaskStage.SUMMARIZE.value,
        episode_id=episode.id,
        episode_title=episode.title,
    )


class CancelPipelineResponse(BaseModel):
    """Response for pipeline cancellation."""

    status: str
    message: str
    episode_id: str
    cancelled_tasks: int


@router.post("/run-pipeline", response_model=RunPipelineResponse)
async def run_pipeline(
    request: RunPipelineRequest,
    state: AppState = Depends(get_app_state),
) -> RunPipelineResponse:
    """
    Run the full pipeline for an episode from its current state to completion.

    This endpoint queues the next required stage for an episode and sets metadata
    to automatically chain-enqueue subsequent stages until the target state is reached.

    For example, if an episode is in DISCOVERED state with target_state="summarized":
    - Queues DOWNLOAD task with run_full_pipeline=True
    - When DOWNLOAD completes, automatically queues DOWNSAMPLE
    - Continues until SUMMARIZE completes or a task fails

    Args:
        request: Podcast slug, episode slug, and optional target state
        state: Application state

    Returns:
        RunPipelineResponse with first task ID and pipeline info

    Raises:
        HTTPException 404: If podcast or episode not found
        HTTPException 400: If episode is already at or past target state
        HTTPException 409: If a task is already queued/processing for this episode
    """
    # Get podcast and episode by slugs
    result = state.repository.get_episode_by_slug(request.podcast_slug, request.episode_slug)
    if not result:
        raise HTTPException(status_code=404, detail=f"Episode not found: {request.podcast_slug}/{request.episode_slug}")

    podcast, episode = result

    # Determine starting stage based on current episode state
    starting_stage = _get_starting_stage(episode.state)

    if starting_stage is None:
        raise HTTPException(
            status_code=400,
            detail=f"Episode is already in {episode.state.value} state (fully processed)",
        )

    # Validate target_state
    valid_target_states = ["downloaded", "downsampled", "transcribed", "cleaned", "summarized"]
    if request.target_state not in valid_target_states:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid target_state: {request.target_state}. Must be one of: {valid_target_states}",
        )

    # Check if target state is achievable (not before current state)
    state_order = ["discovered", "downloaded", "downsampled", "transcribed", "cleaned", "summarized"]
    current_idx = state_order.index(episode.state.value)
    target_idx = state_order.index(request.target_state)

    if target_idx <= current_idx:
        raise HTTPException(
            status_code=400,
            detail=f"Episode is already at {episode.state.value} state, cannot reach {request.target_state}",
        )

    # Check for existing pending/processing task for starting stage
    if state.queue_manager.has_pending_task(episode.id, starting_stage):
        raise HTTPException(
            status_code=409,
            detail=f"A {starting_stage.value} task is already queued or processing for this episode",
        )

    # Create task with run_full_pipeline metadata
    metadata = {
        "run_full_pipeline": True,
        "target_state": request.target_state,
        "initiated_by": "api",
    }

    task = state.queue_manager.add_task(
        episode_id=episode.id,
        stage=starting_stage,
        metadata=metadata,
    )

    return RunPipelineResponse(
        task_id=task.id,
        status="queued",
        message=f"Pipeline started for {episode.title}: {starting_stage.value} → {request.target_state}",
        starting_stage=starting_stage.value,
        target_state=request.target_state,
        episode_id=episode.id,
        episode_title=episode.title,
    )


@router.post("/episode/{episode_id}/cancel-pipeline", response_model=CancelPipelineResponse)
async def cancel_pipeline(
    episode_id: str,
    state: AppState = Depends(get_app_state),
) -> CancelPipelineResponse:
    """
    Cancel all pending/scheduled pipeline tasks for an episode.

    This stops any running pipeline by cancelling all pending and retry_scheduled tasks.
    Tasks that are currently processing will complete, but no further stages will be queued.

    Args:
        episode_id: ID of the episode whose pipeline to cancel

    Returns:
        CancelPipelineResponse with count of cancelled tasks

    Raises:
        HTTPException 404: If episode not found
    """
    # Verify episode exists
    result = state.repository.get_episode(episode_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Episode not found: {episode_id}")

    _, episode = result

    # Get all tasks for this episode
    tasks = state.queue_manager.get_tasks_for_episode(episode_id)

    # Cancel pending and retry_scheduled tasks
    cancelled_count = 0
    for task in tasks:
        if task.status in (QueueTaskStatus.PENDING, QueueTaskStatus.RETRY_SCHEDULED):
            # Mark as failed with cancellation message
            state.queue_manager.fail_task(task.id, "Pipeline cancelled by user")
            cancelled_count += 1

    return CancelPipelineResponse(
        status="ok",
        message=f"Cancelled {cancelled_count} pending task(s) for {episode.title}",
        episode_id=episode_id,
        cancelled_tasks=cancelled_count,
    )


@router.get("/task/{task_id}", response_model=QueuedTaskStatusResponse)
async def get_queued_task_status(
    task_id: str,
    state: AppState = Depends(get_app_state),
) -> QueuedTaskStatusResponse:
    """
    Get the status of a queued task.

    Args:
        task_id: ID of the task to check

    Returns:
        QueuedTaskStatusResponse with task details

    Raises:
        HTTPException 404: If task not found
    """
    task = state.queue_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    return QueuedTaskStatusResponse(
        task_id=task.id,
        episode_id=task.episode_id,
        stage=task.stage.value,
        status=task.status.value,
        error_message=task.error_message,
        created_at=task.created_at.isoformat() if task.created_at else None,
        updated_at=task.updated_at.isoformat() if task.updated_at else None,
        started_at=task.started_at.isoformat() if task.started_at else None,
        completed_at=task.completed_at.isoformat() if task.completed_at else None,
    )


@router.get("/queue/status", response_model=QueueStatusResponse)
async def get_queue_status(
    state: AppState = Depends(get_app_state),
) -> QueueStatusResponse:
    """
    Get the overall queue and worker status.

    Returns:
        QueueStatusResponse with queue statistics and worker info
    """
    current_task = state.task_worker.get_current_task()

    return QueueStatusResponse(
        pending_count=state.queue_manager.get_pending_count(),
        worker_running=state.task_worker.is_running(),
        current_task=current_task.to_dict() if current_task else None,
        stats=state.queue_manager.get_queue_stats(),
    )


@router.get("/episode/{episode_id}/tasks")
async def get_episode_tasks(
    episode_id: str,
    state: AppState = Depends(get_app_state),
) -> Dict[str, Any]:
    """
    Get all tasks for a specific episode.

    Args:
        episode_id: ID of the episode

    Returns:
        Dictionary with episode tasks
    """
    tasks = state.queue_manager.get_tasks_for_episode(episode_id)

    return {
        "episode_id": episode_id,
        "tasks": [task.to_dict() for task in tasks],
    }


@router.get("/task/{task_id}/progress")
async def stream_task_progress(
    task_id: str,
    state: AppState = Depends(get_app_state),
) -> StreamingResponse:
    """
    Stream real-time progress updates for a task via Server-Sent Events (SSE).

    This endpoint provides real-time progress updates during transcription
    tasks. It's particularly useful for WhisperX transcription where
    progress can be tracked through multiple stages (loading, transcribing,
    aligning, diarizing, formatting).

    Args:
        task_id: ID of the task to monitor

    Returns:
        StreamingResponse with SSE events in the format:
        ```
        data: {"stage": "diarizing", "progress_pct": 75, "message": "..."}
        ```

    Raises:
        HTTPException 404: If task not found

    Example client usage (JavaScript):
        ```javascript
        const eventSource = new EventSource('/api/commands/task/{task_id}/progress');
        eventSource.onmessage = (event) => {
            const progress = JSON.parse(event.data);
            updateProgressBar(progress.progress_pct);
            updateStageLabel(progress.stage);
        };
        ```
    """
    # Verify task exists
    task = state.queue_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    async def event_generator():
        """Generate SSE events for progress updates."""
        queue = state.progress_store.subscribe(task_id)

        try:
            while True:
                try:
                    # Wait for progress update with timeout for keepalive
                    progress = await asyncio.wait_for(queue.get(), timeout=30.0)

                    # Send progress event
                    event_data = json.dumps(progress.to_dict())
                    yield f"data: {event_data}\n\n"

                    # Check if task is complete or failed
                    if progress.stage in ("completed", "failed"):
                        break

                except asyncio.TimeoutError:
                    # Send keepalive comment to maintain connection
                    yield ": keepalive\n\n"

                    # Check if task is still processing
                    current_task = state.queue_manager.get_task(task_id)
                    if current_task and current_task.status.value in ("completed", "failed"):
                        # Task finished but we didn't get the final progress update
                        yield f"data: {json.dumps({'stage': current_task.status.value, 'progress_pct': 100 if current_task.status.value == 'completed' else 0, 'message': current_task.error_message or 'Task finished'})}\n\n"
                        break

        except asyncio.CancelledError:
            # Client disconnected
            logger.debug(f"SSE connection closed for task {task_id}")

        finally:
            state.progress_store.unsubscribe(task_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


@router.get("/task/{task_id}/progress/current")
async def get_current_progress(
    task_id: str,
    state: AppState = Depends(get_app_state),
) -> Dict[str, Any]:
    """
    Get the current progress for a task (non-streaming).

    This is a fallback endpoint for clients that don't support SSE.
    It returns the latest known progress for a task.

    Args:
        task_id: ID of the task

    Returns:
        Dictionary with current progress or task status

    Raises:
        HTTPException 404: If task not found
    """
    task = state.queue_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    # Try to get progress from store
    progress = state.progress_store.get(task_id)

    if progress:
        return {
            "task_id": task_id,
            "has_progress": True,
            **progress.to_dict(),
        }

    # No progress available - return task status
    return {
        "task_id": task_id,
        "has_progress": False,
        "stage": task.status.value,
        "progress_pct": 100 if task.status.value == "completed" else 0,
        "message": task.error_message or f"Task status: {task.status.value}",
        "estimated_remaining_seconds": None,
    }


# ============================================================================
# Dead Letter Queue (DLQ) Endpoints
# ============================================================================


class DLQTaskResponse(BaseModel):
    """Response for a single DLQ task with episode info."""

    task_id: str
    episode_id: str
    episode_title: str
    episode_slug: str
    podcast_title: str
    podcast_slug: str
    stage: str
    error_message: Optional[str] = None
    error_type: Optional[str] = None
    retry_count: int
    max_retries: int
    created_at: Optional[str] = None
    completed_at: Optional[str] = None


class DLQListResponse(BaseModel):
    """Response for listing DLQ tasks."""

    status: str
    tasks: list[DLQTaskResponse]
    count: int


class DLQActionResponse(BaseModel):
    """Response for DLQ actions (retry, skip)."""

    status: str
    message: str
    task_id: str
    new_status: str


class DLQBulkRetryRequest(BaseModel):
    """Request body for bulk retry of DLQ tasks."""

    task_ids: Optional[list[str]] = None  # If None, retry all


class DLQBulkRetryResponse(BaseModel):
    """Response for bulk retry of DLQ tasks."""

    status: str
    retried: int
    skipped: int
    task_ids: list[str]


@router.get("/dlq", response_model=DLQListResponse)
async def list_dlq_tasks(
    limit: int = 100,
    state: AppState = Depends(get_app_state),
) -> DLQListResponse:
    """
    List tasks in the Dead Letter Queue (status='dead').

    These are tasks that failed with fatal errors that will not automatically retry.
    They need manual intervention - either retry after fixing the issue, or skip.

    Args:
        limit: Maximum number of tasks to return (default 100)

    Returns:
        DLQListResponse with list of dead tasks and their episode info
    """
    dead_tasks = state.queue_manager.get_dead_tasks(limit=limit)

    tasks_with_info = []
    for task in dead_tasks:
        # Get episode and podcast info
        result = state.repository.get_episode(task.episode_id)
        if result:
            podcast, episode = result
            tasks_with_info.append(
                DLQTaskResponse(
                    task_id=task.id,
                    episode_id=task.episode_id,
                    episode_title=episode.title,
                    episode_slug=episode.slug,
                    podcast_title=podcast.title,
                    podcast_slug=podcast.slug,
                    stage=task.stage.value,
                    error_message=task.error_message,
                    error_type=task.error_type.value if task.error_type else None,
                    retry_count=task.retry_count,
                    max_retries=task.max_retries,
                    created_at=task.created_at.isoformat() if task.created_at else None,
                    completed_at=task.completed_at.isoformat() if task.completed_at else None,
                )
            )
        else:
            # Episode not found - still include task but with placeholder info
            tasks_with_info.append(
                DLQTaskResponse(
                    task_id=task.id,
                    episode_id=task.episode_id,
                    episode_title="[Episode not found]",
                    episode_slug="",
                    podcast_title="[Unknown]",
                    podcast_slug="",
                    stage=task.stage.value,
                    error_message=task.error_message,
                    error_type=task.error_type.value if task.error_type else None,
                    retry_count=task.retry_count,
                    max_retries=task.max_retries,
                    created_at=task.created_at.isoformat() if task.created_at else None,
                    completed_at=task.completed_at.isoformat() if task.completed_at else None,
                )
            )

    return DLQListResponse(
        status="ok",
        tasks=tasks_with_info,
        count=len(tasks_with_info),
    )


@router.post("/dlq/{task_id}/retry", response_model=DLQActionResponse)
async def retry_dlq_task(
    task_id: str,
    state: AppState = Depends(get_app_state),
) -> DLQActionResponse:
    """
    Retry a task from the Dead Letter Queue.

    This moves the task back to 'pending' status and clears the episode's failure state,
    allowing it to be picked up by the worker again.

    Args:
        task_id: ID of the dead task to retry

    Returns:
        DLQActionResponse with new task status

    Raises:
        HTTPException 404: If task not found
        HTTPException 400: If task is not in 'dead' status
    """
    task = state.queue_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    if task.status not in (QueueTaskStatus.DEAD, QueueTaskStatus.FAILED):
        raise HTTPException(
            status_code=400,
            detail=f"Task is not in terminal failure state (status={task.status.value}). Only dead/failed tasks can be retried.",
        )

    # Move task back to pending
    updated_task = state.queue_manager.retry_dead_task(task_id)
    if not updated_task:
        raise HTTPException(status_code=500, detail="Failed to retry task")

    # Clear episode failure state
    state.repository.clear_episode_failure(task.episode_id)

    return DLQActionResponse(
        status="ok",
        message=f"Task {task_id} moved back to pending queue",
        task_id=task_id,
        new_status=updated_task.status.value,
    )


@router.post("/dlq/{task_id}/skip", response_model=DLQActionResponse)
async def skip_dlq_task(
    task_id: str,
    state: AppState = Depends(get_app_state),
) -> DLQActionResponse:
    """
    Skip (resolve) a task from the Dead Letter Queue.

    This marks the task as 'completed' without actually processing it.
    Use this when you've manually resolved the issue or determined the episode
    shouldn't be processed.

    Note: This does NOT clear the episode's failure state. The episode will remain
    marked as failed unless you explicitly clear it.

    Args:
        task_id: ID of the dead task to skip

    Returns:
        DLQActionResponse with new task status

    Raises:
        HTTPException 404: If task not found
        HTTPException 400: If task is not in 'dead' status
    """
    task = state.queue_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    if task.status not in (QueueTaskStatus.DEAD, QueueTaskStatus.FAILED):
        raise HTTPException(
            status_code=400,
            detail=f"Task is not in terminal failure state (status={task.status.value}). Only dead/failed tasks can be skipped.",
        )

    # Mark task as completed (skipped)
    state.queue_manager.complete_task(task_id)

    return DLQActionResponse(
        status="ok",
        message=f"Task {task_id} marked as skipped/resolved",
        task_id=task_id,
        new_status="completed",
    )


@router.post("/dlq/retry-all", response_model=DLQBulkRetryResponse)
async def retry_all_dlq_tasks(
    request: Optional[DLQBulkRetryRequest] = None,
    state: AppState = Depends(get_app_state),
) -> DLQBulkRetryResponse:
    """
    Retry multiple tasks from the Dead Letter Queue.

    If task_ids is provided, only those tasks are retried.
    If task_ids is None or empty, all dead tasks are retried.

    Args:
        request: Optional list of task IDs to retry

    Returns:
        DLQBulkRetryResponse with count of retried and skipped tasks
    """
    dead_tasks = state.queue_manager.get_dead_tasks(limit=1000)

    # Filter to specific task_ids if provided
    if request and request.task_ids:
        task_ids_set = set(request.task_ids)
        dead_tasks = [t for t in dead_tasks if t.id in task_ids_set]

    retried_ids = []
    skipped = 0

    for task in dead_tasks:
        updated_task = state.queue_manager.retry_dead_task(task.id)
        if updated_task:
            # Clear episode failure state
            state.repository.clear_episode_failure(task.episode_id)
            retried_ids.append(task.id)
        else:
            skipped += 1

    return DLQBulkRetryResponse(
        status="ok",
        retried=len(retried_ids),
        skipped=skipped,
        task_ids=retried_ids,
    )
