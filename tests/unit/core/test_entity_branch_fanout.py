"""Spec #28 §0.5 — verify ``_maybe_enqueue_next_stage`` fans out at CLEAN.

Replaces the old linear ``get_next_stage`` walk. CLEAN now enqueues
two successors (SUMMARIZE + EXTRACT_ENTITIES); each subsequent stage
is linear within its branch. A target_state of ``"summarized"`` short-
circuits the user chain at SUMMARIZE while leaving in-flight entity-
branch tasks alone.
"""

from unittest.mock import MagicMock

from thestill.core.queue_manager import Task, TaskStage, TaskStatus
from thestill.core.task_worker import TaskWorker


def _full_pipeline_task(stage: TaskStage, target_state: str = "summarized") -> Task:
    return Task(
        id="task-uuid",
        episode_id="episode-uuid",
        stage=stage,
        status=TaskStatus.COMPLETED,
        priority=5,
        metadata={"run_full_pipeline": True, "target_state": target_state},
    )


def _make_worker() -> tuple[TaskWorker, MagicMock]:
    queue = MagicMock()
    worker = TaskWorker(queue_manager=queue, task_handlers={})
    return worker, queue


def _enqueued_stages(queue: MagicMock) -> list[TaskStage]:
    return [call.kwargs["stage"] for call in queue.add_task.call_args_list]


class TestFanout:
    def test_clean_enqueues_summarize_and_extract_entities(self):
        worker, queue = _make_worker()
        worker._maybe_enqueue_next_stage(_full_pipeline_task(TaskStage.CLEAN))
        assert _enqueued_stages(queue) == [TaskStage.SUMMARIZE, TaskStage.EXTRACT_ENTITIES]

    def test_extract_entities_enqueues_resolve_entities(self):
        worker, queue = _make_worker()
        worker._maybe_enqueue_next_stage(_full_pipeline_task(TaskStage.EXTRACT_ENTITIES))
        assert _enqueued_stages(queue) == [TaskStage.RESOLVE_ENTITIES]

    def test_resolve_entities_enqueues_write_corpus(self):
        worker, queue = _make_worker()
        worker._maybe_enqueue_next_stage(_full_pipeline_task(TaskStage.RESOLVE_ENTITIES))
        assert _enqueued_stages(queue) == [TaskStage.WRITE_CORPUS]

    def test_reindex_terminates(self):
        worker, queue = _make_worker()
        worker._maybe_enqueue_next_stage(_full_pipeline_task(TaskStage.REINDEX))
        assert _enqueued_stages(queue) == []

    def test_summarize_terminates_when_target_reached(self):
        # SUMMARIZE → resulting_state=summarized == target_state — user chain stops.
        worker, queue = _make_worker()
        worker._maybe_enqueue_next_stage(_full_pipeline_task(TaskStage.SUMMARIZE))
        assert _enqueued_stages(queue) == []

    def test_full_pipeline_flag_off_means_no_chaining(self):
        worker, queue = _make_worker()
        task = Task(
            id="t",
            episode_id="ep",
            stage=TaskStage.CLEAN,
            status=TaskStatus.COMPLETED,
            metadata={"run_full_pipeline": False},
        )
        worker._maybe_enqueue_next_stage(task)
        queue.add_task.assert_not_called()

    def test_target_state_does_not_short_circuit_entity_branch(self):
        # An entity-branch stage isn't in _STAGE_TO_STATE, so the
        # target_state check returns None != "summarized" and the chain
        # keeps going. This is the deliberate spec contract: the user-
        # facing target stops the user chain only.
        worker, queue = _make_worker()
        worker._maybe_enqueue_next_stage(_full_pipeline_task(TaskStage.WRITE_CORPUS))
        assert _enqueued_stages(queue) == [TaskStage.REINDEX]
