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

"""Spec #81 - the ``entity-linking`` rubric: live linker vs the stored baseline."""

import json
from types import SimpleNamespace

import pytest

from tests.unit.evals.conftest import make_episode, make_judge, make_podcast
from thestill.core.entity_linking.live_linker import LinkOutcome, group_mentions
from thestill.core.entity_linking.types import Candidate, LinkContext, LinkDecision
from thestill.evals import entity_linking as el
from thestill.evals.entity_linking import (
    BASELINE_ONLY,
    BASELINE_RIGHT,
    BOTH_NONE,
    BOTH_RIGHT,
    DIFFERENT_LINK,
    LIVE_ONLY,
    LIVE_RIGHT,
    NEITHER_RIGHT,
    SAME_LINK,
    UNANSWERED,
    UNCLEAR,
    Answer,
    EpisodeCounts,
    LinkingEvalRunner,
    NameComparison,
    baseline_answer,
    compare_names,
    derived_scores,
    gate,
    live_is_side_a,
    metrics,
    no_memory,
    render_judge_message,
    verdict_for,
)
from thestill.evals.rubrics import get_rubric
from thestill.evals.runner import EvalError
from thestill.models.entities import EntityMention, EntityRecord, EntityType, ResolutionMethod
from thestill.utils.path_manager import PathManager

CTX = LinkContext(episode_id="ep", podcast_title="Deep Questions", episode_title="Brain Rot")

ENTITIES = {
    "person:harry-s-truman": EntityRecord(
        id="person:harry-s-truman",
        type=EntityType.PERSON,
        canonical_name="Harry S. Truman",
        wikidata_qid="Q11613",
        description="33rd US president",
    ),
    "company:openai": EntityRecord(
        id="company:openai", type=EntityType.COMPANY, canonical_name="OpenAI", wikidata_qid="Q21708200"
    ),
}


def _mention(mention_id, name, entity_id=None, method=None):
    if method is None:
        method = ResolutionMethod.DIRECT if entity_id else ResolutionMethod.UNRESOLVABLE
    return EntityMention(
        id=mention_id,
        episode_id="ep",
        segment_id=mention_id,
        start_ms=0,
        end_ms=1,
        surface_form=name,
        surface_label="person",
        quote_excerpt=f"... {name} {mention_id} ...",
        confidence=0.9,
        extractor="gliner:test",
        entity_id=entity_id,
        resolution_method=method,
    )


def _live(key, qid, confidence="high", label="", description=""):
    candidate = Candidate(qid, label or qid, description) if qid else None
    return LinkDecision(surface_key=key, qid=qid, confidence=confidence, candidate=candidate)


def _compare(mentions, decisions, min_confidence="medium"):
    outcome = LinkOutcome(decisions={d.surface_key: d for d in decisions})
    return compare_names(group_mentions(mentions), mentions, outcome, ENTITIES.get, min_confidence=min_confidence)


# --- the baseline ------------------------------------------------------------


def test_the_baseline_is_the_majority_of_the_stored_mentions():
    members = [
        _mention(1, "Truman", "person:harry-s-truman"),
        _mention(2, "Truman", "person:harry-s-truman"),
        _mention(3, "Truman"),
    ]
    answer, split = baseline_answer(members, ENTITIES.get)
    assert (answer.qid, answer.label, answer.description) == ("Q11613", "Harry S. Truman", "33rd US president")
    assert split is True  # ReFinED answered this one name two ways


def test_a_tie_between_a_link_and_no_link_is_no_link():
    answer, split = baseline_answer(
        [_mention(1, "Truman", "person:harry-s-truman"), _mention(2, "Truman")], ENTITIES.get
    )
    assert answer.qid is None and split is True


def test_a_unanimous_baseline_is_not_split():
    _, split = baseline_answer(
        [_mention(1, "OpenAI", "company:openai"), _mention(2, "OpenAI", "company:openai")], ENTITIES.get
    )
    assert split is False


# --- comparing ---------------------------------------------------------------


@pytest.mark.parametrize(
    "entity_id, live_qid, expected",
    [
        ("company:openai", "Q21708200", SAME_LINK),
        ("company:openai", "Q999", DIFFERENT_LINK),
        ("company:openai", None, BASELINE_ONLY),
        (None, "Q100", LIVE_ONLY),
        (None, None, BOTH_NONE),
    ],
)
def test_outcomes(entity_id, live_qid, expected):
    (comparison,), skipped = _compare([_mention(1, "OpenAI", entity_id)], [_live("openai", live_qid)])
    assert comparison.outcome == expected and skipped == 0


def test_a_name_the_live_linker_could_not_answer_is_unanswered_not_none():
    (comparison,), _ = _compare([_mention(1, "OpenAI", "company:openai")], [])
    assert comparison.outcome == UNANSWERED


def test_a_low_confidence_guess_counts_as_no_link_as_it_would_in_the_pipeline():
    (comparison,), _ = _compare([_mention(1, "OpenAI", "company:openai")], [_live("openai", "Q21708200", "low")])
    assert comparison.outcome == BASELINE_ONLY and comparison.live_confidence == "low"


def test_names_the_live_linker_already_decided_have_no_baseline_and_are_skipped():
    comparisons, skipped = _compare(
        [_mention(1, "Dario Amodei", method=ResolutionMethod.LLM_LINKED)], [_live("dario amodei", "Q100")]
    )
    assert comparisons == [] and skipped == 1


# --- blind judging -----------------------------------------------------------


def _disputed(name, baseline_qid="Q11613", live_qid="Q214801"):
    return NameComparison(
        surface_key=name.casefold(),
        surface_form=name,
        excerpts=["Jim Carrey plays Truman"],
        baseline=Answer(baseline_qid, "Harry S. Truman", "33rd US president") if baseline_qid else Answer(),
        live=Answer(live_qid, "The Truman Show", "1998 film") if live_qid else Answer(),
        outcome=DIFFERENT_LINK,
    )


def _names_on_each_side():
    names = [f"name {i}" for i in range(40)]
    return [n for n in names if live_is_side_a(n)], [n for n in names if not live_is_side_a(n)]


def test_the_live_answer_appears_on_both_sides_across_names():
    on_a, on_b = _names_on_each_side()
    assert on_a and on_b


def test_the_judge_is_not_told_which_linker_said_what():
    message = render_judge_message({"n1": _disputed("Truman")}, CTX)
    lowered = message.lower()
    assert not any(word in lowered for word in ("live", "baseline", "refined", "llm"))
    assert "Q11613 | Harry S. Truman | 33rd US president" in message
    assert "Q214801 | The Truman Show | 1998 film" in message
    assert message.index("[n1]") < message.index("<<<UNTRUSTED_ITEM_BEGIN>>>")


def test_no_link_is_shown_as_an_answer_in_its_own_right():
    message = render_judge_message({"n1": _disputed("Truman", baseline_qid=None)}, CTX)
    assert "no link" in message


def test_a_verdict_is_mapped_back_to_the_right_linker_whichever_side_it_was_on():
    on_a, on_b = _names_on_each_side()
    assert verdict_for(_disputed(on_a[0]), "a") == LIVE_RIGHT
    assert verdict_for(_disputed(on_a[0]), "b") == BASELINE_RIGHT
    assert verdict_for(_disputed(on_b[0]), "a") == BASELINE_RIGHT
    assert verdict_for(_disputed(on_b[0]), "b") == LIVE_RIGHT
    assert [verdict_for(_disputed("x"), v) for v in ("both", "neither", "unclear")] == [
        BOTH_RIGHT,
        NEITHER_RIGHT,
        UNCLEAR,
    ]


# --- metrics -----------------------------------------------------------------


def _counts():
    return EpisodeCounts(
        names=100,
        outcomes={SAME_LINK: 40, BOTH_NONE: 30, DIFFERENT_LINK: 5, LIVE_ONLY: 20, BASELINE_ONLY: 3, UNANSWERED: 2},
        verdicts={
            DIFFERENT_LINK: {LIVE_RIGHT: 4, BASELINE_RIGHT: 1},
            LIVE_ONLY: {LIVE_RIGHT: 16, NEITHER_RIGHT: 2, UNCLEAR: 2},
            BASELINE_ONLY: {BASELINE_RIGHT: 0, NEITHER_RIGHT: 3},
        },
    )


def test_metrics_are_ratios_of_counts():
    m = metrics(_counts())
    assert m["agreement"] == pytest.approx(70 / 98)  # the unanswered names are not compared
    assert m["regression_rate"] == pytest.approx(1 / 48)  # baseline right, live wrong, of names the baseline linked
    assert m["new_link_precision"] == pytest.approx(16 / 18)  # "unclear" is left out
    assert m["recall_gain"] == pytest.approx(16 / 50)


def test_a_dimension_with_nothing_to_measure_is_left_out_not_scored():
    scores = derived_scores(EpisodeCounts(names=2, outcomes={SAME_LINK: 2}))
    assert scores == {"agreement": 10.0, "no_regression": 10.0}


def test_the_gate_is_decided_on_corpus_counts():
    verdict = gate(_counts(), checks_ok=True)
    assert verdict["criteria"] == {
        "regression_rate_under_2pct": False,  # 1/48 = 2.08%
        "recall_gain_at_least_25pct": True,
        "new_link_precision_at_least_90pct": False,  # 16/18 = 88.9%
        "deterministic_checks_ok": True,
    }
    assert verdict["passed"] is False


def test_an_empty_run_does_not_pass_by_default():
    assert gate(EpisodeCounts(), checks_ok=True)["passed"] is False


def test_counts_add_up_across_episodes():
    total = EpisodeCounts()
    total.add(_counts())
    total.add(_counts())
    assert total.names == 200 and total.outcome(LIVE_ONLY) == 40 and total.verdict(LIVE_ONLY, LIVE_RIGHT) == 32


def test_the_eval_linker_remembers_nothing():
    memory = no_memory()
    memory.upsert(SimpleNamespace())
    assert (
        memory.get("openai", None) is None and memory.podcast_decisions("openai") == [] and memory.delete("openai") == 0
    )


# --- the runner --------------------------------------------------------------


class FakeRepo:
    def __init__(self, mentions_by_episode, blacklisted=()):
        self.mentions_by_episode = mentions_by_episode
        self.blacklisted = set(blacklisted)

    def list_linker_decided_mentions(self, episode_id):
        return self.mentions_by_episode.get(episode_id, [])

    def get_entity(self, entity_id):
        return ENTITIES.get(entity_id)

    def is_blacklisted(self, surface_form, qid):
        return (surface_form, qid) in self.blacklisted


class FakeLinker:
    version = "p1:mock"

    def __init__(self, decisions, fail_for=(), unreachable=False):
        self.decisions = decisions
        self.fail_for = set(fail_for)
        self.unreachable = unreachable
        self.linked = []

    def link(self, groups, context, *, is_blacklisted=None):
        self.linked.append(context.episode_id)
        if context.episode_id in self.fail_for:
            raise RuntimeError("linker exploded")
        keys = {g.surface_key for g in groups}
        return LinkOutcome(
            decisions={k: d for k, d in self.decisions.items() if k in keys},
            names=len(groups),
            unreachable=self.unreachable,
        )

    def resolve(self, *a, **k):  # pragma: no cover - the eval must never call this
        raise AssertionError("the eval must not write through the linker")


def _env(tmp_path, episodes, mentions_by_slug, linker, blacklisted=()):
    podcast = make_podcast("deep-questions", [])
    built = []
    for slug in episodes:
        episode = make_episode(slug, "deep-questions", id=f"id-{slug}")
        podcast.episodes.append(episode)
        built.append(episode)
    repo = FakeRepo({f"id-{slug}": m for slug, m in mentions_by_slug.items()}, blacklisted)
    runner = LinkingEvalRunner(
        SimpleNamespace(entity_linking_min_confidence="medium"),
        PathManager(str(tmp_path)),
        SimpleNamespace(list_podcasts=lambda: [podcast]),
        entity_repository=repo,
        linker=linker,
        context_builder=lambda _repo, p, e: LinkContext(episode_id=e.id, podcast_title=p.title, episode_title=e.title),
    )
    return runner, podcast, built


def _verdicts(*pairs):
    return json.dumps({"verdicts": [{"id": i, "correct": c, "reason": "because"} for i, c in pairs]})


def _side(name, who):
    """The letter the judge must say for ``who`` ("live"/"baseline") to win."""
    return "a" if live_is_side_a(name.casefold()) == (who == "live") else "b"


MENTIONS = [
    _mention(1, "OpenAI", "company:openai"),  # same link
    _mention(2, "Truman", "person:harry-s-truman"),  # different link
    _mention(3, "Dario Amodei"),  # live only
    _mention(4, "Nobody"),  # both none
]
DECISIONS = {
    "openai": _live("openai", "Q21708200", label="OpenAI"),
    "truman": _live("truman", "Q214801", label="The Truman Show", description="1998 film"),
    "dario amodei": _live("dario amodei", "Q100", label="Dario Amodei"),
    "nobody": _live("nobody", None),
}


def test_a_run_compares_judges_and_persists(tmp_path):
    linker = FakeLinker(DECISIONS)
    runner, podcast, (episode,) = _env(tmp_path, ["brain-rot"], {"brain-rot": MENTIONS}, linker)
    # disputed names are sent in mention order: Truman (n1), Dario Amodei (n2)
    judge = make_judge([_verdicts(("n1", _side("Truman", "live")), ("n2", _side("Dario Amodei", "live")))])
    manifest = runner.run(get_rubric("entity-linking"), judge, [(podcast, episode)])

    (item,) = manifest.items
    assert item.status == "ok" and item.checks_ok is True
    assert item.scores == {"agreement": 5.0, "no_regression": 10.0, "new_link_precision": 10.0, "recall_gain": 5.0}

    run_dir = runner.path_manager.evaluation_run_dir(manifest.run_id)
    report = json.loads((run_dir / item.report_file).read_text())
    assert report["linker_version"] == "p1:mock"
    by_name = {n["surface_form"]: n for n in report["names"]}
    assert (by_name["Truman"]["outcome"], by_name["Truman"]["verdict"]) == (DIFFERENT_LINK, LIVE_RIGHT)
    assert by_name["OpenAI"]["verdict"] is None  # agreement is never judged

    totals = json.loads((run_dir / "totals.json").read_text())
    assert totals["counts"]["names"] == 4
    assert totals["metrics"]["recall_gain"] == pytest.approx(0.5)
    assert totals["passed"] is True
    assert (run_dir / "summary.json").exists()
    assert judge.provider.call_count == 1


def test_an_episode_with_full_agreement_costs_no_judge_call(tmp_path):
    linker = FakeLinker(DECISIONS)
    runner, podcast, (episode,) = _env(tmp_path, ["agree"], {"agree": [MENTIONS[0], MENTIONS[3]]}, linker)
    judge = make_judge([])
    manifest = runner.run(get_rubric("entity-linking"), judge, [(podcast, episode)])
    assert manifest.items[0].scores == {"agreement": 10.0, "no_regression": 10.0, "recall_gain": 0.0}
    assert judge.provider.call_count == 0


def test_one_failing_episode_does_not_lose_the_others(tmp_path):
    linker = FakeLinker(DECISIONS, fail_for={"id-bad"})
    runner, podcast, episodes = _env(tmp_path, ["bad", "good"], {"bad": MENTIONS, "good": [MENTIONS[0]]}, linker)
    manifest = runner.run(get_rubric("entity-linking"), make_judge([]), [(podcast, e) for e in episodes])
    assert [(i.episode_slug, i.status) for i in manifest.items] == [("bad", "failed"), ("good", "ok")]
    assert "linker exploded" in manifest.items[0].error
    totals = json.loads((runner.path_manager.evaluation_run_dir(manifest.run_id) / "totals.json").read_text())
    assert totals["counts"]["names"] == 1  # the failed episode contributes nothing


def test_a_name_the_judge_skips_counts_towards_nothing(tmp_path):
    runner, podcast, (episode,) = _env(tmp_path, ["brain-rot"], {"brain-rot": MENTIONS}, FakeLinker(DECISIONS))
    judge = make_judge([_verdicts(("n1", _side("Truman", "baseline")))])  # says nothing about n2
    manifest = runner.run(get_rubric("entity-linking"), judge, [(podcast, episode)])
    scores = manifest.items[0].scores
    assert scores["no_regression"] == 5.0  # Truman: the baseline was right
    assert "new_link_precision" not in scores  # Dario Amodei went unjudged


def test_a_blacklisted_live_link_fails_the_checks_and_the_gate(tmp_path):
    runner, podcast, (episode,) = _env(
        tmp_path, ["agree"], {"agree": [MENTIONS[0]]}, FakeLinker(DECISIONS), blacklisted={("OpenAI", "Q21708200")}
    )
    manifest = runner.run(get_rubric("entity-linking"), make_judge([]), [(podcast, episode)])
    assert manifest.items[0].checks_ok is False
    totals = json.loads((runner.path_manager.evaluation_run_dir(manifest.run_id) / "totals.json").read_text())
    assert totals["criteria"]["deterministic_checks_ok"] is False and totals["passed"] is False


def test_an_unreachable_linker_is_flagged_not_scored_as_disagreement(tmp_path):
    linker = FakeLinker({}, unreachable=True)
    runner, podcast, (episode,) = _env(tmp_path, ["down"], {"down": [MENTIONS[0]]}, linker)
    manifest = runner.run(get_rubric("entity-linking"), make_judge([]), [(podcast, episode)])
    item = manifest.items[0]
    assert item.checks_ok is False
    assert item.scores == {}  # nothing was compared, so nothing is scored


def test_more_than_one_sample_is_refused(tmp_path):
    runner, podcast, (episode,) = _env(tmp_path, ["agree"], {"agree": [MENTIONS[0]]}, FakeLinker(DECISIONS))
    with pytest.raises(EvalError, match="--samples 1"):
        runner.run(get_rubric("entity-linking"), make_judge([]), [(podcast, episode)], samples=2)


def test_discover_keeps_only_episodes_with_something_to_compare(tmp_path):
    runner, _podcast, _episodes = _env(tmp_path, ["has", "none"], {"has": MENTIONS}, FakeLinker(DECISIONS))
    found = runner.discover(get_rubric("entity-linking"))
    assert [e.slug for _p, e in found] == ["has"]


def test_discover_honours_a_pinned_episode_file(tmp_path):
    runner, _podcast, _episodes = _env(
        tmp_path, ["one", "two"], {"one": MENTIONS, "two": MENTIONS}, FakeLinker(DECISIONS)
    )
    pinned = tmp_path / "set.json"
    pinned.write_text(json.dumps({"episodes": [{"podcast_slug": "deep-questions", "episode_slug": "two"}]}))
    found = runner.discover(get_rubric("entity-linking"), episodes_file=pinned)
    assert [e.slug for _p, e in found] == ["two"]


def test_the_rubric_is_registered_with_its_derived_dimensions():
    rubric = get_rubric(el.RUBRIC_NAME)
    assert rubric.dimensions == ("agreement", "no_regression", "new_link_precision", "recall_gain")
    assert "SECURITY NOTE" in rubric.system_prompt


# --- wiring ------------------------------------------------------------------


def test_the_pinned_set_is_twenty_episodes_across_at_least_eight_podcasts():
    from pathlib import Path

    pinned = json.loads((Path(__file__).parents[2] / "fixtures/eval/entity_linking_episodes.json").read_text())
    episodes = pinned["episodes"]
    assert len(episodes) == 20
    assert len({e["podcast_slug"] for e in episodes}) >= 8
    assert all(e["why"] for e in episodes)


def test_the_cli_builds_a_live_linker_that_remembers_nothing(monkeypatch):
    from thestill import cli

    built = {}

    def fake_build_linker(config, link_decisions, **_kw):
        built["linker"] = config.entity_linker
        built["memory"] = link_decisions
        return FakeLinker({})

    monkeypatch.setattr("thestill.core.entity_linking.factory.build_linker", fake_build_linker)
    config = SimpleNamespace(entity_linker="refined", model_copy=lambda update: SimpleNamespace(**update))
    ctx = SimpleNamespace(
        obj=SimpleNamespace(config=config, path_manager=None, feed_manager=None, entity_repository=FakeRepo({}))
    )
    runner = cli._eval_runner_for(ctx, get_rubric("entity-linking"))
    assert isinstance(runner, LinkingEvalRunner)
    assert built["linker"] == "live"  # whatever ENTITY_LINKER says, the eval runs the live one
    assert built["memory"].get("anything", None) is None
    assert not isinstance(cli._eval_runner_for(ctx, get_rubric("summary")), LinkingEvalRunner)


def test_the_gate_verdict_is_printed(tmp_path, capsys):
    from thestill import cli

    (tmp_path / "totals.json").write_text(json.dumps({"counts": {"names": 100}, **gate(_counts(), checks_ok=True)}))
    cli._echo_linking_gate(tmp_path)
    out = capsys.readouterr().out
    assert "over 100 names" in out and "new_link_precision   88.9%" in out and "FAIL" in out
