"""Spec #66 — the entity stages skip cleanly when their optional extra is absent.

The AWS deployment image deliberately omits ``[entities]``: GLiNER plus
ReFinED need 4-6 GB of RAM, which will not fit a t4g.medium alongside
Postgres. Before this change ``extract-entities`` raised ``gliner is not
installed``, and because the pipeline is a LINEAR chain

    summarize -> extract-entities -> resolve-entities -> reindex -> ...

that raise severed everything downstream — most importantly ``reindex``,
which writes the search chunks. Observed in production on 2026-08-05: ten
failed extract-entities tasks and four cleaned episodes with no search
chunks at all, invisible to search despite being fully readable.

The contract these tests pin:

- unavailable extractor -> episode flagged ``skipped_unavailable`` and the
  handler RETURNS, so the worker completes the task and the chain continues
- the status is distinct from ``skipped_legacy`` (structurally ineligible,
  re-running never helps) so the backlog is queryable and drainable later
- when the extractor IS available nothing is skipped
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from thestill.core.task_handlers import handle_extract_entities, handle_resolve_entities
from thestill.models.entities import EntityExtractionStatus


def _make_task(episode_id: str = "ep-1"):
    from thestill.core.queue_manager import Task, TaskStage, TaskStatus

    return Task(id="t-1", episode_id=episode_id, stage=TaskStage.EXTRACT_ENTITIES, status=TaskStatus.PROCESSING)


def _make_state():
    episode = MagicMock()
    episode.id = "ep-1"
    episode.title = "Some Episode"
    episode.clean_transcript_json_path = "the-show/ep_cleaned.json"

    podcast = MagicMock()
    podcast.slug = "the-show"

    state = MagicMock()
    state.repository.get_episode.return_value = (podcast, episode)
    return state, episode


class TestExtractEntitiesUnavailable:
    def test_flags_skipped_unavailable_and_returns(self):
        state, episode = _make_state()

        with patch("thestill.core.entity_extractor.EntityExtractor.is_available", return_value=False):
            handle_extract_entities(_make_task(), state)

        state.repository.update_entity_extraction_status.assert_called_with(
            episode_id=episode.id,
            status=EntityExtractionStatus.SKIPPED_UNAVAILABLE.value,
        )

    def test_does_not_raise_so_the_chain_continues(self):
        # The whole point: a raise here strands the episode before reindex.
        state, _ = _make_state()
        with patch("thestill.core.entity_extractor.EntityExtractor.is_available", return_value=False):
            handle_extract_entities(_make_task(), state)  # must not raise

    def test_status_is_distinct_from_skipped_legacy(self):
        # skipped_legacy means "re-running would never help"; this backlog IS
        # eligible and must be findable separately.
        assert EntityExtractionStatus.SKIPPED_UNAVAILABLE.value != EntityExtractionStatus.SKIPPED_LEGACY.value
        assert EntityExtractionStatus.SKIPPED_UNAVAILABLE.value == "skipped_unavailable"

    def test_legacy_episodes_still_use_the_legacy_status(self):
        state, episode = _make_state()
        episode.clean_transcript_json_path = None  # structurally ineligible

        with patch("thestill.core.entity_extractor.EntityExtractor.is_available", return_value=True):
            handle_extract_entities(_make_task(), state)

        state.repository.update_entity_extraction_status.assert_called_with(
            episode_id=episode.id,
            status=EntityExtractionStatus.SKIPPED_LEGACY.value,
        )

    def test_available_extractor_is_not_skipped(self):
        state, _ = _make_state()
        with patch("thestill.core.entity_extractor.EntityExtractor.is_available", return_value=True):
            # Proceeds past the guard into real work and fails there on the
            # MagicMock fixture. What matters is only that it did NOT take
            # the skip branch.
            with pytest.raises(Exception):
                handle_extract_entities(_make_task(), state)

        statuses = [c.kwargs.get("status") for c in state.repository.update_entity_extraction_status.call_args_list]
        assert EntityExtractionStatus.SKIPPED_UNAVAILABLE.value not in statuses


class TestResolveEntitiesUnavailable:
    def test_skips_when_refined_absent_and_mentions_pending(self):
        state, _ = _make_state()
        state.entity_repository.list_pending_mentions.return_value = [MagicMock()]

        with patch("thestill.core.entity_resolver.EntityResolver.is_available", return_value=False):
            handle_resolve_entities(_make_task(), state)  # must not raise

        # Mentions are left pending — they are the backlog marker here.
        state.entity_repository.list_pending_mentions.assert_called()

    def test_no_pending_mentions_is_already_a_noop(self):
        state, _ = _make_state()
        state.entity_repository.list_pending_mentions.return_value = []

        with patch("thestill.core.entity_resolver.EntityResolver.is_available", return_value=False):
            handle_resolve_entities(_make_task(), state)  # must not raise


class TestAvailabilityHelpers:
    def test_extractor_reports_truthfully_for_this_environment(self):
        from thestill.core.entity_extractor import EntityExtractor

        try:
            import gliner  # noqa: F401

            expected = True
        except ImportError:
            expected = False
        assert EntityExtractor.is_available() is expected

    def test_resolver_reports_truthfully_for_this_environment(self):
        from thestill.core.entity_resolver import EntityResolver

        try:
            import refined.inference.processor  # noqa: F401

            expected = True
        except ImportError:
            expected = False
        assert EntityResolver.is_available() is expected
