# Copyright 2025-2026 Thestill
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

"""Spec #81 - the last-attempt rule: search never depends on the linker.

``resolve-entities`` sits ahead of ``reindex`` in a linear chain. A linker
that cannot finish retries while there is budget; on the last attempt the
handler returns so the chain goes on, and the episode is marked as owed work.
"""

import uuid
from unittest.mock import MagicMock

import pytest

from thestill.core.entity_linking.live_linker import LinkerUnavailableError
from thestill.core.entity_linking.shared import EntityLinkerBrokenError, unresolvable_result
from thestill.core.entity_resolver import EntityResolverBrokenError
from thestill.core.queue_manager import Task, TaskStage, TaskStatus
from thestill.core.task_handlers import build_link_context, handle_resolve_entities, resolve_pending_mentions
from thestill.models.entities import EntityMention, EntityRecord, EntityType
from thestill.models.podcast import Episode, Podcast
from thestill.utils.exceptions import TransientError


def _mention(mention_id, surface):
    return EntityMention(
        id=mention_id,
        episode_id="ep-uuid",
        segment_id=mention_id,
        start_ms=0,
        end_ms=1,
        surface_form=surface,
        surface_label="person",
        quote_excerpt=f"... {surface} ...",
        confidence=0.9,
        extractor="gliner:test",
    )


def _task(retry_count=0, max_retries=3):
    return Task(
        id=str(uuid.uuid4()),
        episode_id="ep-uuid",
        stage=TaskStage.RESOLVE_ENTITIES,
        status=TaskStatus.PROCESSING,
        retry_count=retry_count,
        max_retries=max_retries,
    )


def _episode():
    return Episode(id="ep-uuid", external_id="e1", title="Ep", description="", audio_url="https://x/e.mp3")


def _state(linker, pending, episode=None):
    state = MagicMock()
    podcast = Podcast(id=str(uuid.uuid4()), rss_url="https://x/f.xml", title="Show", slug="show", description="")
    state.repository.get_episode.return_value = (podcast, episode or _episode())
    repo = state.entity_repository
    repo.list_pending_mentions.return_value = pending
    repo.find_duplicate_qid_pairs.return_value = []
    repo.lookup_override.return_value = None
    repo.is_blacklisted.return_value = False
    repo.list_resolved_persons_for_episode.return_value = []
    repo.list_unresolved_person_mentions.return_value = []
    repo.get_episode_anchors.return_value = []
    state.entity_resolver = linker
    return state


class FailingLinker:
    def __init__(self, failure):
        self.failure = failure
        self.contexts = []

    def resolve(self, mentions, *, is_blacklisted=None, context=None):
        self.contexts.append(context)
        raise self.failure


def _unavailable(results=()):
    return LinkerUnavailableError("wikidata down", results=list(results), unanswered_names=1)


@pytest.mark.parametrize("retry_count", [0, 1])
def test_an_outage_retries_while_there_is_budget(retry_count):
    state = _state(FailingLinker(_unavailable()), [_mention(1, "Ed Zitron")])
    with pytest.raises(TransientError) as err:
        handle_resolve_entities(_task(retry_count=retry_count), state)
    # item-class on purpose: an infra error is rescheduled without spending
    # budget, so the last attempt would never come during an outage
    assert err.value.error_class == "item"
    state.repository.update_entity_extraction_status.assert_not_called()


def test_an_outage_on_the_last_attempt_defers_and_lets_the_chain_continue():
    state = _state(FailingLinker(_unavailable()), [_mention(1, "Ed Zitron")])
    handle_resolve_entities(_task(retry_count=2), state)  # returns: reindex will run
    state.repository.update_entity_extraction_status.assert_called_once_with(
        episode_id="ep-uuid", status="linking_deferred"
    )
    state.entity_repository.resolve_mention.assert_not_called()  # nothing written off as unresolvable


@pytest.mark.parametrize("error", [EntityLinkerBrokenError("garbage"), EntityResolverBrokenError("refined broke")])
def test_a_broken_linker_retries_then_marks_failed_without_blocking_reindex(error):
    state = _state(FailingLinker(error), [_mention(1, "Ed Zitron")])
    with pytest.raises(TransientError):
        handle_resolve_entities(_task(retry_count=0), state)
    handle_resolve_entities(_task(retry_count=2), state)
    state.repository.update_entity_extraction_status.assert_called_once_with(episode_id="ep-uuid", status="failed")
    state.entity_repository.resolve_mention.assert_not_called()


def test_a_single_attempt_budget_defers_at_once():
    state = _state(FailingLinker(_unavailable()), [_mention(1, "Ed Zitron")])
    handle_resolve_entities(_task(retry_count=0, max_retries=1), state)
    state.repository.update_entity_extraction_status.assert_called_once()


def test_what_was_decided_before_the_outage_is_recorded():
    decided = unresolvable_result(_mention(1, "Nobody"))
    state = _state(FailingLinker(_unavailable([decided])), [_mention(1, "Nobody"), _mention(2, "Ed Zitron")])
    handle_resolve_entities(_task(retry_count=2), state)
    (call,) = state.entity_repository.resolve_mention.call_args_list
    assert call.kwargs["mention_id"] == 1 and call.kwargs["status"] == "unresolvable"


def test_a_success_settles_a_deferral_and_never_sets_a_status_itself():
    linker = MagicMock()
    linker.resolve.return_value = [unresolvable_result(_mention(1, "Nobody"))]
    state = _state(linker, [_mention(1, "Nobody")])
    handle_resolve_entities(_task(), state)
    state.repository.settle_linking_deferred.assert_called_once_with("ep-uuid")
    state.repository.update_entity_extraction_status.assert_not_called()


def test_a_failed_attempt_does_not_settle_the_deferral():
    state = _state(FailingLinker(_unavailable()), [_mention(1, "Ed Zitron")])
    handle_resolve_entities(_task(retry_count=2), state)
    state.repository.settle_linking_deferred.assert_not_called()


def test_the_linker_is_given_the_episode_context():
    linker = FailingLinker(_unavailable())
    state = _state(linker, [_mention(1, "Ed Zitron")])
    anchor = EntityRecord(id="person:cal-newport", type=EntityType.PERSON, canonical_name="Cal Newport")
    state.entity_repository.get_episode_anchors.return_value = ["person:cal-newport", "person:gone"]
    state.entity_repository.get_entity.side_effect = lambda eid: anchor if eid == "person:cal-newport" else None
    handle_resolve_entities(_task(retry_count=2), state)
    (context,) = linker.contexts
    assert (context.episode_id, context.podcast_title, context.episode_title) == ("ep-uuid", "Show", "Ep")
    assert context.anchor_names == ["Cal Newport"] and context.language == "en"


def test_human_overrides_are_recorded_even_when_the_linker_fails():
    repo = MagicMock()
    repo.is_blacklisted.return_value = False
    repo.find_duplicate_qid_pairs.return_value = []
    repo.list_resolved_persons_for_episode.return_value = []
    repo.list_unresolved_person_mentions.return_value = []
    override = {"override_kind": "force_unresolvable", "entity_id": None}
    repo.lookup_override.side_effect = lambda surface_form, episode_id: override if surface_form == "Forced" else None
    run = resolve_pending_mentions(
        repo,
        FailingLinker(EntityLinkerBrokenError("garbage")),
        [_mention(1, "Forced"), _mention(2, "Ed Zitron")],
        episode_id="ep-uuid",
    )
    assert isinstance(run.failure, EntityLinkerBrokenError)
    assert [c.kwargs["mention_id"] for c in repo.resolve_mention.call_args_list] == [1]


def test_build_link_context_normalises_the_language():
    repo = MagicMock()
    repo.get_episode_anchors.return_value = []
    podcast = Podcast(id="p", rss_url="https://x/f.xml", title="Emisija", slug="e", description="", language="hr-HR")
    assert build_link_context(repo, podcast, _episode()).language == "hr"
