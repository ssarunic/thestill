# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Spec #28 Phase 3.3 — per-tool latency budget CI gate.

Enforces the per-tool P50/P95 thresholds from spec lines 1316–1325
against a reproducible 10-episode fixture corpus. The thresholds in
the spec target the fixture corpus directly; we add a 1.25× tolerance
on top to absorb GitHub Actions runner variance (a shared
``ubuntu-latest`` is noisy at sub-millisecond scales). Production
budget = 3× fixture (spec §1324) — that's enforced by a separate
nightly job, not per-PR CI.
"""

from __future__ import annotations

import statistics
import time
from typing import Callable, List

import pytest

from thestill.repositories.sqlite_entity_repository import SqliteEntityRepository
from thestill.search.base import SearchMode
from thestill.search.sqlite_vec_client import SqliteVecBackend

# Spec-defined thresholds (ms). Keep these in sync with
# specs/28-corpus-search-and-entities.md lines 1318-1323.
_BUDGETS_MS = {
    "lexical": (30, 100),
    "hybrid": (200, 600),
    "find_mentions": (20, 80),
    "list_quotes_by": (20, 80),
}

# CI tolerance — runner noise eats budget at the P95 tail. 1.25 buys
# headroom without papering over real regressions; if a real change
# pushes timings 25%+ over spec we want to see the failure.
_CI_TOLERANCE = 1.25

# Sample count per test. 200 is large enough that P50/P95 are stable
# (N=200 puts the 95th percentile at index 190, not the absolute max),
# small enough to keep the whole job under a minute.
_N_SAMPLES = 200

_QUERY_BANK = [
    "Elon Musk",
    "SpaceX",
    "AI infrastructure",
    "OpenAI partnership",
    "Scott Galloway",
    "market trajectory next quarter",
    "structural shift cyclical",
    "competitive landscape announcement",
    "investing bellwether",
    "consensus view disagreement",
]


def _percentiles_ms(samples_seconds: List[float]) -> tuple[float, float]:
    """Return (P50, P95) of a sample list in milliseconds."""
    samples_ms = [s * 1000 for s in samples_seconds]
    samples_ms.sort()
    p50 = statistics.median(samples_ms)
    # statistics.quantiles(n=20)[18] is the 95th percentile by linear
    # interpolation; for a sorted list of 200 it's effectively
    # samples_ms[189-190].
    p95 = statistics.quantiles(samples_ms, n=20, method="inclusive")[18]
    return p50, p95


def _measure(fn: Callable[[str], object], queries: List[str], *, n: int) -> List[float]:
    """Time ``fn(query)`` ``n`` times, cycling through ``queries``."""
    samples: List[float] = []
    # One warm-up pass so SQLite's page cache is populated and we
    # don't measure cold-cache outliers.
    for q in queries:
        fn(q)
    for i in range(n):
        q = queries[i % len(queries)]
        t0 = time.perf_counter()
        fn(q)
        samples.append(time.perf_counter() - t0)
    return samples


def _assert_budget(samples: List[float], *, budget_key: str) -> None:
    p50_budget, p95_budget = _BUDGETS_MS[budget_key]
    p50, p95 = _percentiles_ms(samples)
    p50_limit = p50_budget * _CI_TOLERANCE
    p95_limit = p95_budget * _CI_TOLERANCE
    assert p50 < p50_limit, (
        f"{budget_key} P50 {p50:.1f}ms >= budget {p50_budget}ms × tolerance "
        f"{_CI_TOLERANCE} = {p50_limit:.1f}ms (n={len(samples)})"
    )
    assert p95 < p95_limit, (
        f"{budget_key} P95 {p95:.1f}ms >= budget {p95_budget}ms × tolerance "
        f"{_CI_TOLERANCE} = {p95_limit:.1f}ms (n={len(samples)})"
    )


@pytest.fixture(scope="module")
def search_backend(fixture_corpus_db, stub_embedding_model):
    return SqliteVecBackend(
        db_path=fixture_corpus_db["db_path"],
        embedding_model=stub_embedding_model,
    )


@pytest.fixture(scope="module")
def entity_repo(fixture_corpus_db):
    return SqliteEntityRepository(db_path=fixture_corpus_db["db_path"])


def test_search_corpus_lexical_latency(search_backend):
    """search_corpus(mode=lexical) — ⌘K typing path, must stay snappy."""
    samples = _measure(
        lambda q: search_backend.search(q, mode=SearchMode.LEXICAL, limit=10, filters=None),
        _QUERY_BANK,
        n=_N_SAMPLES,
    )
    _assert_budget(samples, budget_key="lexical")


def test_search_corpus_hybrid_latency(search_backend):
    """search_corpus(mode=hybrid) — full lex+vec fusion path."""
    samples = _measure(
        lambda q: search_backend.search(q, mode=SearchMode.HYBRID, limit=10, filters=None),
        _QUERY_BANK,
        n=_N_SAMPLES,
    )
    _assert_budget(samples, budget_key="hybrid")


def test_find_mentions_latency(entity_repo, fixture_corpus_db):
    """SqliteEntityRepository.find_mentions — backs the MCP find_mentions tool."""
    entity_ids = fixture_corpus_db["entity_ids"]
    samples = _measure(
        lambda q: entity_repo.find_mentions(entity_id=q, limit=50),
        entity_ids,
        n=_N_SAMPLES,
    )
    _assert_budget(samples, budget_key="find_mentions")


def test_list_quotes_by_latency(entity_repo):
    """SqliteEntityRepository.list_mentions_by_speaker — backs the MCP list_quotes_by tool."""
    speakers = ["speaker-0", "speaker-1"]
    samples = _measure(
        lambda q: entity_repo.list_mentions_by_speaker(speaker=q, limit=50),
        speakers,
        n=_N_SAMPLES,
    )
    _assert_budget(samples, budget_key="list_quotes_by")
