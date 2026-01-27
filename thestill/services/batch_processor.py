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
Batch processor for digest command.

Implements THES-26: Batch processor orchestration using the existing
QueueManager and TaskWorker infrastructure for both CLI and Web/MCP use.

This module provides a unified approach:
- Queues tasks using existing QueueManager
- For CLI sync mode: waits for all tasks to complete
- For CLI async / Web / MCP: returns immediately after queuing
"""

import signal
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set, Tuple

from structlog import get_logger

from ..core.queue_manager import QueueManager, Task, TaskStage, TaskStatus
from ..models.podcast import Episode, EpisodeState, Podcast

logger = get_logger(__name__)


# Map EpisodeState to the next TaskStage needed
STATE_TO_NEXT_STAGE: Dict[EpisodeState, TaskStage] = {
    EpisodeState.DISCOVERED: TaskStage.DOWNLOAD,
    EpisodeState.DOWNLOADED: TaskStage.DOWNSAMPLE,
    EpisodeState.DOWNSAMPLED: TaskStage.TRANSCRIBE,
    EpisodeState.TRANSCRIBED: TaskStage.CLEAN,
    EpisodeState.CLEANED: TaskStage.SUMMARIZE,
    # SUMMARIZED and FAILED have no next stage
}


@dataclass
class QueuedEpisode:
    """Tracks a queued episode and its processing outcome."""

    podcast: Podcast
    episode: Episode
    initial_task_id: str
    initial_stage: TaskStage
    final_status: Optional[TaskStatus] = None
    final_stage: Optional[TaskStage] = None
    error_message: Optional[str] = None


@dataclass
class BatchQueueResult:
    """Result of batch queue operation."""

    queued: List[QueuedEpisode] = field(default_factory=list)
    skipped: List[Tuple[Podcast, Episode, str]] = field(default_factory=list)
    was_interrupted: bool = False

    @property
    def queued_count(self) -> int:
        """Number of episodes queued for processing."""
        return len(self.queued)

    @property
    def skipped_count(self) -> int:
        """Number of episodes skipped."""
        return len(self.skipped)

    @property
    def successful_count(self) -> int:
        """Number of episodes that completed successfully (after waiting)."""
        return sum(
            1 for e in self.queued if e.final_status == TaskStatus.COMPLETED and e.final_stage == TaskStage.SUMMARIZE
        )

    @property
    def failed_count(self) -> int:
        """Number of episodes that failed (after waiting)."""
        return sum(1 for e in self.queued if e.final_status in (TaskStatus.FAILED, TaskStatus.DEAD))

    @property
    def pending_count(self) -> int:
        """Number of episodes still processing (when not waiting)."""
        return sum(
            1
            for e in self.queued
            if e.final_status is None
            or e.final_status in (TaskStatus.PENDING, TaskStatus.PROCESSING, TaskStatus.RETRY_SCHEDULED)
        )


# Type alias for progress callback
# Called with (queued_episode, current_status, current_stage) during wait loop
# Status and stage may be None if task state cannot be determined
ProgressCallback = Callable[
    [QueuedEpisode, Optional[TaskStatus], Optional[TaskStage]],
    None,
]


class BatchQueueService:
    """
    Service for batch processing episodes through the task queue.

    This service leverages the existing QueueManager and TaskWorker
    infrastructure to process multiple episodes. It provides a unified
    approach for both CLI and Web/MCP use cases.

    Features:
    - Queues tasks for multiple episodes with run_full_pipeline metadata
    - Optionally waits for all tasks to complete (for CLI sync mode)
    - Tracks progress and outcomes for each episode
    - Supports graceful shutdown via Ctrl+C

    Usage:
        service = BatchQueueService(queue_manager)

        # Queue and return immediately (async mode)
        result = service.queue_episodes(episodes)

        # Queue and wait for completion (sync mode)
        result = service.queue_episodes(episodes, wait=True)
    """

    # Terminal statuses that indicate processing is complete
    TERMINAL_STATUSES = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.DEAD}

    def __init__(
        self,
        queue_manager: QueueManager,
        poll_interval: float = 2.0,
        timeout: Optional[float] = None,
    ):
        """
        Initialize batch queue service.

        Args:
            queue_manager: QueueManager instance for task operations
            poll_interval: Seconds between status polls when waiting (default: 2.0)
            timeout: Maximum seconds to wait for completion (default: None = no timeout)
        """
        self.queue_manager = queue_manager
        self.poll_interval = poll_interval
        self.timeout = timeout

        # Shutdown flag for graceful Ctrl+C handling
        self._shutdown_requested = False
        self._original_sigint_handler = None

    def _install_signal_handler(self):
        """Install signal handler for graceful shutdown."""
        self._original_sigint_handler = signal.signal(signal.SIGINT, self._handle_sigint)

    def _restore_signal_handler(self):
        """Restore original signal handler."""
        if self._original_sigint_handler is not None:
            signal.signal(signal.SIGINT, self._original_sigint_handler)
            self._original_sigint_handler = None

    def _handle_sigint(self, signum, frame):
        """Handle Ctrl+C by setting shutdown flag."""
        logger.info("Shutdown requested, stopping wait...")
        self._shutdown_requested = True

    def queue_episodes(
        self,
        episodes: List[Tuple[Podcast, Episode]],
        wait: bool = False,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> BatchQueueResult:
        """
        Queue episodes for processing through the full pipeline.

        Args:
            episodes: List of (Podcast, Episode) tuples to process
            wait: If True, wait for all tasks to complete before returning
            progress_callback: Optional callback for progress updates during wait

        Returns:
            BatchQueueResult with queued episodes and their outcomes
        """
        result = BatchQueueResult()

        if not episodes:
            logger.info("No episodes to queue")
            return result

        logger.info("Queueing episodes for batch processing", episode_count=len(episodes))

        # Queue tasks for each episode
        for podcast, episode in episodes:
            queued = self._queue_episode(podcast, episode, result)
            if queued:
                result.queued.append(queued)

        logger.info(
            "Batch queueing complete",
            queued=result.queued_count,
            skipped=result.skipped_count,
        )

        # Optionally wait for completion
        if wait and result.queued:
            self._wait_for_completion(result, progress_callback)

        return result

    def _queue_episode(
        self,
        podcast: Podcast,
        episode: Episode,
        result: BatchQueueResult,
    ) -> Optional[QueuedEpisode]:
        """Queue a single episode, returning QueuedEpisode or None if skipped."""
        # Determine next stage based on current state
        next_stage = STATE_TO_NEXT_STAGE.get(episode.state)

        if next_stage is None:
            reason = (
                "already summarized" if episode.state == EpisodeState.SUMMARIZED else f"in {episode.state.value} state"
            )
            result.skipped.append((podcast, episode, reason))
            logger.debug(
                "Skipping episode",
                episode_id=episode.external_id,
                reason=reason,
            )
            return None

        # Check if there's already a pending task for this episode/stage
        if self.queue_manager.has_pending_task(episode.id, next_stage):
            result.skipped.append((podcast, episode, "already queued"))
            logger.debug(
                "Skipping episode - already queued",
                episode_id=episode.external_id,
                stage=next_stage.value,
            )
            return None

        # Queue the task with run_full_pipeline metadata
        task = self.queue_manager.add_task(
            episode_id=episode.id,
            stage=next_stage,
            priority=0,
            metadata={"run_full_pipeline": True},
        )

        logger.info(
            "Episode queued",
            episode_id=episode.external_id,
            episode_title=episode.title,
            task_id=task.id,
            stage=next_stage.value,
        )

        return QueuedEpisode(
            podcast=podcast,
            episode=episode,
            initial_task_id=task.id,
            initial_stage=next_stage,
        )

    def _wait_for_completion(
        self,
        result: BatchQueueResult,
        progress_callback: Optional[ProgressCallback],
    ) -> None:
        """Wait for all queued episodes to complete processing."""
        logger.info(
            "Waiting for batch completion",
            episode_count=result.queued_count,
        )

        self._install_signal_handler()
        start_time = time.time()

        try:
            # Track which episodes are still being processed
            pending_episodes: Set[str] = {qe.episode.id for qe in result.queued}

            while pending_episodes and not self._shutdown_requested:
                # Check timeout
                if self.timeout and (time.time() - start_time) > self.timeout:
                    logger.warning(
                        "Batch wait timeout reached",
                        remaining=len(pending_episodes),
                    )
                    break

                # Check status of each pending episode
                completed_this_round: Set[str] = set()

                for queued_episode in result.queued:
                    if queued_episode.episode.id not in pending_episodes:
                        continue

                    # Get latest task status for this episode
                    status, stage, error = self._get_episode_status(queued_episode)

                    # Update the queued episode with current status
                    queued_episode.final_status = status
                    queued_episode.final_stage = stage
                    queued_episode.error_message = error

                    # Call progress callback
                    if progress_callback and status and stage:
                        progress_callback(queued_episode, status, stage)

                    # Check if terminal state reached
                    if status in self.TERMINAL_STATUSES:
                        # For COMPLETED, check if we've reached SUMMARIZE stage
                        if status == TaskStatus.COMPLETED:
                            if stage == TaskStage.SUMMARIZE:
                                completed_this_round.add(queued_episode.episode.id)
                                logger.info(
                                    "Episode completed successfully",
                                    episode_id=queued_episode.episode.external_id,
                                )
                            # Otherwise, TaskWorker will chain to next stage
                        else:
                            # FAILED or DEAD
                            completed_this_round.add(queued_episode.episode.id)
                            logger.warning(
                                "Episode processing failed",
                                episode_id=queued_episode.episode.external_id,
                                status=status.value,
                                stage=stage.value if stage else None,
                                error=error,
                            )

                pending_episodes -= completed_this_round

                if pending_episodes and not self._shutdown_requested:
                    time.sleep(self.poll_interval)

        finally:
            self._restore_signal_handler()

        if self._shutdown_requested:
            result.was_interrupted = True
            logger.info("Batch wait interrupted by user")

        logger.info(
            "Batch processing status",
            successful=result.successful_count,
            failed=result.failed_count,
            pending=result.pending_count,
            interrupted=result.was_interrupted,
        )

    def _get_episode_status(
        self,
        queued_episode: QueuedEpisode,
    ) -> Tuple[Optional[TaskStatus], Optional[TaskStage], Optional[str]]:
        """
        Get the current processing status for an episode.

        Returns the status of the most recent task for the episode.

        Returns:
            Tuple of (status, stage, error_message)
        """
        tasks = self.queue_manager.get_tasks_for_episode(queued_episode.episode.id)

        if not tasks:
            return None, None, None

        # Tasks are ordered by created_at DESC, so first is most recent
        latest_task = tasks[0]

        return latest_task.status, latest_task.stage, latest_task.error_message
