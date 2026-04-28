"""Spec #28 §6 — failure isolation rule for the entity branch.

A task failure in the entity branch (``extract-entities``,
``resolve-entities``, ``write-corpus``, ``reindex``) must NOT touch the
user-facing ``episodes.failed_at_stage``. Only
``episodes.entity_extraction_status`` is bumped to ``"failed"``.

User-chain stages (download/downsample/transcribe/clean/summarize)
keep the existing behaviour: the episode card flips to FAILED.

These tests exercise ``TaskWorker._mark_episode_failed`` against a
mock repository — that is the single branching point where the rule
is enforced.
"""

from unittest.mock import MagicMock

from thestill.core.queue_manager import Task, TaskStage, TaskStatus
from thestill.core.task_worker import TaskWorker


def _make_task(stage: TaskStage) -> Task:
    return Task(
        id="task-uuid",
        episode_id="episode-uuid",
        stage=stage,
        status=TaskStatus.FAILED,
    )


def _make_worker_with_mock_repo() -> tuple[TaskWorker, MagicMock]:
    repo = MagicMock()
    queue = MagicMock()
    handlers: dict = {}
    worker = TaskWorker(queue_manager=queue, task_handlers=handlers, repository=repo)
    return worker, repo


class TestEntityBranchFailureIsolation:
    def test_extract_entities_failure_only_touches_entity_status(self):
        worker, repo = _make_worker_with_mock_repo()
        worker._mark_episode_failed(_make_task(TaskStage.EXTRACT_ENTITIES), "boom", "transient")
        repo.update_entity_extraction_status.assert_called_once_with(
            episode_id="episode-uuid",
            status="failed",
        )
        repo.mark_episode_failed.assert_not_called()

    def test_resolve_entities_failure_only_touches_entity_status(self):
        worker, repo = _make_worker_with_mock_repo()
        worker._mark_episode_failed(_make_task(TaskStage.RESOLVE_ENTITIES), "boom", "fatal")
        repo.update_entity_extraction_status.assert_called_once()
        repo.mark_episode_failed.assert_not_called()

    def test_write_corpus_failure_only_touches_entity_status(self):
        worker, repo = _make_worker_with_mock_repo()
        worker._mark_episode_failed(_make_task(TaskStage.WRITE_CORPUS), "boom", "transient")
        repo.update_entity_extraction_status.assert_called_once()
        repo.mark_episode_failed.assert_not_called()

    def test_reindex_failure_only_touches_entity_status(self):
        worker, repo = _make_worker_with_mock_repo()
        worker._mark_episode_failed(_make_task(TaskStage.REINDEX), "boom", "transient")
        repo.update_entity_extraction_status.assert_called_once()
        repo.mark_episode_failed.assert_not_called()


class TestUserChainFailureUnchanged:
    def test_summarize_failure_marks_episode_failed(self):
        worker, repo = _make_worker_with_mock_repo()
        worker._mark_episode_failed(_make_task(TaskStage.SUMMARIZE), "boom", "transient")
        repo.mark_episode_failed.assert_called_once_with(
            episode_id="episode-uuid",
            failed_at_stage="summarize",
            failure_reason="boom",
            failure_type="transient",
        )
        repo.update_entity_extraction_status.assert_not_called()

    def test_download_failure_marks_episode_failed(self):
        worker, repo = _make_worker_with_mock_repo()
        worker._mark_episode_failed(_make_task(TaskStage.DOWNLOAD), "boom", "fatal")
        repo.mark_episode_failed.assert_called_once()
        repo.update_entity_extraction_status.assert_not_called()


class TestNoRepoIsBenign:
    def test_no_repository_skips_silently(self):
        queue = MagicMock()
        worker = TaskWorker(queue_manager=queue, task_handlers={}, repository=None)
        # Should not raise.
        worker._mark_episode_failed(_make_task(TaskStage.EXTRACT_ENTITIES), "boom", "transient")
        worker._mark_episode_failed(_make_task(TaskStage.SUMMARIZE), "boom", "transient")
