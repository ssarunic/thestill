"""Spec #28 §0.5 — verify ``_maybe_enqueue_next_stage`` walks the linear chain.

Chain shape: ``download → … → clean → summarize → extract-entities →
resolve-entities → write-corpus → reindex``. There is no fan-out — the
file is named ``..._fanout`` for git-history continuity (the original
spec called for a fan-out at CLEAN; the design evolved to fully
linear when we realised the per-episode mutex serialised it anyway
and a future GLiNER variant may consume summary text).

``target_state`` is honoured as an explicit early-stop for the *user
chain* (downloaded…summarized). It does NOT block the entity branch:
``target_state="summarized"`` stops new user-chain work but the
entity-branch successor (``extract-entities``) still kicks off so
search/index keep working. Callers that omit ``target_state`` run the
chain through to reindex either way.
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

    def test_resolve_entities_enqueues_reindex(self):
        worker, queue = _make_worker()
        worker._maybe_enqueue_next_stage(_full_pipeline_task(TaskStage.RESOLVE_ENTITIES))
        assert _enqueued_stages(queue) == [TaskStage.REINDEX]

    def test_reindex_chains_rebuild_cooccurrences(self):
        worker, queue = _make_worker()
        worker._maybe_enqueue_next_stage(_full_pipeline_task(TaskStage.REINDEX))
        assert _enqueued_stages(queue) == [TaskStage.REBUILD_COOCCURRENCES]

    def test_rebuild_cooccurrences_chains_compute_related(self):
        worker, queue = _make_worker()
        worker._maybe_enqueue_next_stage(_full_pipeline_task(TaskStage.REBUILD_COOCCURRENCES))
        assert _enqueued_stages(queue) == [TaskStage.COMPUTE_RELATED]

    def test_compute_related_chains_enrich_entities(self):
        # spec #47 — compute-related is no longer terminal; it chains into
        # enrich-entities (Wikidata/Wikipedia display data), which runs last.
        worker, queue = _make_worker()
        worker._maybe_enqueue_next_stage(_full_pipeline_task(TaskStage.COMPUTE_RELATED))
        assert _enqueued_stages(queue) == [TaskStage.ENRICH_ENTITIES]

    def test_enrich_entities_terminates(self):
        worker, queue = _make_worker()
        worker._maybe_enqueue_next_stage(_full_pipeline_task(TaskStage.ENRICH_ENTITIES))
        assert _enqueued_stages(queue) == []


class TestTargetState:
    def test_summarized_target_still_starts_entity_branch(self):
        # Regression: api_commands.py callers pass
        # target_state="summarized" by default. The early-stop is a
        # *user-chain* concept (its valid values are user-chain
        # stages). The entity branch — extract → resolve → reindex —
        # is a separate failure domain that must run after summarize
        # so search/index stays populated. Previously this returned
        # ``[]``, leaving 27 episodes stranded with no chunks/mentions.
        worker, queue = _make_worker()
        worker._maybe_enqueue_next_stage(_full_pipeline_task(TaskStage.SUMMARIZE, target_state="summarized"))
        assert _enqueued_stages(queue) == [TaskStage.EXTRACT_ENTITIES]

    def test_unset_target_runs_chain_to_reindex(self):
        # api_episodes.py + batch_processor.py omit target_state — they
        # should also flow into the entity branch automatically.
        worker, queue = _make_worker()
        worker._maybe_enqueue_next_stage(_full_pipeline_task(TaskStage.SUMMARIZE))
        assert _enqueued_stages(queue) == [TaskStage.EXTRACT_ENTITIES]

    def test_explicit_earlier_target_stops_user_chain(self):
        # target_state="cleaned" stops at clean; the entity branch
        # never starts because clean's successor is summarize (a
        # user-chain stage), and we filter user-chain successors
        # away once the target is reached.
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


class TestEntityBranchAlwaysChains:
    """Spec #28 §0.5 — the entity branch is atomic. Standalone enqueues
    of ``extract-entities`` (admin rebuilds, repair scripts, the CLI
    ``rebuild-entities``) MUST chain into ``resolve-entities`` and on
    to ``reindex`` regardless of the ``run_full_pipeline`` metadata
    flag. Without this, extract writes orphan ``pending`` mentions
    that nothing ever resolves — which is exactly the bug that hit the
    370-episode ``complete-but-empty`` bucket.
    """

    @staticmethod
    def _bare_task(stage: TaskStage) -> Task:
        # No metadata at all — simulates queue_manager.add_task(...) with
        # the default metadata=None used by repair scripts.
        return Task(
            id="t",
            episode_id="ep",
            stage=stage,
            status=TaskStatus.COMPLETED,
            metadata={},
        )

    def test_summarize_chains_entity_branch_without_full_pipeline_flag(self):
        # Regression: ``thestill summarize`` and single-stage retries
        # don't set ``run_full_pipeline``. Before this fix, those
        # paths left summarized episodes silently unindexed (52
        # episodes had no extract-entities task at all). The entity
        # branch is non-destructive and idempotent — successful
        # summarize should always start it.
        worker, queue = _make_worker()
        worker._maybe_enqueue_next_stage(self._bare_task(TaskStage.SUMMARIZE))
        assert _enqueued_stages(queue) == [TaskStage.EXTRACT_ENTITIES]

    def test_extract_entities_chains_resolve_without_full_pipeline_flag(self):
        worker, queue = _make_worker()
        worker._maybe_enqueue_next_stage(self._bare_task(TaskStage.EXTRACT_ENTITIES))
        assert _enqueued_stages(queue) == [TaskStage.RESOLVE_ENTITIES]

    def test_resolve_entities_chains_reindex_without_full_pipeline_flag(self):
        worker, queue = _make_worker()
        worker._maybe_enqueue_next_stage(self._bare_task(TaskStage.RESOLVE_ENTITIES))
        assert _enqueued_stages(queue) == [TaskStage.REINDEX]

    def test_reindex_chains_rebuild_cooccurrences_without_full_pipeline_flag(self):
        worker, queue = _make_worker()
        worker._maybe_enqueue_next_stage(self._bare_task(TaskStage.REINDEX))
        assert _enqueued_stages(queue) == [TaskStage.REBUILD_COOCCURRENCES]

    def test_rebuild_cooccurrences_chains_compute_related_without_full_pipeline_flag(self):
        worker, queue = _make_worker()
        worker._maybe_enqueue_next_stage(self._bare_task(TaskStage.REBUILD_COOCCURRENCES))
        assert _enqueued_stages(queue) == [TaskStage.COMPUTE_RELATED]

    def test_compute_related_chains_enrich_entities_without_full_pipeline_flag(self):
        worker, queue = _make_worker()
        worker._maybe_enqueue_next_stage(self._bare_task(TaskStage.COMPUTE_RELATED))
        assert _enqueued_stages(queue) == [TaskStage.ENRICH_ENTITIES]

    def test_enrich_entities_terminates_without_full_pipeline_flag(self):
        worker, queue = _make_worker()
        worker._maybe_enqueue_next_stage(self._bare_task(TaskStage.ENRICH_ENTITIES))
        assert _enqueued_stages(queue) == []

    def test_user_chain_still_requires_full_pipeline_flag(self):
        # Regression guard: the entity-branch carve-out must NOT
        # accidentally make user-chain stages auto-chain on standalone
        # retries. A bare CLEAN must still stop where it is.
        worker, queue = _make_worker()
        worker._maybe_enqueue_next_stage(self._bare_task(TaskStage.CLEAN))
        queue.add_task.assert_not_called()
