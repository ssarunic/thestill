"""Spec #28 §0.5 — dependency-graph fan-out tests.

Replaces the old linear ``get_next_stage`` with ``get_next_stages``,
which returns a list (zero, one, or many successors) so ``CLEAN`` can
fan out to both ``SUMMARIZE`` (existing user chain) and
``EXTRACT_ENTITIES`` (entity branch). These tests pin the graph shape
and the failure-isolation classifier; they do not exercise the
worker, repository, or DB.
"""

from thestill.core.queue_manager import STAGE_SUCCESSORS, TaskStage, get_next_stages, is_entity_branch_stage


class TestStageSuccessors:
    def test_user_chain_is_linear(self):
        assert get_next_stages(TaskStage.DOWNLOAD) == [TaskStage.DOWNSAMPLE]
        assert get_next_stages(TaskStage.DOWNSAMPLE) == [TaskStage.TRANSCRIBE]
        assert get_next_stages(TaskStage.TRANSCRIBE) == [TaskStage.CLEAN]
        assert get_next_stages(TaskStage.CLEAN) == [TaskStage.SUMMARIZE]

    def test_summarize_continues_into_entity_branch(self):
        # spec #28 — entities run AFTER summarize, not in parallel.
        # A future GLiNER variant may consume summary text, so summary
        # must be durable on disk before extraction starts.
        assert get_next_stages(TaskStage.SUMMARIZE) == [TaskStage.EXTRACT_ENTITIES]

    def test_entity_branch_is_linear_to_reindex(self):
        assert get_next_stages(TaskStage.EXTRACT_ENTITIES) == [TaskStage.RESOLVE_ENTITIES]
        assert get_next_stages(TaskStage.RESOLVE_ENTITIES) == [TaskStage.WRITE_CORPUS]
        assert get_next_stages(TaskStage.WRITE_CORPUS) == [TaskStage.REINDEX]
        assert get_next_stages(TaskStage.REINDEX) == []

    def test_returned_list_is_a_copy(self):
        # Defensive: callers must be free to mutate the returned list
        # without affecting the canonical graph.
        result = get_next_stages(TaskStage.CLEAN)
        result.clear()
        assert get_next_stages(TaskStage.CLEAN) == [TaskStage.SUMMARIZE]

    def test_every_stage_has_an_entry(self):
        # Pin the invariant so a future stage addition doesn't silently
        # produce an empty-list (= terminate) successor list — that
        # would be a quietly broken pipeline.
        for stage in TaskStage:
            assert stage in STAGE_SUCCESSORS, f"missing successors entry for {stage.value}"


class TestEntityBranchClassifier:
    def test_entity_branch_stages_classified_as_non_user_failing(self):
        for stage in (
            TaskStage.EXTRACT_ENTITIES,
            TaskStage.RESOLVE_ENTITIES,
            TaskStage.WRITE_CORPUS,
            TaskStage.REINDEX,
        ):
            assert is_entity_branch_stage(stage), f"{stage.value} should be entity-branch"

    def test_user_chain_stages_not_entity_branch(self):
        for stage in (
            TaskStage.DOWNLOAD,
            TaskStage.DOWNSAMPLE,
            TaskStage.TRANSCRIBE,
            TaskStage.CLEAN,
            TaskStage.SUMMARIZE,
        ):
            assert not is_entity_branch_stage(stage), f"{stage.value} must not be entity-branch"
