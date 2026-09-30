"""Which anchor-scan rows were read out of a longer name GLiNER found."""

from __future__ import annotations

from unittest.mock import MagicMock

from thestill.core.anchor_scan_repair import apply_anchor_scan_repair, free_occurrences, plan_anchor_scan_repair
from thestill.repositories.entity_repository import AnchorScanOverlap

EP = "11111111-1111-4111-8111-111111111111"
DOGUS = "person:dogus-cubuk"


def _overlap(mention_id, surface, quote, *, other="Dogus Cubuk", entity=DOGUS, other_entity=DOGUS, segment=1):
    return AnchorScanOverlap(
        mention_id=mention_id,
        episode_id=EP,
        segment_id=segment,
        entity_id=entity,
        surface_form=surface,
        quote_excerpt=quote,
        other_entity_id=other_entity,
        other_surface_form=other,
    )


QUOTE = "Liam Fedus and Dogus Cubuk are building a company to change that."


class TestFreeOccurrences:
    def test_counts_only_occurrences_outside_the_longer_name(self):
        assert free_occurrences("Dogus", ["Dogus Cubuk"], QUOTE) == (1, 0)
        assert free_occurrences("Dogus", ["Dogus Cubuk"], "Dogus Cubuk, or just Dogus.") == (2, 1)

    def test_whole_words_only(self):
        assert free_occurrences("Tom", ["Tom Hanks"], "Tomas met Tom Hanks.") == (1, 0)


class TestPlan:
    def test_parts_of_a_name_gliner_found_are_deleted(self):
        plan = plan_anchor_scan_repair([_overlap(10, "Dogus", QUOTE), _overlap(11, "Cubuk", QUOTE)])

        assert sorted(d.mention_id for d in plan.deletions) == [10, 11]
        assert not any(d.misattributed for d in plan.deletions)
        assert plan.deletions[0].inside == "Dogus Cubuk"
        assert plan.episodes == [EP]

    def test_a_namesake_inside_someone_elses_name_is_misattributed(self):
        quote = "You sounded like Gary Neville doing commentary work."
        plan = plan_anchor_scan_repair(
            [
                _overlap(
                    20,
                    "Gary",
                    quote,
                    other="Gary Neville",
                    entity="person:gary-lineker",
                    other_entity="person:gary-neville",
                )
            ]
        )

        assert [d.mention_id for d in plan.deletions] == [20]
        assert plan.deletions[0].misattributed
        assert plan.summary()["  credited to the wrong entity"] == 1

    def test_a_row_whose_quote_shows_the_name_on_its_own_is_kept(self):
        plan = plan_anchor_scan_repair([_overlap(30, "Dogus", "Dogus Cubuk, or just Dogus.")])

        assert plan.deletions == []
        assert plan.kept_ambiguous == 1

    def test_only_the_surplus_goes_when_the_name_also_stands_alone(self):
        # Segment says "Dogus Cubuk" (nested scan row, quote A) and later a
        # lone "Dogus" (real scan row, quote B): exactly one row goes.
        nested = _overlap(40, "Dogus", QUOTE)
        alone = _overlap(41, "Dogus", "Later, Dogus explained the lab.")
        plan = plan_anchor_scan_repair([nested, alone])

        assert [d.mention_id for d in plan.deletions] == [40]
        assert plan.kept_ambiguous == 1

    def test_identical_rows_keep_one_per_free_occurrence(self):
        # Two scan rows share a quote that shows one nested and one free
        # "Dogus": they can't be told apart, so both stay.
        quote = "Dogus Cubuk, or just Dogus."
        plan = plan_anchor_scan_repair([_overlap(50, "Dogus", quote), _overlap(51, "Dogus", quote)])

        assert plan.deletions == []

    def test_a_longer_name_that_merely_contains_the_letters_is_ignored(self):
        # "Tom" is not a whole word of "Tomas Lindahl".
        plan = plan_anchor_scan_repair([_overlap(60, "Tom", "Tomas Lindahl said so.", other="Tomas Lindahl")])

        assert plan.deletions == []
        assert plan.kept_ambiguous == 0

    def test_prefers_the_entitys_own_name_when_both_contain_it(self):
        quote = "Dogus Cubuk and Dogus Cubuk Labs, the company."
        plan = plan_anchor_scan_repair(
            [
                _overlap(70, "Dogus", quote, other="Dogus Cubuk Labs", other_entity="company:dogus-cubuk-labs"),
                _overlap(70, "Dogus", quote),
            ]
        )

        assert [(d.inside, d.misattributed) for d in plan.deletions] == [("Dogus Cubuk", False)]


def test_apply_deletes_and_recounts_cooccurrences_for_affected_episodes():
    repo = MagicMock()
    repo.delete_mentions_by_ids.return_value = 2
    repo.rebuild_cooccurrences.return_value = 5
    plan = plan_anchor_scan_repair([_overlap(10, "Dogus", QUOTE), _overlap(11, "Cubuk", QUOTE)])

    result = apply_anchor_scan_repair(repo, plan)

    repo.delete_mentions_by_ids.assert_called_once_with([10, 11])
    repo.rebuild_cooccurrences.assert_called_once_with(episode_ids=[EP])
    assert (result.deleted, result.episodes, result.cooccurrence_pairs) == (2, [EP], 5)
