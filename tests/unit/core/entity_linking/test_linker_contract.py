"""Spec #81 - what ``resolve-entities`` relies on, asserted on both linkers.

The handler does not know which linker it holds. Anything it depends on is
pinned here once and run against the ReFinED resolver and the live linker.
"""

import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from tests.unit.core.entity_linking.conftest import ScriptedProvider, mention, pick_first_candidate
from thestill.core.entity_linking.cache import LinkDecisionCache
from thestill.core.entity_linking.candidates import WikidataCandidateSource
from thestill.core.entity_linking.chooser import LLMCandidateChooser
from thestill.core.entity_linking.live_linker import LiveWikidataLinker
from thestill.core.entity_linking.rate_limiter import WikidataRateLimiter
from thestill.core.entity_linking.shared import ResolutionResult
from thestill.core.entity_linking.types import LinkContext
from thestill.core.entity_resolver import EntityResolver
from thestill.core.wikidata_client import WikidataSearchHit
from thestill.models.entities import ResolutionMethod
from thestill.repositories.sqlite_link_decision_repository import SqliteLinkDecisionRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository

KNOWN = {"Elon Musk": "Q317521", "OpenAI": "Q21708200"}
POD = "11111111-0000-4000-8000-000000000000"
CTX = LinkContext(episode_id="ep-1", podcast_id=POD)


class _StubReFinED:
    def process_text(self, text):
        return [
            SimpleNamespace(
                text=name,
                coarse_type="PER",
                predicted_entity=SimpleNamespace(
                    wikidata_entity_id=qid, wikipedia_entity_title=name, human_readable_name=name, description=None
                ),
            )
            for name, qid in KNOWN.items()
            if name in text
        ]


class _FakeWikidata:
    def search_entities(self, name, *, language="en", limit=8):
        return [WikidataSearchHit(KNOWN[name], name, "known thing")] if name in KNOWN else []

    def fetch_p31(self, qid):
        return []


def _refined(_tmp_path):
    return EntityResolver(preloaded_model=_StubReFinED())


def _live(tmp_path):
    db = str(tmp_path / "contract.db")
    SqlitePodcastRepository(db_path=db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, description) VALUES (?, 'https://x/y', 'Show', '')", (POD,)
        )
    chooser = LLMCandidateChooser(ScriptedProvider([pick_first_candidate] * 10))
    wikidata = _FakeWikidata()
    return LiveWikidataLinker(
        candidate_source=WikidataCandidateSource(wikidata, WikidataRateLimiter(1000, sleep=lambda _s: None)),
        chooser=chooser,
        cache=LinkDecisionCache(
            SqliteLinkDecisionRepository(db_path=db),
            linker_version=chooser.version,
            clock=lambda: datetime.now(timezone.utc),
        ),
        wikidata_client=wikidata,
    )


@pytest.fixture(params=[_refined, _live], ids=["refined", "live"])
def linker(request, tmp_path):
    return request.param(tmp_path)


def test_no_mentions_gives_no_results(linker):
    assert linker.resolve([]) == []


def test_one_result_per_mention(linker):
    mentions = [mention(1, "Elon Musk"), mention(2, "OpenAI", label="company"), mention(3, "Nobody Known")]
    results = linker.resolve(mentions, context=CTX)
    assert all(isinstance(r, ResolutionResult) for r in results)
    assert sorted(r.mention_id for r in results) == [1, 2, 3]


def test_a_resolved_result_carries_a_qid_and_a_typed_slug_id(linker):
    (result,) = linker.resolve([mention(1, "Elon Musk")], context=CTX)
    assert result.status == "resolved"
    assert result.entity.wikidata_qid == "Q317521"
    assert result.entity.id == "person:elon-musk"
    assert result.method in (ResolutionMethod.DIRECT, ResolutionMethod.LLM_LINKED)


def test_an_unresolvable_result_still_carries_a_local_entity(linker):
    (result,) = linker.resolve([mention(1, "Nobody Known")], context=CTX)
    assert result.status == "unresolvable" and result.method == ResolutionMethod.UNRESOLVABLE
    assert result.entity.wikidata_qid is None
    assert result.entity.id == "person:nobody-known"
    assert result.entity.aliases == []


def test_the_blacklist_is_consulted_with_the_spoken_name_and_the_qid(linker):
    seen = []

    def is_blacklisted(surface_form, qid):
        seen.append((surface_form, qid))
        return True

    (result,) = linker.resolve([mention(1, "Elon Musk")], is_blacklisted=is_blacklisted, context=CTX)
    assert result.status == "unresolvable"
    assert ("Elon Musk", "Q317521") in seen


def test_context_is_optional(linker):
    (result,) = linker.resolve([mention(1, "OpenAI", label="company")])
    assert result.entity.wikidata_qid == "Q21708200"
