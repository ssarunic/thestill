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
import logging
import threading
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ...core.queue_manager import QueueManager, Task, TaskStage
from ...core.queue_manager import TaskStatus as QueueTaskStatus
from ...models.podcast import EpisodeState
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
