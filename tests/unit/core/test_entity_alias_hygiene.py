"""Alias hygiene (``core/entity_alias_hygiene.py``).

The cases are the real ones from the 2026-09 corpus: what the pre-#79
resolver bug stored ("tariffs" on Donald Trump), what a lexical-only rule
would wrongly throw away ("AWS", "Coke"), and what post-fix "evidence" must
not be allowed to protect ("AI" on Geoffrey Hinton).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from thestill.core.entity_alias_hygiene import (
    RESOLVER_FIX_CUTOFF,
    apply_alias_cleanup,
    is_related_alias,
    plan_alias_cleanup,
)
from thestill.core.entity_anchor import _surfaces_for
from thestill.models.entities import EntityRecord, EntityType
from thestill.repositories.entity_repository import AliasEvidence


class TestIsRelatedAlias:
    @pytest.mark.parametrize(
        "alias, canonical",
        [
            ("Musk", "Elon Musk"),  # token
            ("Zuck", "Mark Zuckerberg"),  # substring
            ("@elonmusk", "Elon Musk"),  # handle
            ("FDA", "Food and Drug Administration"),  # initials, function words skipped
            ("F.D.R.", "Franklin D. Roosevelt"),  # dotted initials
            ("AWS", "Amazon Web Services"),
            ("Haaland", "Alf-Inge Håland"),  # transliteration
            ("Semmelweiss", "Ignaz Semmelweis"),  # spelling
            ("Mbappe", "Kylian Mbappé"),  # accents
            ("Open AI", "OpenAI"),  # run-together
            ("GM", "General Motors"),  # short, but initials
            ("X", "X Corp"),  # short, but a whole token
        ],
    )
    def test_related(self, alias, canonical):
        assert is_related_alias(alias, canonical) is True

    @pytest.mark.parametrize(
        "alias, canonical",
        [
            ("tariffs", "Donald Trump"),
            ("you guys", "Elon Musk"),
            ("price", "Elon Musk"),
            ("OpenAI", "Anthropic principle"),
            ("AI", "Geoffrey Hinton"),
            ("ai", "OpenAI"),  # a two-letter fragment is not a nickname
            ("Ryan Carson", "Treehouse (company)"),
            ("Coke", "Coca-Cola"),  # genuinely unrelated on its face: needs evidence
            ("", "Elon Musk"),
            ("Elon Musk", "Elon Musk"),  # the name itself is not an alias
            ("ELON MUSK", "Elon Musk"),
            ("Kyutai", "Kyūtai"),  # accent-only variant is the name itself
        ],
    )
    def test_unrelated(self, alias, canonical):
        assert is_related_alias(alias, canonical) is False


def _ev(entity_id, canonical, alias, *, type="person", since=0, before=0, anchor=0, coref=0):
    return AliasEvidence(
        entity_id=entity_id,
        entity_type=type,
        canonical_name=canonical,
        alias=alias,
        direct_since_fix=since,
        direct_before_fix=before,
        anchor_mentions=anchor,
        coref_mentions=coref,
    )


class TestPlan:
    def _verdict(self, row, **kwargs):
        [verdict] = plan_alias_cleanup([row], **kwargs).verdicts
        return verdict

    def test_related_alias_is_kept_without_any_evidence(self):
        v = self._verdict(_ev("person:elon-musk", "Elon Musk", "Musk"))
        assert (v.keep, v.reason) == (True, "related")

    def test_bug_era_alias_is_removed_and_its_mentions_are_undone(self):
        v = self._verdict(_ev("person:donald-trump", "Donald Trump", "president", before=2, anchor=20, coref=170))
        assert (v.keep, v.reason) == (False, "unsupported")
        assert v.mentions_to_delete == 20  # anchor mentions exist only because of the alias
        assert v.mentions_to_reset == 172  # real extractions, wrongly linked

    def test_unrelated_company_alias_survives_on_post_fix_evidence(self):
        v = self._verdict(_ev("company:coca-cola", "Coca-Cola", "Coke", type="company", since=21))
        assert (v.keep, v.reason) == (True, "evidence")

    def test_pre_fix_links_are_not_evidence(self):
        v = self._verdict(_ev("company:openai", "OpenAI", "Anthropic", type="company", before=5))
        assert v.keep is False

    def test_people_never_keep_an_alias_on_evidence_alone(self):
        """On the real corpus this path protected "AI" on Geoffrey Hinton
        (70 junk anchor mentions) and "Brian Armstrong" on a wrestler."""
        v = self._verdict(_ev("person:geoffrey-hinton", "Geoffrey Hinton", "AI", since=4, anchor=70))
        assert v.keep is False and v.mentions_to_delete == 70

    def test_short_aliases_need_more_evidence(self):
        """The resolver's half-coverage floor is one character for "AI"."""
        weak = self._verdict(_ev("topic:parallel", "Parallel computing", "AI", type="topic", since=1))
        strong = self._verdict(_ev("company:pwc", "PricewaterhouseCoopers", "PwC", type="company", since=8))
        assert weak.keep is False and strong.keep is True

    def test_allowlist_beats_the_rule_case_insensitively(self):
        row = _ev("person:jesus", "Jesus", "Christ", since=3)
        assert self._verdict(row).keep is False
        v = self._verdict(row, allowlist=[("person:jesus", "christ")])
        assert (v.keep, v.reason) == (True, "allowlisted")

    def test_alias_repeating_the_name_is_dropped_but_its_mentions_are_left_alone(self):
        v = self._verdict(_ev("topic:status-quo", "status quo", "Status Quo", type="topic", since=9, before=4))
        assert (v.keep, v.reason) == (False, "redundant")
        assert v.mentions_to_delete == 0 and v.mentions_to_reset == 0

    def test_summary_counts(self):
        plan = plan_alias_cleanup(
            [
                _ev("person:elon-musk", "Elon Musk", "Musk"),
                _ev("person:elon-musk", "Elon Musk", "price", before=1, anchor=30),
                _ev("person:elon-musk", "Elon Musk", "elon musk"),
                _ev("company:coca-cola", "Coca-Cola", "Coke", type="company", since=2),
            ]
        )
        assert plan.summary() == {
            "aliases_total": 4,
            "aliases_removed": 2,
            "aliases_removed_redundant": 1,
            "entities_touched": 1,
            "anchor_mentions_deleted": 30,
            "mentions_reset_to_pending": 1,
            "kept_evidence": 1,
            "kept_related": 1,
        }

    def test_cutoff_is_the_day_after_the_resolver_fix(self):
        assert RESOLVER_FIX_CUTOFF == datetime(2026, 5, 9, tzinfo=timezone.utc)


class FakeRepo:
    def __init__(self, entity):
        self.entity = entity
        self.calls = []
        self.direct = {"price": [(1, "ep-a"), (2, "ep-b")]}

    def delete_mentions_by_entity_surface(self, entity_id, surface, *, methods):
        self.calls.append(("delete", entity_id, surface, methods))
        return 30 if surface == "price" else 0

    def find_mention_ids_by_entity_surface(self, entity_id, surface, *, methods):
        self.calls.append(("find", entity_id, surface, methods))
        return self.direct.get(surface, [])

    def reset_mentions_to_pending(self, ids):
        self.calls.append(("reset", tuple(ids)))
        return len(ids)

    def get_entity(self, entity_id):
        return self.entity

    def replace_aliases(self, entity_id, aliases):
        self.calls.append(("replace", entity_id, tuple(aliases)))
        return True

    def rebuild_cooccurrences(self, *, episode_ids=None):
        self.calls.append(("rebuild", episode_ids))
        return 7


class FakeQueue:
    def __init__(self):
        self.tasks = []

    def add_task(self, episode_id, stage):
        self.tasks.append((episode_id, stage.value))


class TestApply:
    def _setup(self):
        entity = EntityRecord(
            id="person:elon-musk",
            type=EntityType.PERSON,
            canonical_name="Elon Musk",
            aliases=["Musk", "price", "Elon Musk"],
        )
        plan = plan_alias_cleanup(
            [
                _ev("person:elon-musk", "Elon Musk", "Musk"),
                _ev("person:elon-musk", "Elon Musk", "price", before=2, anchor=30),
                _ev("person:elon-musk", "Elon Musk", "Elon Musk", since=50),
            ]
        )
        return FakeRepo(entity), plan

    def test_deletes_anchor_resets_real_mentions_rewrites_aliases_then_rebuilds(self):
        repo, plan = self._setup()
        queue = FakeQueue()
        result = apply_alias_cleanup(repo, plan, queue_manager=queue)

        assert ("delete", "person:elon-musk", "price", ("anchor",)) in repo.calls
        assert ("find", "person:elon-musk", "price", ("direct", "coref")) in repo.calls
        assert ("reset", (1, 2)) in repo.calls
        assert ("replace", "person:elon-musk", ("Musk",)) in repo.calls
        assert repo.calls[-1] == ("rebuild", None)  # full rebuild, and last
        assert (result.aliases_removed, result.entities_updated) == (2, 1)
        assert (result.anchor_mentions_deleted, result.mentions_reset) == (30, 2)
        assert queue.tasks == [("ep-a", "resolve-entities"), ("ep-b", "resolve-entities")]

    def test_redundant_alias_never_touches_mentions(self):
        repo, plan = self._setup()
        apply_alias_cleanup(repo, plan)
        touched = {call[2] for call in repo.calls if call[0] in ("delete", "find")}
        assert touched == {"price"}

    def test_without_a_queue_mentions_are_reset_but_nothing_is_enqueued(self):
        repo, plan = self._setup()
        result = apply_alias_cleanup(repo, plan, queue_manager=None)
        assert result.mentions_reset == 2 and result.episodes_enqueued == 0
        assert result.episodes_affected == ["ep-a", "ep-b"]

    def test_nothing_to_remove_writes_nothing(self):
        repo, _ = self._setup()
        result = apply_alias_cleanup(repo, plan_alias_cleanup([_ev("person:elon-musk", "Elon Musk", "Musk")]))
        assert repo.calls == [] and result.aliases_removed == 0


class TestPrevention:
    """A polluted alias must be inert even before the data is cleaned."""

    def test_anchor_surfaces_skip_unrelated_stored_aliases(self):
        musk = EntityRecord(
            id="person:elon-musk",
            type=EntityType.PERSON,
            canonical_name="Elon Musk",
            aliases=["price", "you guys", "@elonmusk"],
        )
        surfaces = {s.lower() for s in _surfaces_for(musk)}
        assert {"elon musk", "musk", "elon", "@elonmusk"} <= surfaces
        assert not ({"price", "you guys"} & surfaces)

    def test_coref_ignores_unrelated_stored_aliases(self):
        from thestill.core.entity_coref import _candidates_for as matcher

        trump = EntityRecord(
            id="person:donald-trump",
            type=EntityType.PERSON,
            canonical_name="Donald Trump",
            aliases=["president", "The Donald"],
        )
        assert matcher("president", [trump]) == []
        assert matcher("Donald", [trump]) == [trump]
