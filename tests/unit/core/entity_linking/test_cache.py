"""Spec #81 Stage 4 - decision cache rules, on a real SQLite repository."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from thestill.core.entity_linking.cache import LinkDecisionCache
from thestill.core.entity_linking.types import LinkDecision
from thestill.repositories.sqlite_link_decision_repository import SqliteLinkDecisionRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository

PODS = [f"{n}{n}{n}{n}{n}{n}{n}{n}-0000-4000-8000-000000000000" for n in "1234"]


class Clock:
    def __init__(self):
        self.now = datetime(2026, 9, 21, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


@pytest.fixture
def repo(tmp_path):
    db = str(tmp_path / "cache.db")
    SqlitePodcastRepository(db_path=db)
    with sqlite3.connect(db) as conn:
        for pid in PODS:
            conn.execute(
                "INSERT INTO podcasts (id, rss_url, title, description) VALUES (?, ?, 'Show', '')",
                (pid, f"https://example.com/{pid}"),
            )
    return SqliteLinkDecisionRepository(db_path=db)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def cache(repo, clock):
    return LinkDecisionCache(repo, linker_version="v1", none_ttl_days=30, clock=clock)


def _decision(qid="Q1", confidence="high", key="mercury", reason="because"):
    return LinkDecision(surface_key=key, qid=qid, confidence=confidence, reason=reason)


def test_a_miss_is_none(cache):
    assert cache.lookup("mercury", PODS[0]) is None


def test_a_recorded_decision_is_found_for_its_podcast_only(cache):
    cache.record(_decision(), PODS[0])
    hit = cache.lookup("mercury", PODS[0])
    assert (hit.qid, hit.confidence, hit.from_cache, hit.cache_scope) == ("Q1", "high", True, "podcast")
    assert cache.lookup("mercury", PODS[1]) is None


def test_podcast_scope_wins_over_corpus_scope(cache):
    cache.record(_decision(qid="Q-planet"), None)
    cache.record(_decision(qid="Q-freddie"), PODS[0])
    assert cache.lookup("mercury", PODS[0]).qid == "Q-freddie"
    other = cache.lookup("mercury", PODS[1])
    assert (other.qid, other.cache_scope) == ("Q-planet", "corpus")


def test_promoted_corpus_wide_once_three_podcasts_agree(cache):
    for pid in PODS[:2]:
        cache.record(_decision(), pid)
    assert cache.lookup("mercury", PODS[3]) is None
    cache.record(_decision(), PODS[2])
    assert cache.lookup("mercury", PODS[3]).qid == "Q1"


def test_disagreement_blocks_promotion(cache):
    cache.record(_decision(qid="Q-other"), PODS[0])
    for pid in PODS[1:4]:
        cache.record(_decision(), pid)
    assert cache.lookup("mercury", None) is None


def test_nones_and_low_confidence_never_count_towards_promotion(cache):
    cache.record(_decision(qid=None), PODS[0])
    cache.record(_decision(confidence="low"), PODS[1])
    cache.record(_decision(), PODS[2])
    assert cache.lookup("mercury", None) is None


def test_a_none_expires_so_the_name_is_checked_again(cache, clock):
    cache.record(_decision(qid=None, confidence="high"), PODS[0])
    clock.now += timedelta(days=29)
    assert cache.lookup("mercury", PODS[0]).qid is None
    clock.now += timedelta(days=1)
    assert cache.lookup("mercury", PODS[0]) is None


def test_a_low_confidence_answer_expires_like_a_none(cache, clock):
    cache.record(_decision(confidence="low"), PODS[0])
    clock.now += timedelta(days=30)
    assert cache.lookup("mercury", PODS[0]) is None


def test_a_link_never_expires(cache, clock):
    cache.record(_decision(), PODS[0])
    clock.now += timedelta(days=3650)
    assert cache.lookup("mercury", PODS[0]).qid == "Q1"


def test_decisions_from_another_linker_version_are_ignored(repo, clock, cache):
    cache.record(_decision(), PODS[0])
    v2 = LinkDecisionCache(repo, linker_version="v2", clock=clock)
    assert v2.lookup("mercury", PODS[0]) is None
    v2.record(_decision(qid="Q2"), PODS[0])
    assert v2.lookup("mercury", PODS[0]).qid == "Q2"


def test_lookup_is_read_only_and_hits_are_counted_explicitly(cache, repo):
    cache.record(_decision(), PODS[0])
    hit = cache.lookup("mercury", PODS[0])
    assert repo.get("mercury", PODS[0]).hits == 0
    cache.record_hit(hit, PODS[0])
    assert repo.get("mercury", PODS[0]).hits == 1


def test_a_corpus_hit_is_counted_on_the_corpus_row(cache, repo):
    cache.record(_decision(), None)
    cache.record_hit(cache.lookup("mercury", PODS[0]), PODS[0])
    assert repo.get("mercury", None).hits == 1


def test_invalidate_folds_the_spoken_name_and_clears_every_scope(cache):
    cache.record(_decision(), PODS[0])
    cache.record(_decision(), None)
    assert cache.invalidate("  MERCURY ") == 2
    assert cache.lookup("mercury", PODS[0]) is None


def test_the_stored_reason_is_truncated(cache, repo):
    cache.record(_decision(reason="x" * 500), PODS[0])
    assert len(repo.get("mercury", PODS[0]).reason) == 200
