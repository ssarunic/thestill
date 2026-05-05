"""Spec #28 §1.5 — handle_resolve_entities task handler tests.

Mirrors the test pattern from ``test_handle_extract_entities.py``:
``MagicMock`` AppState, locally-defined ``StubReFinED``, status-write
assertions.

Cases:
- happy path: pending mentions in → resolver runs → upsert + flip
  status + cooccurrences rebuild + alias-merge
- no-pending: episode with zero pending mentions → no-op (idempotent
  re-run)
- one-failure-doesn't-tank-the-batch: same coverage as resolver test
  but at handler level
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import List
from unittest.mock import MagicMock

from thestill.core.entity_resolver import EntityResolver
from thestill.core.queue_manager import Task, TaskStage, TaskStatus
from thestill.core.task_handlers import handle_resolve_entities
from thestill.models.entities import EntityMention, EntityType, MentionRole, ResolutionStatus
from thestill.models.podcast import Episode, Podcast


class StubReFinED:
    def __init__(self):
        self.predictions = {
            "Elon Musk": ("Q317521", "Elon Musk", "PER"),
            "OpenAI": ("Q21708200", "OpenAI", "ORG"),
        }

    def process_text(self, text: str):
        spans = []
        for surface, (qid, title, coarse) in self.predictions.items():
            idx = text.find(surface)
            if idx == -1:
                continue
            spans.append(
                SimpleNamespace(
                    text=surface,
                    coarse_type=coarse,
                    predicted_entity=SimpleNamespace(
                        wikidata_entity_id=qid,
                        wikipedia_entity_title=title,
                        human_readable_name=title,
                        description=None,
                    ),
                )
            )
        return spans


def _pending_mention(mention_id: int, surface: str) -> EntityMention:
    return EntityMention(
        id=mention_id,
        entity_id=None,
        resolution_status=ResolutionStatus.PENDING,
        episode_id="ep-uuid",
        segment_id=mention_id,
        start_ms=mention_id * 1000,
        end_ms=mention_id * 1000 + 5000,
        speaker="Host",
        role=MentionRole.MENTIONED,
        surface_form=surface,
        surface_label="person",
        quote_excerpt=f"… mentioning {surface} on the show …",
        confidence=0.9,
        extractor="gliner:test",
    )


def _make_task() -> Task:
    return Task(
        id=str(uuid.uuid4()),
        episode_id="ep-uuid",
        stage=TaskStage.RESOLVE_ENTITIES,
        status=TaskStatus.PROCESSING,
    )


def _make_podcast() -> Podcast:
    return Podcast(
        id=str(uuid.uuid4()),
        rss_url="https://example.com/feed.xml",
        title="Fixture",
        slug="fixture",
        description="",
    )


def _make_episode() -> Episode:
    return Episode(
        id="ep-uuid",
        external_id="e1",
        title="Fixture Ep",
        description="",
        audio_url="https://example.com/ep1.mp3",
    )


def _build_state(pending: List[EntityMention]):
    state = MagicMock()
    state.repository.get_episode.return_value = (_make_podcast(), _make_episode())
    state.entity_repository.list_pending_mentions.return_value = pending
    # find_duplicate_qid_pairs returns a list — default empty for the
    # happy path; tests that exercise alias-merge override.
    state.entity_repository.find_duplicate_qid_pairs.return_value = []
    # Spec §1.13.7 — handler now consults the override layer (returns
    # None for "no override") and the blacklist (returns False for
    # "not blacklisted") before resolution. MagicMock's auto-mock
    # would otherwise hand back a truthy stub.
    state.entity_repository.lookup_override.return_value = None
    state.entity_repository.is_blacklisted.return_value = False
    # Spec §1.13.5 — coref pass runs after resolution. Default empty
    # so the happy-path test isn't perturbed by a stubbed coref result.
    state.entity_repository.list_resolved_persons_for_episode.return_value = []
    state.entity_repository.list_unresolved_person_mentions.return_value = []
    state.entity_resolver = EntityResolver(preloaded_model=StubReFinED())
    return state


class TestHappyPath:
    def test_resolver_runs_upsert_resolve_cooccur(self):
        state = _build_state([_pending_mention(1, "Elon Musk"), _pending_mention(2, "OpenAI")])
        handle_resolve_entities(_make_task(), state)

        # Each mention should produce one upsert + one resolve_mention call
        assert state.entity_repository.upsert_entity.call_count == 2
        assert state.entity_repository.resolve_mention.call_count == 2

        # All resolve_mention calls were for 'resolved' status
        statuses = [c.kwargs["status"] for c in state.entity_repository.resolve_mention.call_args_list]
        assert statuses == ["resolved", "resolved"]

        # Cooccurrences rebuild scoped to this episode
        state.entity_repository.rebuild_cooccurrences.assert_called_once_with(episode_ids=["ep-uuid"])

    def test_unresolvable_mention_passes_null_entity_id(self):
        # Stub doesn't know about "Whoever" so it falls back to local-slug
        state = _build_state([_pending_mention(1, "Whoever")])
        handle_resolve_entities(_make_task(), state)

        resolve_call = state.entity_repository.resolve_mention.call_args_list[0]
        assert resolve_call.kwargs["status"] == "unresolvable"
        assert resolve_call.kwargs["entity_id"] is None


class TestNoPending:
    def test_episode_with_zero_pending_skips_resolver_but_rebuilds_cooccurrences(self):
        # Spec #28 §1.7 — the extractor can insert already-resolved
        # anchor + speaker mentions, leaving zero pending rows. The
        # cooccurrence graph still needs the pairs from those resolved
        # rows. ``rebuild_cooccurrences`` is cheap when the episode has
        # no resolved mentions (returns 0 after one indexed SELECT) so
        # always running it is the right default.
        state = _build_state([])
        handle_resolve_entities(_make_task(), state)

        state.entity_repository.upsert_entity.assert_not_called()
        state.entity_repository.resolve_mention.assert_not_called()
        state.entity_repository.rebuild_cooccurrences.assert_called_once_with(episode_ids=["ep-uuid"])


class TestInlineAliasMerge:
    def test_merges_qid_duplicates_touching_resolved_entities(self):
        state = _build_state([_pending_mention(1, "Elon Musk")])
        # Imagine an existing duplicate of person:elon-musk that
        # shares its QID. The handler should detect it and collapse.
        state.entity_repository.find_duplicate_qid_pairs.return_value = [
            ("Q317521", "person:elon-musk", "person:musk"),
        ]
        handle_resolve_entities(_make_task(), state)

        state.entity_repository.repoint_mentions.assert_called_once_with(
            from_entity_id="person:musk",
            to_entity_id="person:elon-musk",
        )
        state.entity_repository.delete_entity.assert_called_once_with("person:musk")

    def test_skips_duplicates_unrelated_to_this_episode(self):
        state = _build_state([_pending_mention(1, "Elon Musk")])
        # A duplicate involving entirely different entities should
        # not be collapsed by this episode's resolution.
        state.entity_repository.find_duplicate_qid_pairs.return_value = [
            ("Q478214", "company:tesla", "company:tesla-inc"),
        ]
        handle_resolve_entities(_make_task(), state)

        state.entity_repository.repoint_mentions.assert_not_called()
        state.entity_repository.delete_entity.assert_not_called()
