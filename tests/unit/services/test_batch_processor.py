"""
Unit tests for BatchQueueService.

Tests the batch processing service that queues episodes for processing
through the existing task queue infrastructure.
"""

from datetime import datetime, timezone
from unittest.mock import Mock, call, patch

import pytest

from thestill.core.queue_manager import QueueManager, Task, TaskStage, TaskStatus
from thestill.models.podcast import Episode, EpisodeState, Podcast
from thestill.services.batch_processor import STATE_TO_NEXT_STAGE, BatchQueueResult, BatchQueueService, QueuedEpisode


@pytest.fixture
def mock_queue_manager():
    """Create mock queue manager."""
    return Mock(spec=QueueManager)


@pytest.fixture
def sample_podcast():
    """Create a sample podcast for testing."""
    return Podcast(
        id="podcast-123",
        title="Test Podcast",
        description="A test podcast",
        rss_url="https://example.com/feed.xml",
    )


def make_episode(
    external_id: str,
    title: str,
    state: EpisodeState,
    podcast_id: str = "podcast-123",
    episode_id: str = None,
) -> Episode:
    """Helper to create episodes with specific states."""
    episode = Episode(
        external_id=external_id,
        podcast_id=podcast_id,
        title=title,
        description=f"Description for {title}",
        pub_date=datetime.now(timezone.utc),
        audio_url=f"https://example.com/{external_id}.mp3",
    )

    # Override ID if provided
    if episode_id:
        episode.id = episode_id

    # Set appropriate paths to achieve desired state
    if state == EpisodeState.DOWNLOADED:
        episode.audio_path = f"{external_id}.mp3"
    elif state == EpisodeState.DOWNSAMPLED:
        episode.audio_path = f"{external_id}.mp3"
        episode.downsampled_audio_path = f"{external_id}.wav"
    elif state == EpisodeState.TRANSCRIBED:
        episode.audio_path = f"{external_id}.mp3"
        episode.downsampled_audio_path = f"{external_id}.wav"
        episode.raw_transcript_path = f"{external_id}.json"
    elif state == EpisodeState.CLEANED:
        episode.audio_path = f"{external_id}.mp3"
        episode.downsampled_audio_path = f"{external_id}.wav"
        episode.raw_transcript_path = f"{external_id}.json"
        episode.clean_transcript_path = f"{external_id}.md"
    elif state == EpisodeState.SUMMARIZED:
        episode.audio_path = f"{external_id}.mp3"
        episode.downsampled_audio_path = f"{external_id}.wav"
        episode.raw_transcript_path = f"{external_id}.json"
        episode.clean_transcript_path = f"{external_id}.md"
        episode.summary_path = f"{external_id}_summary.md"
    elif state == EpisodeState.FAILED:
        episode.failed_at_stage = "download"
        episode.failure_reason = "Network error"

    return episode


def make_task(
    task_id: str,
    episode_id: str,
    stage: TaskStage,
    status: TaskStatus = TaskStatus.PENDING,
    error_message: str = None,
) -> Task:
    """Helper to create Task objects."""
    return Task(
        id=task_id,
        episode_id=episode_id,
        stage=stage,
        status=status,
        error_message=error_message,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


class TestStateToNextStageMapping:
    """Tests for STATE_TO_NEXT_STAGE mapping."""

    def test_discovered_maps_to_download(self):
        """DISCOVERED state should map to DOWNLOAD stage."""
        assert STATE_TO_NEXT_STAGE[EpisodeState.DISCOVERED] == TaskStage.DOWNLOAD

    def test_downloaded_maps_to_downsample(self):
        """DOWNLOADED state should map to DOWNSAMPLE stage."""
        assert STATE_TO_NEXT_STAGE[EpisodeState.DOWNLOADED] == TaskStage.DOWNSAMPLE

    def test_downsampled_maps_to_transcribe(self):
        """DOWNSAMPLED state should map to TRANSCRIBE stage."""
        assert STATE_TO_NEXT_STAGE[EpisodeState.DOWNSAMPLED] == TaskStage.TRANSCRIBE

    def test_transcribed_maps_to_clean(self):
        """TRANSCRIBED state should map to CLEAN stage."""
        assert STATE_TO_NEXT_STAGE[EpisodeState.TRANSCRIBED] == TaskStage.CLEAN

    def test_cleaned_maps_to_summarize(self):
        """CLEANED state should map to SUMMARIZE stage."""
        assert STATE_TO_NEXT_STAGE[EpisodeState.CLEANED] == TaskStage.SUMMARIZE

    def test_summarized_has_no_mapping(self):
        """SUMMARIZED state should not be in mapping."""
        assert EpisodeState.SUMMARIZED not in STATE_TO_NEXT_STAGE

    def test_failed_has_no_mapping(self):
        """FAILED state should not be in mapping."""
        assert EpisodeState.FAILED not in STATE_TO_NEXT_STAGE


class TestBatchQueueService:
    """Tests for BatchQueueService."""

    def test_queue_empty_list(self, mock_queue_manager):
        """Queueing empty list should return empty result."""
        service = BatchQueueService(mock_queue_manager)
        result = service.queue_episodes([])

        assert result.queued_count == 0
        assert result.skipped_count == 0
        mock_queue_manager.add_task.assert_not_called()

    def test_queue_discovered_episode(self, mock_queue_manager, sample_podcast):
        """DISCOVERED episode should be queued for DOWNLOAD."""
        episode = make_episode("ep1", "Episode 1", EpisodeState.DISCOVERED)
        mock_queue_manager.has_pending_task.return_value = False
        mock_queue_manager.add_task.return_value = make_task("task-1", episode.id, TaskStage.DOWNLOAD)

        service = BatchQueueService(mock_queue_manager)
        result = service.queue_episodes([(sample_podcast, episode)])

        assert result.queued_count == 1
        assert result.skipped_count == 0
        mock_queue_manager.add_task.assert_called_once_with(
            episode_id=episode.id,
            stage=TaskStage.DOWNLOAD,
            priority=0,
            metadata={"run_full_pipeline": True},
        )

    def test_queue_cleaned_episode(self, mock_queue_manager, sample_podcast):
        """CLEANED episode should be queued for SUMMARIZE."""
        episode = make_episode("ep1", "Episode 1", EpisodeState.CLEANED)
        mock_queue_manager.has_pending_task.return_value = False
        mock_queue_manager.add_task.return_value = make_task("task-1", episode.id, TaskStage.SUMMARIZE)

        service = BatchQueueService(mock_queue_manager)
        result = service.queue_episodes([(sample_podcast, episode)])

        assert result.queued_count == 1
        mock_queue_manager.add_task.assert_called_once_with(
            episode_id=episode.id,
            stage=TaskStage.SUMMARIZE,
            priority=0,
            metadata={"run_full_pipeline": True},
        )

    def test_skip_summarized_episode(self, mock_queue_manager, sample_podcast):
        """SUMMARIZED episode should be skipped."""
        episode = make_episode("ep1", "Episode 1", EpisodeState.SUMMARIZED)

        service = BatchQueueService(mock_queue_manager)
        result = service.queue_episodes([(sample_podcast, episode)])

        assert result.queued_count == 0
        assert result.skipped_count == 1
        assert result.skipped[0][2] == "already summarized"
        mock_queue_manager.add_task.assert_not_called()

    def test_skip_failed_episode(self, mock_queue_manager, sample_podcast):
        """FAILED episode should be skipped."""
        episode = make_episode("ep1", "Episode 1", EpisodeState.FAILED)

        service = BatchQueueService(mock_queue_manager)
        result = service.queue_episodes([(sample_podcast, episode)])

        assert result.queued_count == 0
        assert result.skipped_count == 1
        assert "failed" in result.skipped[0][2].lower()
        mock_queue_manager.add_task.assert_not_called()

    def test_skip_already_queued_episode(self, mock_queue_manager, sample_podcast):
        """Episode with pending task should be skipped."""
        episode = make_episode("ep1", "Episode 1", EpisodeState.DISCOVERED)
        mock_queue_manager.has_pending_task.return_value = True

        service = BatchQueueService(mock_queue_manager)
        result = service.queue_episodes([(sample_podcast, episode)])

        assert result.queued_count == 0
        assert result.skipped_count == 1
        assert result.skipped[0][2] == "already queued"
        mock_queue_manager.add_task.assert_not_called()

    def test_queue_multiple_episodes(self, mock_queue_manager, sample_podcast):
        """Multiple episodes at different states should be queued correctly."""
        episodes = [
            make_episode("ep1", "Episode 1", EpisodeState.DISCOVERED),
            make_episode("ep2", "Episode 2", EpisodeState.DOWNLOADED),
            make_episode("ep3", "Episode 3", EpisodeState.SUMMARIZED),  # Should skip
        ]

        mock_queue_manager.has_pending_task.return_value = False
        mock_queue_manager.add_task.side_effect = [
            make_task("task-1", episodes[0].id, TaskStage.DOWNLOAD),
            make_task("task-2", episodes[1].id, TaskStage.DOWNSAMPLE),
        ]

        service = BatchQueueService(mock_queue_manager)
        result = service.queue_episodes([(sample_podcast, ep) for ep in episodes])

        assert result.queued_count == 2
        assert result.skipped_count == 1
        assert mock_queue_manager.add_task.call_count == 2


class TestBatchQueueResultProperties:
    """Tests for BatchQueueResult computed properties."""

    def test_successful_count(self, sample_podcast):
        """successful_count should count episodes that completed SUMMARIZE."""
        result = BatchQueueResult()

        # Episode that completed summarization
        qe1 = QueuedEpisode(
            podcast=sample_podcast,
            episode=make_episode("ep1", "Ep 1", EpisodeState.DISCOVERED),
            initial_task_id="t1",
            initial_stage=TaskStage.DOWNLOAD,
        )
        qe1.final_status = TaskStatus.COMPLETED
        qe1.final_stage = TaskStage.SUMMARIZE

        # Episode that completed but at clean stage (still processing)
        qe2 = QueuedEpisode(
            podcast=sample_podcast,
            episode=make_episode("ep2", "Ep 2", EpisodeState.DISCOVERED),
            initial_task_id="t2",
            initial_stage=TaskStage.DOWNLOAD,
        )
        qe2.final_status = TaskStatus.COMPLETED
        qe2.final_stage = TaskStage.CLEAN

        result.queued = [qe1, qe2]

        assert result.successful_count == 1

    def test_failed_count(self, sample_podcast):
        """failed_count should count FAILED and DEAD statuses."""
        result = BatchQueueResult()

        qe1 = QueuedEpisode(
            podcast=sample_podcast,
            episode=make_episode("ep1", "Ep 1", EpisodeState.DISCOVERED),
            initial_task_id="t1",
            initial_stage=TaskStage.DOWNLOAD,
        )
        qe1.final_status = TaskStatus.FAILED

        qe2 = QueuedEpisode(
            podcast=sample_podcast,
            episode=make_episode("ep2", "Ep 2", EpisodeState.DISCOVERED),
            initial_task_id="t2",
            initial_stage=TaskStage.DOWNLOAD,
        )
        qe2.final_status = TaskStatus.DEAD

        qe3 = QueuedEpisode(
            podcast=sample_podcast,
            episode=make_episode("ep3", "Ep 3", EpisodeState.DISCOVERED),
            initial_task_id="t3",
            initial_stage=TaskStage.DOWNLOAD,
        )
        qe3.final_status = TaskStatus.COMPLETED
        qe3.final_stage = TaskStage.SUMMARIZE

        result.queued = [qe1, qe2, qe3]

        assert result.failed_count == 2

    def test_pending_count(self, sample_podcast):
        """pending_count should count non-terminal statuses."""
        result = BatchQueueResult()

        qe1 = QueuedEpisode(
            podcast=sample_podcast,
            episode=make_episode("ep1", "Ep 1", EpisodeState.DISCOVERED),
            initial_task_id="t1",
            initial_stage=TaskStage.DOWNLOAD,
        )
        qe1.final_status = TaskStatus.PENDING

        qe2 = QueuedEpisode(
            podcast=sample_podcast,
            episode=make_episode("ep2", "Ep 2", EpisodeState.DISCOVERED),
            initial_task_id="t2",
            initial_stage=TaskStage.DOWNLOAD,
        )
        qe2.final_status = TaskStatus.PROCESSING

        qe3 = QueuedEpisode(
            podcast=sample_podcast,
            episode=make_episode("ep3", "Ep 3", EpisodeState.DISCOVERED),
            initial_task_id="t3",
            initial_stage=TaskStage.DOWNLOAD,
        )
        qe3.final_status = None  # Not yet checked

        result.queued = [qe1, qe2, qe3]

        assert result.pending_count == 3


class TestBatchQueueServiceWait:
    """Tests for wait functionality in BatchQueueService."""

    def test_wait_for_completion(self, mock_queue_manager, sample_podcast):
        """Service should poll until all tasks complete."""
        episode = make_episode("ep1", "Episode 1", EpisodeState.DISCOVERED)
        mock_queue_manager.has_pending_task.return_value = False
        mock_queue_manager.add_task.return_value = make_task("task-1", episode.id, TaskStage.DOWNLOAD)

        # Simulate task completing on second poll
        mock_queue_manager.get_tasks_for_episode.side_effect = [
            [make_task("task-1", episode.id, TaskStage.DOWNLOAD, TaskStatus.PROCESSING)],
            [make_task("task-1", episode.id, TaskStage.SUMMARIZE, TaskStatus.COMPLETED)],
        ]

        service = BatchQueueService(mock_queue_manager, poll_interval=0.01)

        with patch("time.sleep"):  # Don't actually sleep in tests
            result = service.queue_episodes([(sample_podcast, episode)], wait=True)

        assert result.queued_count == 1
        assert result.successful_count == 1
        assert mock_queue_manager.get_tasks_for_episode.call_count == 2

    def test_wait_handles_failure(self, mock_queue_manager, sample_podcast):
        """Service should detect and report failed tasks."""
        episode = make_episode("ep1", "Episode 1", EpisodeState.DISCOVERED)
        mock_queue_manager.has_pending_task.return_value = False
        mock_queue_manager.add_task.return_value = make_task("task-1", episode.id, TaskStage.DOWNLOAD)

        # Simulate task failing
        mock_queue_manager.get_tasks_for_episode.return_value = [
            make_task(
                "task-1",
                episode.id,
                TaskStage.DOWNLOAD,
                TaskStatus.DEAD,
                error_message="Fatal error",
            )
        ]

        service = BatchQueueService(mock_queue_manager, poll_interval=0.01)

        with patch("time.sleep"):
            result = service.queue_episodes([(sample_podcast, episode)], wait=True)

        assert result.queued_count == 1
        assert result.failed_count == 1
        assert result.queued[0].error_message == "Fatal error"

    def test_wait_respects_timeout(self, mock_queue_manager, sample_podcast):
        """Service should stop waiting after timeout."""
        episode = make_episode("ep1", "Episode 1", EpisodeState.DISCOVERED)
        mock_queue_manager.has_pending_task.return_value = False
        mock_queue_manager.add_task.return_value = make_task("task-1", episode.id, TaskStage.DOWNLOAD)

        # Task stays pending forever
        mock_queue_manager.get_tasks_for_episode.return_value = [
            make_task("task-1", episode.id, TaskStage.DOWNLOAD, TaskStatus.PROCESSING)
        ]

        service = BatchQueueService(mock_queue_manager, poll_interval=0.01, timeout=0.05)

        with patch("time.sleep"):
            result = service.queue_episodes([(sample_podcast, episode)], wait=True)

        assert result.queued_count == 1
        assert result.pending_count == 1  # Still pending after timeout

    def test_progress_callback_called(self, mock_queue_manager, sample_podcast):
        """Progress callback should be called during wait."""
        episode = make_episode("ep1", "Episode 1", EpisodeState.DISCOVERED)
        mock_queue_manager.has_pending_task.return_value = False
        mock_queue_manager.add_task.return_value = make_task("task-1", episode.id, TaskStage.DOWNLOAD)

        mock_queue_manager.get_tasks_for_episode.return_value = [
            make_task("task-1", episode.id, TaskStage.SUMMARIZE, TaskStatus.COMPLETED)
        ]

        callback = Mock()
        service = BatchQueueService(mock_queue_manager, poll_interval=0.01)

        with patch("time.sleep"):
            result = service.queue_episodes([(sample_podcast, episode)], wait=True, progress_callback=callback)

        callback.assert_called()
        call_args = callback.call_args
        assert call_args[0][1] == TaskStatus.COMPLETED
        assert call_args[0][2] == TaskStage.SUMMARIZE
