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

"""Spec #89 Phase 1 — corpus search quality against a golden query set.

``run_golden`` sends every golden query through a search function, records the
top-k hits with latency, and has the eval judge grade any (query, passage)
pair it has not seen before — one batched call per query. Grades are cached
in a JSONL file keyed by query + episode + segment + text hash, so reruns and
new variants only pay for new pairs.

``score_runs`` turns saved runs into numbers against the POOLED judgments of
all runs being compared: nDCG's ideal ranking is built from every relevant
passage any variant found, which is what makes variants comparable.

Grades: 2 = about the query's subject or answers it; 1 = substantively
related; 0 = unrelated, including namesakes (Lego toys for "Legora").
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from structlog import get_logger

from ..utils.text_sanitizer import sanitize_text

logger = get_logger(__name__)

GOLDEN_PATH = Path(__file__).parent / "data" / "search_golden.json"
PASSAGE_CHARS = 600  # what the judge sees per passage; the UI shows 600 too

JUDGE_SYSTEM = """You grade podcast transcript passages for a search engine.
For the user's query, grade EACH passage:
  2 = the passage is about the query's subject or directly answers it
  1 = the passage is substantively related to the query
  0 = unrelated. A different thing with a similar name is 0 (Lego toys for "Legora",
      the anthropic principle for "Anthropic"). A passage that merely has a similar
      sound or language as the query is 0.
The query may be lowercase or misspelled; judge against its intended meaning, given as INTENT.
Return JSON only: {"grades": [{"i": <passage number>, "grade": 0|1|2}, ...]} covering every passage."""


@dataclass(frozen=True)
class GoldenQuery:
    id: str
    kind: str
    query: str
    about: str


@dataclass
class Hit:
    episode_id: str
    segment_id: int
    text: str
    score: Optional[float] = None  # the backend's own score (rerank score when reranking)
    origin: Optional[str] = None  # which leg found it (reranked runs only)

    @property
    def key(self) -> str:
        return f"{self.episode_id}:{self.segment_id}:{_text_hash(self.text)}"


@dataclass
class QueryResult:
    id: str
    kind: str
    query: str
    latency_ms: float
    hits: List[Hit] = field(default_factory=list)


@dataclass
class Run:
    label: str
    created_at: str
    k: int
    results: List[QueryResult]
    judge: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "Run":
        results = [
            QueryResult(**{**r, "hits": [Hit(**h) for h in r["hits"]]})  # type: ignore[arg-type]
            for r in data["results"]
        ]
        return cls(
            label=data["label"],
            created_at=data["created_at"],
            k=data["k"],
            results=results,
            judge=data.get("judge", {}),
        )


def _text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def load_golden(path: Path = GOLDEN_PATH) -> List[GoldenQuery]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [GoldenQuery(**q) for q in data["queries"]]


class JudgmentCache:
    """Append-only JSONL of grades: {"query", "key", "grade"}; last write wins."""

    def __init__(self, path: Path):
        self.path = path
        self._grades: Dict[Tuple[str, str], int] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    self._grades[(row["query"], row["key"])] = int(row["grade"])

    def get(self, query: str, key: str) -> Optional[int]:
        return self._grades.get((query, key))

    def put_many(self, query: str, grades: Dict[str, int]) -> None:
        if not grades:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            for key, grade in grades.items():
                self._grades[(query, key)] = grade
                fh.write(json.dumps({"query": query, "key": key, "grade": grade}, ensure_ascii=False) + "\n")

    def relevant_keys(self, query: str) -> Dict[str, int]:
        return {k: g for (q, k), g in self._grades.items() if q == query and g > 0}


def _judge_messages(q: GoldenQuery, hits: Sequence[Hit]) -> List[Dict[str, str]]:
    passages = "\n\n".join(f"[{i}] {h.text[:PASSAGE_CHARS]}" for i, h in enumerate(hits, start=1))
    user = f"QUERY: {q.query}\nINTENT: {q.about}\n\nPASSAGES:\n{passages}"
    return [{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": user}]


def _parse_grades(raw: str, n: int) -> Dict[int, int]:
    clean, _ = sanitize_text(raw)
    data = json.loads(clean)
    out: Dict[int, int] = {}
    for row in data["grades"]:
        i, g = int(row["i"]), int(row["grade"])
        if 1 <= i <= n and g in (0, 1, 2):
            out[i] = g
    if len(out) != n:
        raise ValueError(f"judge graded {len(out)} of {n} passages")
    return out


def judge_hits(judge, q: GoldenQuery, hits: Sequence[Hit], cache: JudgmentCache) -> int:
    """Grade the hits the cache has not seen; returns how many were judged."""
    unseen: List[Hit] = []
    seen_keys = set()
    for h in hits:
        if cache.get(q.query, h.key) is None and h.key not in seen_keys:
            unseen.append(h)
            seen_keys.add(h.key)
    if not unseen:
        return 0
    temperature = judge.info.temperature if judge.provider.supports_temperature() else None
    last_error: Optional[Exception] = None
    for _attempt in (1, 2):  # FM-7: sanitize -> parse -> validate, retry once
        raw = judge.provider.chat_completion(
            messages=_judge_messages(q, unseen), temperature=temperature, response_format={"type": "json_object"}
        )
        try:
            grades = _parse_grades(raw, len(unseen))
            break
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            last_error = exc
            logger.warning("search_eval_judge_invalid", query_id=q.id, error=str(exc)[:300])
    else:
        raise RuntimeError(f"judge failed twice on {q.id}: {last_error}")
    cache.put_many(q.query, {unseen[i - 1].key: g for i, g in grades.items()})
    return len(unseen)


SearchFn = Callable[[str, int], List[Hit]]


def run_golden(
    search: SearchFn,
    queries: Iterable[GoldenQuery],
    *,
    label: str,
    k: int = 10,
    judge=None,
    cache: Optional[JudgmentCache] = None,
) -> Run:
    """Run every query, then judge unseen hits when a judge is given."""
    from datetime import datetime, timezone

    queries = list(queries)
    if queries:
        search(queries[0].query, k)  # warm-up: model loads must not count as query latency
    results: List[QueryResult] = []
    for q in queries:
        t0 = time.perf_counter()
        hits = search(q.query, k)[:k]
        latency = (time.perf_counter() - t0) * 1000
        results.append(QueryResult(id=q.id, kind=q.kind, query=q.query, latency_ms=round(latency, 1), hits=hits))
    run = Run(label=label, created_at=datetime.now(timezone.utc).isoformat(), k=k, results=results)
    if judge is not None and cache is not None:
        run.judge = {"provider": judge.info.provider, "model": judge.info.model, "pinned": judge.info.pinned}
        by_id = {q.id: q for q in queries}
        judged = 0
        for r in results:
            judged += judge_hits(judge, by_id[r.id], r.hits, cache)
        logger.info("search_eval_judged", label=label, new_pairs=judged)
    return run


def _dcg(gains: Sequence[int]) -> float:
    return sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(gains))


@dataclass
class KindScore:
    kind: str
    queries: int
    ndcg: Optional[float]  # mean over queries that have any relevant passage in the pool
    junk: float  # mean count of grade-0 results in the top k
    results: float  # mean number of results returned
    unjudged: int  # hits without a grade (judge not run for this run)


def score_runs(runs: Sequence[Run], cache: JudgmentCache) -> Dict[str, Dict[str, KindScore]]:
    """Per run label, per kind (plus "all"): pooled nDCG@k, junk@k, result count."""
    out: Dict[str, Dict[str, KindScore]] = {}
    for run in runs:
        per_kind: Dict[str, List[Tuple[Optional[float], int, int, int]]] = {}
        for r in run.results:
            grades = [cache.get(r.query, h.key) for h in r.hits]
            unjudged = sum(g is None for g in grades)
            known = [g or 0 for g in grades]
            ideal = sorted(cache.relevant_keys(r.query).values(), reverse=True)[: run.k]
            ndcg = _dcg(known) / _dcg(ideal) if ideal else None
            junk = sum(1 for g in grades if g == 0)
            for kind in (r.kind, "all"):
                per_kind.setdefault(kind, []).append((ndcg, junk, len(r.hits), unjudged))
        out[run.label] = {}
        for kind, rows in per_kind.items():
            ndcgs = [n for n, *_ in rows if n is not None]
            out[run.label][kind] = KindScore(
                kind=kind,
                queries=len(rows),
                ndcg=round(statistics.mean(ndcgs), 3) if ndcgs else None,
                junk=round(statistics.mean(j for _, j, _, _ in rows), 2),
                results=round(statistics.mean(n for _, _, n, _ in rows), 1),
                unjudged=sum(u for *_, u in rows),
            )
    return out


def latency_summary(run: Run) -> Tuple[float, float]:
    lat = sorted(r.latency_ms for r in run.results)
    if not lat:
        return 0.0, 0.0
    p50 = lat[len(lat) // 2]
    p95 = lat[min(len(lat) - 1, int(round(0.95 * (len(lat) - 1))))]
    return p50, p95


def per_query_table(runs: Sequence[Run], cache: JudgmentCache) -> List[dict]:
    """Rows of {id, query, <label>: (ndcg, junk)} for spotting regressions."""
    rows: Dict[str, dict] = {}
    for run in runs:
        for r in run.results:
            grades = [cache.get(r.query, h.key) or 0 for h in r.hits]
            ideal = sorted(cache.relevant_keys(r.query).values(), reverse=True)[: run.k]
            ndcg = round(_dcg(grades) / _dcg(ideal), 2) if ideal else None
            row = rows.setdefault(r.id, {"id": r.id, "kind": r.kind, "query": r.query})
            row[run.label] = (ndcg, sum(1 for h in r.hits if cache.get(r.query, h.key) == 0))
    return list(rows.values())
