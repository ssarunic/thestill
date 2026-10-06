"""Spec #92 — parsing the Resource List and grounding it in the transcript.

All pure: summaries are strings, transcripts are built inline, nothing is
mocked.
"""

from __future__ import annotations

import pytest

from thestill.core.summary_resources import (
    PlanContext,
    ResourceParseError,
    infer_kind,
    is_contract_line,
    normalize_kind,
    parse_resource_line,
    parse_resource_list,
    plan_resources,
)
from thestill.models.annotated_transcript import AnnotatedTranscript

# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------


class TestParseLine:
    def test_the_dominant_bold_colon_shape(self):
        item = parse_resource_line("**Codex:** The AI tool Yana uses for coding. [03:00](?t=180&cite=c20)")
        assert (item.name, item.kind, item.cite_id, item.raw_label) == ("Codex", None, "c20", "03:00")
        assert item.gloss == "The AI tool Yana uses for coding"

    def test_name_kind_timestamp(self):
        item = parse_resource_line("Mad Men (TV Show) [12:09](?t=729&cite=c3)")
        assert (item.name, item.kind, item.cite_id) == ("Mad Men", "tv", "c3")

    def test_compound_free_form_kind(self):
        assert parse_resource_line("Paul Kedrosky (Investor/Guest) [02:38]").kind == "person"
        assert parse_resource_line("Ramp (Spending Data/Tool) [12:09]").kind == "tool"

    def test_contract_shape(self):
        text = "**Margin Call** (film): Jeremy Irons on the music stopping [44:45](?t=2685&cite=c7)"
        item = parse_resource_line(text)
        assert (item.name, item.kind, item.contract) == ("Margin Call", "film", True)
        assert item.gloss == "Jeremy Irons on the music stopping"
        assert is_contract_line(text)
        assert not is_contract_line("**Codex:** a tool [03:00]")

    def test_a_parenthetical_that_is_not_a_kind_becomes_gloss(self):
        item = parse_resource_line("**Grok (xAI):** Used by Paul. [44:58](?t=2698&cite=c29)")
        assert (item.name, item.gloss) == ("Grok", "Used by Paul (xAI)")

    def test_decoration_is_stripped(self):
        assert parse_resource_line('**"Pacing the Frontier" Petition:** statement [21:28]').name == (
            "Pacing the Frontier Petition"
        )
        assert parse_resource_line("*Die with Zero* (Book) by Bill Perkins [12:00]").name == (
            "Die with Zero by Bill Perkins"
        )

    @pytest.mark.parametrize("name", ["Bed, Bath & Beyond", "Pride & Prejudice", "Johnson & Johnson"])
    def test_names_that_look_combined_are_one_name_with_no_fallback(self, name):
        item = parse_resource_line(f"**{name}:** something [01:00]")
        assert item.name == name and item.fallbacks == ()

    def test_slash_offers_parts_as_fallbacks(self):
        item = parse_resource_line("**Vercel / GitHub:** Hosting. [25:40](?t=1540&cite=c23)")
        assert item.name == "Vercel / GitHub"
        assert item.fallbacks == (("Vercel", None), ("GitHub", None))

    def test_by_offers_title_and_full_name_author(self):
        item = parse_resource_line("*The Art of War* (Book) by Sun Tzu [05:00]")
        assert item.fallbacks == (("The Art of War", "book"), ("Sun Tzu", "person"))

    def test_by_inside_a_title_offers_nothing(self):
        assert parse_resource_line("**Stand by Me** (film): a film [05:00]").fallbacks == ()


class TestParseList:
    def test_every_section_8_is_read_and_names_deduplicated(self):
        md = (
            "## 8. 📚 Resource List\n* **Ramp:** spending data [12:09](?t=729&cite=c1)\n\n"
            "## 9. 💩 BS\n* **Not a resource:** x\n\n---\n\n"
            "## 8. 📚 Resource List\n* **ramp:** again [40:00](?t=2400&cite=c9)\n* **Vanguard:** funds [49:34]\n"
        )
        items = parse_resource_list(md)
        assert [(i.name, i.cite_id) for i in items] == [("Ramp", "c1"), ("Vanguard", None)]

    def test_no_section_is_no_items(self):
        assert parse_resource_list("## 1. Gist\nhello") == []

    def test_a_section_of_unreadable_bullets_raises(self):
        with pytest.raises(ResourceParseError):
            parse_resource_list("## 8. Resource List\n* [12:09]\n* **:** [01:00]\n")

    def test_one_unreadable_bullet_does_not_lose_the_rest(self):
        items = parse_resource_list("## 8. Resource List\n* [12:09]\n* **Ramp:** data [01:00]\n")
        assert [i.name for i in items] == ["Ramp"]


class TestKinds:
    def test_normalize(self):
        assert normalize_kind("TV Show") == "tv"
        assert normalize_kind("Book by Nir Eyal") == "book"
        assert normalize_kind("xAI") is None

    def test_earliest_gloss_word_wins(self):
        assert infer_kind(None, "Stripe's internal data querying/visualization tool") == "tool"
        assert infer_kind(None, "Author of Three Felonies a Day") == "person"
        assert infer_kind(None, "Yana's AI fashion brand") == "company"
        assert infer_kind(None, "2,000-year-old archaeological mystery") is None
        assert infer_kind("film", "a podcast about films") == "film"


# ---------------------------------------------------------------------------
# Admission and expansion
# ---------------------------------------------------------------------------


def _transcript(*segments, offset=0.0):
    """segments: (start_s, text) — each 10 s long."""
    return AnnotatedTranscript.model_validate(
        {
            "episode_id": "ep",
            "playback_time_offset_seconds": offset,
            "segments": [
                {"id": i, "start": start, "end": start + 10, "speaker": "Host", "text": text, "kind": "content"}
                for i, (start, text) in enumerate(segments)
            ],
        }
    )


def _plan(md, transcript, ctx=None, **kw):
    return plan_resources(parse_resource_list(md), transcript, ctx or PlanContext(), **kw)


def _section(*lines):
    return "## 8. Resource List\n" + "\n".join(f"* {line}" for line in lines) + "\n"


class TestAdmission:
    def test_a_multi_word_name_is_admitted_anywhere(self):
        tx = _transcript((10, "Let's talk markets."), (900, "I've been watching Mad Men again."))
        plan = _plan(_section("Mad Men (TV Show) [00:10](?t=10&cite=c1)"), tx)
        (g,) = plan.grounded
        assert (g.name, g.outcome, g.surface_label) == ("Mad Men", "elsewhere", "product")

    def test_near_uses_the_cited_segment(self):
        tx = _transcript((700, "Mad Men is the greatest show."))
        (g,) = _plan(_section("Mad Men (TV Show) [12:09](?t=729&cite=c1)"), tx).grounded
        assert g.outcome == "near"

    def test_a_single_word_needs_near(self):
        tx = _transcript((60, "Ramp data says so."), (2000, "Ramp again."))
        far = _plan(_section("**Ramp:** spending data [10:00](?t=600&cite=c1)"), tx)
        assert far.grounded == () and far.stats["ungrounded"] == 1
        near = _plan(_section("**Ramp:** spending data [01:00](?t=60&cite=c1)"), tx)
        assert [g.name for g in near.grounded] == ["Ramp"]

    def test_a_single_word_is_case_sensitive(self):
        tx = _transcript((60, "Prices ramp up every year."))
        assert _plan(_section("**Ramp:** data [01:00]"), tx).grounded == ()

    def test_a_first_name_never_admits_a_person(self):
        tx = _transcript((150, "Paul, great to see you."), (160, "Thanks, Paul."))
        plan = _plan(_section("Paul Kedrosky (Investor/Guest) [02:38]"), tx)
        assert plan.grounded == () and plan.stats["ungrounded"] == 1
        assert plan.hints() == {}

    def test_an_admitted_person_scans_the_surname_never_the_first_name(self):
        tx = _transcript((150, "You spoke to Paul Kedrosky yesterday."), (4200, "Kedrosky brings data."))
        (g,) = _plan(_section("Paul Kedrosky (Investor/Guest) [02:38]"), tx).grounded
        assert g.scan_surfaces == ("Paul Kedrosky", "Kedrosky")
        assert set(_plan(_section("Paul Kedrosky (Investor/Guest) [02:38]"), tx).hints()) == {
            "paul kedrosky",
            "kedrosky",
        }

    def test_raw_label_works_without_a_citation(self):
        tx = _transcript((150, "Ramp is great."))
        assert [g.name for g in _plan(_section("**Ramp:** data [02:38]"), tx).grounded] == ["Ramp"]

    def test_playback_offset_is_applied(self):
        # Segment at raw 100 s is playback 130 s with a 30 s pre-roll.
        tx = _transcript((100, "Ramp data."), offset=30.0)
        assert _plan(_section("**Ramp:** data [02:10]"), tx, window_s=5).grounded != ()

    def test_leading_the_is_optional(self):
        tx = _transcript((10, "Have you read Art of War?"))
        assert [g.name for g in _plan(_section("**The Art of War:** a book"), tx).grounded] == ["The Art of War"]

    def test_anchors_are_dropped(self):
        tx = _transcript((10, "Ed Elson here with Scott Galloway."))
        ctx = PlanContext(anchor_surfaces=frozenset({"ed elson", "elson", "ed"}))
        plan = _plan(_section("Ed Elson (Host) [00:10]"), tx, ctx=ctx)
        assert plan.grounded == () and plan.stats["anchor"] == 1


class TestFallbacks:
    def test_the_whole_name_wins_when_it_grounds(self):
        tx = _transcript((10, "Pride & Prejudice is a great book."))
        (g,) = _plan(_section("**Pride & Prejudice:** a novel [00:10]"), tx).grounded
        assert g.name == "Pride & Prejudice"

    def test_slash_parts_ground_separately_when_the_whole_does_not(self):
        tx = _transcript((1540, "We host on Vercel and keep code in GitHub."))
        plan = _plan(_section("**Vercel / GitHub:** hosting [25:40](?t=1540&cite=c1)"), tx)
        assert sorted(g.name for g in plan.grounded) == ["GitHub", "Vercel"]

    def test_an_ampersand_item_that_does_not_ground_whole_is_dropped(self):
        tx = _transcript((10, "Nebius Group raised money. CoreWeave too."))
        plan = _plan(_section("**Nebius Group & CoreWeave:** neoclouds [00:10]"), tx)
        assert plan.grounded == () and plan.stats["ungrounded"] == 1

    def test_title_and_author(self):
        tx = _transcript((300, "Bill Perkins wrote Die with Zero."))
        plan = _plan(_section("*Die with Zero* (Book) by Bill Perkins [05:00]"), tx)
        assert {(g.name, g.surface_label) for g in plan.grounded} == {
            ("Die with Zero", "product"),
            ("Bill Perkins", "person"),
        }


class TestAmbiguity:
    def test_a_surname_shared_with_a_different_gliner_name_is_not_scanned(self):
        tx = _transcript((10, "Kate Perkins and Bill Perkins disagree."))
        ctx = PlanContext(extracted_names=[("Bill Perkins", "person")])
        (g,) = _plan(_section("Kate Perkins (Author) [00:10]"), tx, ctx=ctx).grounded
        assert g.scan_surfaces == ("Kate Perkins",) and g.dropped_surfaces == ("Perkins",)

    def test_a_surname_that_is_an_anchor_surface_is_not_scanned(self):
        tx = _transcript((10, "Ben Galloway is not Scott."))
        ctx = PlanContext(anchor_surfaces=frozenset({"scott galloway", "galloway", "scott"}))
        (g,) = _plan(_section("Ben Galloway (Author) [00:10]"), tx, ctx=ctx).grounded
        assert g.dropped_surfaces == ("Galloway",)

    def test_a_surname_two_items_share_is_scanned_by_neither(self):
        tx = _transcript((10, "Ann Smith and Joe Smith wrote it."))
        plan = _plan(_section("Ann Smith (Author) [00:10]", "Joe Smith (Author) [00:10]"), tx)
        assert all(g.dropped_surfaces == ("Smith",) for g in plan.grounded)

    def test_gliner_finding_the_same_full_name_is_not_ambiguous(self):
        tx = _transcript((10, "Paul Kedrosky says hi."))
        ctx = PlanContext(extracted_names=[("Paul Kedrosky", "person")])
        (g,) = _plan(_section("Paul Kedrosky (Investor) [00:10]"), tx, ctx=ctx).grounded
        assert g.scan_surfaces == ("Paul Kedrosky", "Kedrosky")


class TestLabels:
    def test_a_kindless_item_borrows_gliners_label(self):
        tx = _transcript((10, "Vanguard funds are cheap."))
        ctx = PlanContext(extracted_names=[("Vanguard", "company")])
        (g,) = _plan(_section("**Vanguard:** bond funds [00:10]"), tx, ctx=ctx).grounded
        # "funds" is not a kind word; "fund" is, and the gloss word must match whole.
        assert g.surface_label == "company"

    def test_a_kindless_unknown_item_has_no_label(self):
        tx = _transcript((10, "The Baghdad Battery is old."))
        (g,) = _plan(_section("**The Baghdad Battery:** archaeological mystery [00:10]"), tx).grounded
        assert g.kind is None and g.surface_label is None

    def test_hints_carry_kind_and_gloss(self):
        tx = _transcript((10, "Margin Call says it best."))
        plan = _plan(_section("**Margin Call** (film): Jeremy Irons [00:10]"), tx)
        assert plan.hints() == {"margin call": ("film", "Jeremy Irons")}
        (seed,) = plan.seeds()
        assert (seed.surface, seed.surface_label, seed.case_sensitive) == ("Margin Call", "product", False)


def test_the_motivating_episode():
    """Prof G Markets, 2026-10-05: six resources, all said in the episode."""
    tx = _transcript(
        (158, "You spoke to Paul, one of my early role models, Paul Kedrosky, yesterday."),
        (700, "I've been going back and watching Mad Men, the greatest TV show of all time."),
        (725, "But what we know based on the Ramp spending data is that it's informative."),
        (1451, "Breaking Bad and Modern Family and The Sopranos."),
        (2686, "Jeremy Irons in Margin Call says it best."),
        (2974, "Go into Vanguard's fixed income, one of their fixed income products."),
    )
    md = _section(
        "Mad Men (TV Show) [12:09](?t=729&cite=c1)",
        "Modern Family (TV Show) [24:15](?t=1455&cite=c2)",
        "Margin Call (Movie) [44:45](?t=2685&cite=c3)",
        "Ramp (Spending Data/Tool) [12:09](?t=729&cite=c4)",
        "Vanguard Bond Funds (Investment Tool) [49:34](?t=2974&cite=c5)",
        "Paul Kedrosky (Investor/Guest) [02:38](?t=158&cite=c6)",
    )
    plan = _plan(md, tx)
    assert {g.name for g in plan.grounded} == {"Mad Men", "Modern Family", "Margin Call", "Ramp", "Paul Kedrosky"}
    # "Vanguard Bond Funds" is not what was said; the contract (Phase 3) fixes names at the source.
    assert [i.name for i, why in plan.dropped] == ["Vanguard Bond Funds"]
