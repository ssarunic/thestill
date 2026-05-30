# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License").

"""Unit tests for the entity detection queue + correction flow.

Both halves of ``core.entity_review`` are dependency-injected, so these
tests use lightweight fakes — no DB, no network, no AppState.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import List, Optional, Tuple

import pytest

from thestill.core.entity_review import CorrectionError, apply_correction, scan_entities_for_review
from thestill.models.entities import EntityRecord, EntityType

# ----------------------------------------------------------------------
# scan_entities_for_review
# ----------------------------------------------------------------------


class _ScanRepo:
    """Repo stub exposing only ``fetch_resolution_review_rows``."""

    def __init__(self, rows: List[dict]):
        self._rows = rows

    def fetch_resolution_review_rows(self) -> List[dict]:
        return self._rows


def _row(entity_id, type_, canonical, qid, surface, count, p31=()):
    return {
        "entity_id": entity_id,
        "type": type_,
        "canonical_name": canonical,
        "wikidata_qid": qid,
        "wikidata_instance_of": json.dumps(list(p31)),
        "surface_form": surface,
        "mention_count": count,
    }


class TestScan:
    def test_anthropic_shape_is_flagged_for_blacklist_and_ranked_top(self):
        rows = [
            # The bug: short name swallowed by a lowercase-noun extension.
            _row(
                "company:anthropic-principle",
                "company",
                "Anthropic principle",
                "Q240581",
                "Anthropic",
                1546,
                ["Q211364"],
            ),
            # A smaller real bug to prove impact ranking.
            _row("product:vlc-media-player", "product", "VLC media player", "Q171477", "VLC", 94),
        ]
        flags = scan_entities_for_review(_ScanRepo(rows))
        assert [f.entity_id for f in flags] == ["company:anthropic-principle", "product:vlc-media-player"]
        top = flags[0]
        assert top.suggested_action == "blacklist"
        assert top.suggested_qid == "Q240581"
        assert top.kinds == ["surface_extends_canonical"]
        assert top.evidence["affected_mentions"] == 1546

    def test_correct_coref_is_not_flagged(self):
        # "Donald"/"Trump" -> "Donald Trump": the extension is a capitalized
        # proper noun, not a lowercase common noun. Must NOT fire.
        rows = [
            _row("person:donald-trump", "person", "Donald Trump", "Q22686", "Donald", 100, ["Q5"]),
            _row("person:donald-trump", "person", "Donald Trump", "Q22686", "Trump", 200, ["Q5"]),
            _row("person:donald-trump", "person", "Donald Trump", "Q22686", "Donald Trump", 50, ["Q5"]),
        ]
        assert scan_entities_for_review(_ScanRepo(rows)) == []

    def test_parenthetical_disambiguator_is_benign(self):
        # "Slack" -> "Slack (software)": parenthetical, not a concept change.
        rows = [_row("company:slack", "company", "Slack (software)", "Q17130715", "Slack", 285, ["Q7397"])]
        # P31 Q7397 (software) under a company fallback -> classify says TOPIC,
        # so this DOES flag on the type signal — but not on surface-extends.
        flags = scan_entities_for_review(_ScanRepo(rows))
        assert all("surface_extends_canonical" not in f.kinds for f in flags)

    def test_p31_disagreement_flags_for_review(self):
        # "United States" stored as a company; P31 country -> topic.
        rows = [_row("company:united-states", "company", "United States", "Q30", "United States", 300, ["Q6256"])]
        flags = scan_entities_for_review(_ScanRepo(rows))
        assert len(flags) == 1
        f = flags[0]
        assert f.kinds == ["p31_says_other_type"]
        assert f.suggested_action == "review"
        assert f.evidence["p31_suggests"] == "topic"
        # impact (300) boosted by the disagreement multiplier (1.5).
        assert f.score == pytest.approx(450.0)

    def test_p31_unsupported_but_unclassifiable_is_excluded(self):
        # P31 Q211364 isn't in any allow-set, so classify keeps the type:
        # this is an allow-set gap, NOT a per-entity bug — must not appear
        # (the only reason Anthropic shows up is surface-extends).
        rows = [_row("company:some-principle", "company", "Some Principle", "Q999", "Some Principle", 5, ["Q211364"])]
        assert scan_entities_for_review(_ScanRepo(rows)) == []

    def test_limit_is_respected(self):
        rows = [
            _row(f"company:c{i}", "company", f"Thing {i} institute", f"Q{i}", f"Thing {i}", i + 1) for i in range(10)
        ]
        assert len(scan_entities_for_review(_ScanRepo(rows), limit=3)) == 3


# ----------------------------------------------------------------------
# apply_correction
# ----------------------------------------------------------------------


class _FakeRepo:
    """Records correction-relevant calls; configurable lookups."""

    def __init__(self, *, entities=None, mentions=None):
        self.entities = entities or {}  # id -> EntityRecord
        self._mentions = mentions or []  # list[(id, episode_id)]
        self.blacklist_calls: list = []
        self.override_calls: list = []
        self.upserted: list = []
        self.deleted_enrichment: list = []
        self.reset_ids: Optional[List[int]] = None

    def add_blacklist_entry(self, *, surface_form, wrong_qid, reason=None) -> int:
        self.blacklist_calls.append((surface_form, wrong_qid, reason))
        return 11

    def add_override(self, *, surface_form, episode_id, kind, entity_id=None, reason=None, created_by=None) -> int:
        self.override_calls.append((surface_form, episode_id, kind, entity_id, created_by))
        return 22

    def get_entity(self, entity_id) -> Optional[EntityRecord]:
        return self.entities.get(entity_id)

    def find_entity_by_qid(self, qid) -> Optional[EntityRecord]:
        return next((e for e in self.entities.values() if e.wikidata_qid == qid), None)

    def upsert_entity(self, entity: EntityRecord) -> str:
        self.entities[entity.id] = entity
        self.upserted.append(entity)
        return entity.id

    def delete_enrichment(self, entity_id) -> bool:
        self.deleted_enrichment.append(entity_id)
        return True

    def find_mention_ids_by_surface(self, surface_form, *, episode_id=None, statuses=()) -> List[Tuple[int, str]]:
        return list(self._mentions)

    def reset_mentions_to_pending(self, ids: List[int]) -> int:
        self.reset_ids = list(ids)
        return len(ids)


class _FakeQueue:
    def __init__(self):
        self.added: list = []

    def add_task(self, episode_id, stage):
        self.added.append((episode_id, stage))


class _FakeWikidata:
    def __init__(self, *, facts=None):
        self._facts = facts

    def fetch_facts(self, qid, *, language="en"):
        return self._facts


def _facts(label, *, p31=(), description=None):
    """Stand-in for a parsed ``WikidataEntity`` exposing ``entity_refs('P31')``."""
    return SimpleNamespace(
        label=label,
        description=description,
        entity_refs=lambda prop, _p=list(p31): _p if prop == "P31" else [],
    )


def _corp(repo, queue, **kw):
    return apply_correction(repo=repo, queue_manager=queue, **kw)


class TestApplyCorrectionBlacklist:
    def test_blacklist_writes_entry_resets_and_enqueues(self):
        repo = _FakeRepo(mentions=[(1, "ep1"), (2, "ep2"), (3, "ep1")])
        queue = _FakeQueue()
        result = _corp(
            repo, queue, action="blacklist", surface_form="Anthropic", wrong_qid="Q240581", reason="cosmology"
        )
        assert repo.blacklist_calls == [("Anthropic", "Q240581", "cosmology")]
        assert repo.reset_ids == [1, 2, 3]
        # Two distinct episodes, each enqueued once.
        assert sorted(ep for ep, _ in queue.added) == ["ep1", "ep2"]
        assert result.affected_mentions == 3
        assert result.episodes_enqueued == 2
        assert result.blacklist_id == 11
        assert '(("Anthropic", "Q240581"),)' in result.golden_snippet

    def test_blacklist_requires_wrong_qid(self):
        with pytest.raises(CorrectionError):
            _corp(_FakeRepo(), _FakeQueue(), action="blacklist", surface_form="Anthropic")

    def test_every_affected_episode_is_enqueued(self):
        # P2: always enqueue per affected episode. ``has_pending_task`` would
        # also match a 'processing' task that already snapshotted its pending
        # mentions and so would never see the rows we just reset.
        repo = _FakeRepo(mentions=[(1, "ep1"), (2, "ep2"), (3, "ep1")])
        queue = _FakeQueue()
        result = _corp(repo, queue, action="blacklist", surface_form="X", wrong_qid="Q1")
        assert sorted(ep for ep, _ in queue.added) == ["ep1", "ep2"]
        assert result.episodes_enqueued == 2
        assert result.affected_mentions == 3


class TestApplyCorrectionForceEntity:
    def test_force_existing_entity(self):
        target = EntityRecord(id="company:anthropic", type=EntityType.COMPANY, canonical_name="Anthropic")
        repo = _FakeRepo(entities={"company:anthropic": target}, mentions=[(1, "ep1")])
        queue = _FakeQueue()
        result = _corp(
            repo, queue, action="force_entity", surface_form="Anthropic", target_entity_id="company:anthropic"
        )
        assert repo.override_calls == [("Anthropic", None, "force_entity", "company:anthropic", "admin")]
        assert repo.upserted == []  # existing — no mint
        assert result.target_entity_id == "company:anthropic"
        assert result.created_entity_id is None

    def test_force_unknown_entity_raises(self):
        with pytest.raises(CorrectionError):
            _corp(_FakeRepo(), _FakeQueue(), action="force_entity", surface_form="X", target_entity_id="company:nope")

    def test_target_qid_reuses_existing_entity_without_minting(self):
        # P1: an entity already grounded to the QID is reused (no mint, no
        # network), so the resolve task's duplicate-QID merge can't later
        # delete the override target out from under us.
        existing = EntityRecord(
            id="company:anthropic", type=EntityType.COMPANY, canonical_name="Anthropic", wikidata_qid="Q110457238"
        )
        repo = _FakeRepo(entities={"company:anthropic": existing}, mentions=[(1, "ep1")])
        result = _corp(  # wikidata_client omitted on purpose — reuse short-circuits
            repo, _FakeQueue(), action="force_entity", surface_form="Anthropic", target_qid="Q110457238"
        )
        assert result.created_entity_id is None
        assert result.target_entity_id == "company:anthropic"
        assert repo.upserted == []
        assert repo.override_calls[0][3] == "company:anthropic"

    def test_mint_from_qid_creates_entity_and_clears_enrichment(self):
        wd = _FakeWikidata(facts=_facts("Anthropic", p31=["Q4830453"], description="AI safety company"))  # -> COMPANY
        repo = _FakeRepo(mentions=[(1, "ep1")])
        queue = _FakeQueue()
        result = _corp(
            repo,
            queue,
            wikidata_client=wd,
            action="force_entity",
            surface_form="Anthropic",
            target_qid="Q110457238",
            wrong_qid="Q240581",  # redirect combo: also blacklist the bad QID
        )
        assert result.created_entity_id == "company:anthropic"
        assert result.target_entity_id == "company:anthropic"
        minted = repo.entities["company:anthropic"]
        assert minted.type is EntityType.COMPANY
        assert minted.wikidata_qid == "Q110457238"
        assert repo.deleted_enrichment == ["company:anthropic"]
        # redirect combo blacklisted the wrong QID too
        assert repo.blacklist_calls == [("Anthropic", "Q240581", None)]
        assert repo.override_calls[0][2] == "force_entity"

    def test_mint_without_client_raises(self):
        with pytest.raises(CorrectionError):
            _corp(_FakeRepo(), _FakeQueue(), action="force_entity", surface_form="X", target_qid="Q1")

    def test_mint_missing_wikidata_entity_raises(self):
        wd = _FakeWikidata(facts=None)
        with pytest.raises(CorrectionError):
            _corp(
                _FakeRepo(), _FakeQueue(), wikidata_client=wd, action="force_entity", surface_form="X", target_qid="Q1"
            )


class TestApplyCorrectionMisc:
    def test_invalid_action_raises(self):
        with pytest.raises(CorrectionError):
            _corp(_FakeRepo(), _FakeQueue(), action="nonsense", surface_form="X")

    def test_blank_surface_raises(self):
        with pytest.raises(CorrectionError):
            _corp(_FakeRepo(), _FakeQueue(), action="drop", surface_form="   ")

    def test_drop_writes_override_only(self):
        repo = _FakeRepo(mentions=[(1, "ep1")])
        queue = _FakeQueue()
        result = _corp(repo, queue, action="drop", surface_form="the thing", episode_id="ep1")
        assert repo.override_calls == [("the thing", "ep1", "drop", None, "admin")]
        assert repo.blacklist_calls == []
        assert result.affected_mentions == 1
