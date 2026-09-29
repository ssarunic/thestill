"""Spec #89 Phase 1 — golden-set search evaluator."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from thestill.evals.search_quality import (
    GoldenQuery,
    Hit,
    JudgmentCache,
    _parse_grades,
    judge_hits,
    load_golden,
    run_golden,
    score_runs,
)


def test_bundled_golden_set_is_well_formed():
    queries = load_golden()
    assert len(queries) >= 50
    assert len({q.id for q in queries}) == len(queries)
    assert {q.kind for q in queries} == {"name", "concept", "croatian", "nonsense"}
    assert any(q.query == "legora" for q in queries)  # the incident query, lowercase


class _Judge:
    """Grades passages by whether they contain the query word; counts calls."""

    def __init__(self, bad_first: int = 0):
        self.calls = 0
        self.bad_first = bad_first
        self.info = SimpleNamespace(provider="fake", model="fake-1", pinned=True, temperature=0.0)
        self.provider = self

    def supports_temperature(self):
        return True

    def chat_completion(self, messages, temperature=None, response_format=None):
        self.calls += 1
        if self.calls <= self.bad_first:
            return "not json"
        user = messages[1]["content"]
        word = user.split("QUERY: ")[1].split("\n")[0].lower()
        passages = user.split("PASSAGES:\n")[1].split("\n\n")
        grades = [{"i": i, "grade": 2 if word in p.lower() else 0} for i, p in enumerate(passages, start=1)]
        return json.dumps({"grades": grades})


Q = GoldenQuery(id="n-legora-lc", kind="name", query="legora", about="Legora, the legal-AI company")


def _hits(*texts):
    return [Hit(episode_id="ep", segment_id=i, text=t) for i, t in enumerate(texts)]


def test_judge_grades_only_unseen_pairs_and_caches(tmp_path):
    cache = JudgmentCache(tmp_path / "j.jsonl")
    judge = _Judge()
    hits = _hits("Host: Legora is the platform", "Mia Biberović: Ovaj je s kraćom kosom.")
    assert judge_hits(judge, Q, hits, cache) == 2
    assert judge_hits(judge, Q, hits, cache) == 0  # cached
    assert judge.calls == 1
    reloaded = JudgmentCache(tmp_path / "j.jsonl")
    assert [reloaded.get("legora", h.key) for h in hits] == [2, 0]


def test_changed_text_is_a_new_pair(tmp_path):
    cache = JudgmentCache(tmp_path / "j.jsonl")
    judge = _Judge()
    judge_hits(judge, Q, _hits("Host: Legora is the platform"), cache)
    judge_hits(judge, Q, _hits("Host: Legora is the platform, edited"), cache)
    assert judge.calls == 2


def test_invalid_judge_output_is_retried_once_then_raises(tmp_path):
    cache = JudgmentCache(tmp_path / "j.jsonl")
    assert judge_hits(_Judge(bad_first=1), Q, _hits("Host: Legora wins"), cache) == 1
    with pytest.raises(RuntimeError, match="judge failed twice"):
        judge_hits(_Judge(bad_first=2), Q, _hits("Host: another Legora line"), cache)


def test_parse_grades_requires_every_passage():
    with pytest.raises(ValueError):
        _parse_grades(json.dumps({"grades": [{"i": 1, "grade": 2}]}), 2)
    assert _parse_grades(json.dumps({"grades": [{"i": 1, "grade": 2}, {"i": 2, "grade": 0}]}), 2) == {1: 2, 2: 0}


def test_scores_are_pooled_across_runs(tmp_path):
    cache = JudgmentCache(tmp_path / "j.jsonl")
    judge = _Judge()
    good = _hits("Host: Legora is the platform", "Guest: we compete with Legora")
    # Same passage (same episode, segment, text) as good[0], pushed down by filler.
    junk = [Hit(episode_id="ep", segment_id=9, text="Mia Biberović: Ovaj je s kraćom kosom."), good[0]]

    def search_for(hits):
        return lambda q, k: list(hits)

    clean = run_golden(search_for(good), [Q], label="clean", judge=judge, cache=cache)
    noisy = run_golden(search_for(junk), [Q], label="noisy", judge=judge, cache=cache)
    scores = score_runs([clean, noisy], cache)
    assert scores["clean"]["name"].ndcg == pytest.approx(1.0)
    assert scores["noisy"]["name"].ndcg < 1.0
    assert (scores["clean"]["all"].junk, scores["noisy"]["all"].junk) == (0, 1)


def test_nonsense_query_scores_junk_not_ndcg(tmp_path):
    cache = JudgmentCache(tmp_path / "j.jsonl")
    q = GoldenQuery(id="x", kind="nonsense", query="legoraaaa", about="a typo")
    empty = run_golden(lambda q, k: [], [q], label="empty", judge=_Judge(), cache=cache)
    noisy = run_golden(
        lambda q, k: _hits("Tia: Ta haljina, on a little bit."), [q], label="noisy", judge=_Judge(), cache=cache
    )
    s = score_runs([empty, noisy], cache)
    assert s["empty"]["nonsense"].ndcg is None and s["empty"]["nonsense"].junk == 0
    assert s["noisy"]["nonsense"].junk == 1


def test_warm_up_query_is_not_timed(tmp_path):
    calls = []

    def search(q, k):
        calls.append(q)
        return []

    run = run_golden(search, [Q], label="w")
    assert calls == ["legora", "legora"]  # warm-up + the timed run
    assert len(run.results) == 1
