"""Spec #28 §0.5 — verify ``_maybe_enqueue_next_stage`` walks the linear chain.

Chain shape: ``download → … → clean → summarize → extract-entities →
resolve-entities → write-corpus → reindex``. There is no fan-out — the
file is named ``..._fanout`` for git-history continuity (the original
spec called for a fan-out at CLEAN; the design evolved to fully
linear when we realised the per-episode mutex serialised it anyway
and a future GLiNER variant may consume summary text).

``target_state`` is honoured as an explicit early-stop: callers that
pass ``target_state="summarized"`` stop after summarize, callers that
omit ``target_state`` run the chain through to reindex.
"""

from unittest.mock import MagicMock

from thestill.core.queue_manager import Task, TaskStage, TaskStatus
from thestill.core.task_worker import TaskWorker


def _full_pipeline_task(stage: TaskStage, target_state: str | None = None) -> Task:
    metadata = {"run_full_pipeline": True}
    if target_state is not None:
        metadata["target_state"] = target_state
    return Task(
        id="task-uuid",
        episode_id="episode-uuid",
        stage=stage,
        status=TaskStatus.COMPLETED,
        priority=5,
        metadata=metadata,
    )


def _make_worker() -> tuple[TaskWorker, MagicMock]:
    queue = MagicMock()
    worker = TaskWorker(queue_manager=queue, task_handlers={})
    return worker, queue


def _enqueued_stages(queue: MagicMock) -> list[TaskStage]:
    return [call.kwargs["stage"] for call in queue.add_task.call_args_list]


class TestLinearChain:
    def test_clean_enqueues_summarize_only(self):
        worker, queue = _make_worker()
        worker._maybe_enqueue_next_stage(_full_pipeline_task(TaskStage.CLEAN))
        assert _enqueued_stages(queue) == [TaskStage.SUMMARIZE]

    def test_summarize_enqueues_extract_entities(self):
        # The dependency the user explicitly asked for — entities run
        # AFTER summarize, never in parallel with it.
        worker, queue = _make_worker()
        worker._maybe_enqueue_next_stage(_full_pipeline_task(TaskStage.SUMMARIZE))
        assert _enqueued_stages(queue) == [TaskStage.EXTRACT_ENTITIES]

    def test_extract_entities_enqueues_resolve_entities(self):
        worker, queue = _make_worker()
        worker._maybe_enqueue_next_stage(_full_pipeline_task(TaskStage.EXTRACT_ENTITIES))
        assert _enqueued_stages(queue) == [TaskStage.RESOLVE_ENTITIES]

    def test_resolve_entities_enqueues_write_corpus(self):
        worker, queue = _make_worker()
        worker._maybe_enqueue_next_stage(_full_pipeline_task(TaskStage.RESOLVE_ENTITIES))
        assert _enqueued_stages(queue) == [TaskStage.WRITE_CORPUS]

    def test_write_corpus_enqueues_reindex(self):
        worker, queue = _make_worker()
        worker._maybe_enqueue_next_stage(_full_pipeline_task(TaskStage.WRITE_CORPUS))
        assert _enqueued_stages(queue) == [TaskStage.REINDEX]

    def test_reindex_terminates(self):
        worker, queue = _make_worker()
        worker._maybe_enqueue_next_stage(_full_pipeline_task(TaskStage.REINDEX))
        assert _enqueued_stages(queue) == []


class TestTargetState:
    def test_explicit_summarized_target_stops_at_summarize(self):
        # api_commands.py callers pass target_state="summarized" by
        # default — they keep stopping at summarize unless the operator
        # explicitly extends the target.
        worker, queue = _make_worker()
        worker._maybe_enqueue_next_stage(_full_pipeline_task(TaskStage.SUMMARIZE, target_state="summarized"))
        assert _enqueued_stages(queue) == []

    def test_unset_target_runs_chain_to_reindex(self):
        # api_episodes.py + batch_processor.py omit target_state — they
        # should now flow into the entity branch automatically.
        worker, queue = _make_worker()
        worker._maybe_enqueue_next_stage(_full_pipeline_task(TaskStage.SUMMARIZE))
        assert _enqueued_stages(queue) == [TaskStage.EXTRACT_ENTITIES]

    def test_explicit_earlier_target_stops_user_chain(self):
        # target_state="cleaned" stops at clean; the entity branch
        # never starts because clean's successor is summarize and we
        # match the target before enqueueing it.
        worker, queue = _make_worker()
        worker._maybe_enqueue_next_stage(_full_pipeline_task(TaskStage.CLEAN, target_state="cleaned"))
        assert _enqueued_stages(queue) == []


class TestRunFullPipelineFlag:
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
