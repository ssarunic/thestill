"""Spec #47 — handle_enrich_entities task handler tests.

The terminal entity-branch stage fetches Wikidata/Wikipedia display data
for the entities mentioned in the batch's episodes. These tests pin the
handler's orchestration contract with a stubbed enricher (no network):

- coalesces sibling episodes and unions their scoped enrichment selections
- enriches each candidate and persists via ``upsert_enrichment``
- caps the burst at ``enrichment_max_per_task`` (overflow → scheduled sweep)
- a single entity's failure must not abort the batch (spec #42 FM-1)
- entities without a QID are skipped defensively
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

from thestill.core.queue_manager import Task, TaskStage, TaskStatus
from thestill.core.task_handlers import handle_enrich_entities
from thestill.models.entities import EntityRecord, EntityType
from thestill.models.enrichment import EnrichmentStatus, EntityEnrichment


def _make_task(episode_id: str = "ep-1") -> Task:
    return Task(
        id=str(uuid.uuid4()),
        episode_id=episode_id,
        stage=TaskStage.ENRICH_ENTITIES,
        status=TaskStatus.PROCESSING,
    )


def _entity(eid: str, *, qid: str | None = "Q1") -> EntityRecord:
    slug = eid.split(":", 1)[1]
    return EntityRecord(
        id=eid,
        type=EntityType.PERSON,
        canonical_name=slug.replace("-", " ").title(),
        wikidata_qid=qid,
    )


def _enrichment(eid: str, *, wd=EnrichmentStatus.OK, wp=EnrichmentStatus.OK, headline="x") -> EntityEnrichment:
    now = datetime.now(timezone.utc)
    en = EntityEnrichment(entity_id=eid, schema_version=1, created_at=now, updated_at=now, headline=headline)
    en.wikidata_status = wd
    en.wikipedia_status = wp
    return en


class _StubEnricher:
    """Records enrich() calls; returns a per-entity enrichment or raises."""

    def __init__(self, *, raise_for: set[str] | None = None):
        self.calls: list[str] = []
        self._raise_for = raise_for or set()

    def enrich(self, entity: EntityRecord) -> EntityEnrichment:
        self.calls.append(entity.id)
        if entity.id in self._raise_for:
            raise RuntimeError("wikidata down")
        return _enrichment(entity.id)


def _build_state(
    *,
    coalesced: list[str],
    needing: dict[str, list[str]],
    entities: dict[str, EntityRecord],
    enricher: _StubEnricher,
    max_per_task: int = 200,
):
    state = MagicMock()
    state.config.enrichment_request_delay_sec = 0  # no sleeping in tests
    state.config.enrichment_max_age_days = 30
    state.config.enrichment_max_per_task = max_per_task
    state.queue_manager.claim_pending_for_coalescing.return_value = coalesced
    state.entity_repository.entity_ids_needing_enrichment.side_effect = (
        lambda *, episode_id, **kw: list(needing.get(episode_id, []))
    )
    state.entity_repository.get_entity.side_effect = lambda eid: entities.get(eid)
    # Pre-seed the cached enricher so the factory returns the stub and never
    # builds real HTTP clients.
    state.entity_enricher = enricher
    return state


class TestHappyPath:
    def test_coalesces_episodes_and_enriches_union(self):
        enricher = _StubEnricher()
        state = _build_state(
            coalesced=["ep-2"],  # ep-1 is the task's own episode
            needing={"ep-1": ["person:a", "person:b"], "ep-2": ["person:b", "person:c"]},
            entities={k: _entity(k) for k in ("person:a", "person:b", "person:c")},
            enricher=enricher,
        )
        handle_enrich_entities(_make_task("ep-1"), state)

        # Union of {a,b} ∪ {b,c} = {a,b,c}, deduped, each enriched once.
        assert sorted(enricher.calls) == ["person:a", "person:b", "person:c"]
        assert state.entity_repository.upsert_enrichment.call_count == 3
        # Scoped selection queried per coalesced episode.
        assert state.entity_repository.entity_ids_needing_enrichment.call_count == 2


class TestCap:
    def test_caps_attempts_and_defers_overflow(self):
        enricher = _StubEnricher()
        ids = [f"person:e{i}" for i in range(5)]
        state = _build_state(
            coalesced=[],
            needing={"ep-1": ids},
            entities={k: _entity(k) for k in ids},
            enricher=enricher,
            max_per_task=2,
        )
        handle_enrich_entities(_make_task("ep-1"), state)

        # Only the first 2 (sorted) are attempted; the rest wait for the sweep.
        assert enricher.calls == ["person:e0", "person:e1"]
        assert state.entity_repository.upsert_enrichment.call_count == 2


class TestFailureIsolation:
    def test_one_entity_failure_does_not_abort_batch(self):
        # Spec #42 FM-1 — a transient enrich error on one entity is swallowed
        # and the remaining entities still get processed.
        enricher = _StubEnricher(raise_for={"person:b"})
        ids = ["person:a", "person:b", "person:c"]
        state = _build_state(
            coalesced=[],
            needing={"ep-1": ids},
            entities={k: _entity(k) for k in ids},
            enricher=enricher,
        )
        handle_enrich_entities(_make_task("ep-1"), state)

        assert enricher.calls == ids  # all attempted despite b raising
        # b's failed enrich is not persisted; a and c are.
        persisted = [c.args[0].entity_id for c in state.entity_repository.upsert_enrichment.call_args_list]
        assert persisted == ["person:a", "person:c"]


class TestSkips:
    def test_entities_without_qid_are_skipped(self):
        enricher = _StubEnricher()
        state = _build_state(
            coalesced=[],
            needing={"ep-1": ["person:a", "person:noqid"]},
            entities={
                "person:a": _entity("person:a"),
                "person:noqid": _entity("person:noqid", qid=None),
            },
            enricher=enricher,
        )
        handle_enrich_entities(_make_task("ep-1"), state)

        assert enricher.calls == ["person:a"]
        assert state.entity_repository.upsert_enrichment.call_count == 1

    def test_no_candidates_is_a_noop(self):
        enricher = _StubEnricher()
        state = _build_state(coalesced=[], needing={}, entities={}, enricher=enricher)
        handle_enrich_entities(_make_task("ep-1"), state)

        assert enricher.calls == []
        state.entity_repository.upsert_enrichment.assert_not_called()
