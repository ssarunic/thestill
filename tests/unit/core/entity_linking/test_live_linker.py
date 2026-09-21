"""Spec #81 - the live linker end to end, on fakes that fail independently."""

import sqlite3
from datetime import datetime, timezone

import pytest

from tests.unit.core.entity_linking.conftest import ScriptedProvider, mention, pick_first_candidate
from thestill.core.entity_linking.cache import LinkDecisionCache
from thestill.core.entity_linking.candidates import WikidataCandidateSource
from thestill.core.entity_linking.chooser import LLMCandidateChooser
from thestill.core.entity_linking.live_linker import LinkerUnavailableError, LiveWikidataLinker, group_mentions
from thestill.core.entity_linking.rate_limiter import WikidataRateLimiter
from thestill.core.entity_linking.shared import EntityLinkerBrokenError
from thestill.core.entity_linking.types import LinkContext
from thestill.core.wikidata_client import WikidataSearchHit, WikidataUnavailable
from thestill.models.entities import EntityType, ResolutionMethod
from thestill.repositories.sqlite_link_decision_repository import SqliteLinkDecisionRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository

POD = "11111111-0000-4000-8000-000000000000"
CTX = LinkContext(episode_id="ep-1", podcast_id=POD, podcast_title="Show", episode_title="Episode")

FILM = WikidataSearchHit("Q214801", "The Truman Show", "1998 film")
PRESIDENT = WikidataSearchHit("Q11613", "Harry S. Truman", "33rd US president")
DARIO = WikidataSearchHit("Q100", "Dario Amodei", "American AI researcher")


class FakeWikidata:
    """Search answers per name; an Exception value is raised for that name only."""

    def __init__(self, hits, p31=None):
        self.hits = hits
        self.p31 = p31 or {}
        self.searched = []

    def search_entities(self, name, *, language="en", limit=8):
        self.searched.append(name)
        answer = self.hits.get(name, [])
        if isinstance(answer, Exception):
            raise answer
        return answer

    def fetch_p31(self, qid):
        return self.p31.get(qid, [])


@pytest.fixture
def decisions(tmp_path):
    db = str(tmp_path / "linker.db")
    SqlitePodcastRepository(db_path=db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, description) VALUES (?, 'https://x/y', 'Show', '')", (POD,)
        )
    return SqliteLinkDecisionRepository(db_path=db)


def make_linker(decisions, wikidata, answers, min_confidence="medium"):
    provider = ScriptedProvider(answers)
    chooser = LLMCandidateChooser(provider)
    linker = LiveWikidataLinker(
        candidate_source=WikidataCandidateSource(wikidata, WikidataRateLimiter(1000, sleep=lambda _s: None)),
        chooser=chooser,
        cache=LinkDecisionCache(decisions, linker_version=chooser.version, clock=lambda: datetime.now(timezone.utc)),
        wikidata_client=wikidata,
        min_confidence=min_confidence,
    )
    return linker, provider


def _answer(*choices):
    return {"choices": [{"id": i, "qid": q, "confidence": c, "reason": "r"} for i, q, c in choices]}


def test_links_a_name_to_the_chosen_candidate(decisions):
    wikidata = FakeWikidata({"Dario Amodei": [DARIO]}, p31={"Q100": ["Q5"]})
    linker, _ = make_linker(decisions, wikidata, [pick_first_candidate])
    (result,) = linker.resolve([mention(1, "Dario Amodei")], context=CTX)
    assert result.status == "resolved" and result.method == ResolutionMethod.LLM_LINKED
    entity = result.entity
    assert (entity.id, entity.wikidata_qid, entity.canonical_name) == ("person:dario-amodei", "Q100", "Dario Amodei")
    assert entity.description == "American AI researcher" and entity.wikidata_instance_of == ["Q5"]
    assert entity.aliases == []  # identical to the canonical name: no alias


def test_every_mention_of_a_name_shares_one_decision_and_one_llm_call(decisions):
    wikidata = FakeWikidata({"Truman": [PRESIDENT, FILM]})
    linker, provider = make_linker(decisions, wikidata, [_answer(("n1", "Q214801", "high"))])
    results = linker.resolve([mention(1, "Truman"), mention(2, "truman"), mention(3, "TRUMAN")], context=CTX)
    assert {r.mention_id for r in results} == {1, 2, 3}
    assert {r.entity.wikidata_qid for r in results} == {"Q214801"}
    assert wikidata.searched == ["Truman"] and provider.call_count == 0 and len(provider.user_messages) == 1


def test_no_candidates_is_unresolvable_without_an_llm_call(decisions):
    linker, provider = make_linker(decisions, FakeWikidata({}), [])
    (result,) = linker.resolve([mention(1, "Zzyzx Person")], context=CTX)
    assert result.status == "unresolvable" and result.method == ResolutionMethod.UNRESOLVABLE
    assert result.entity.wikidata_qid is None and provider.user_messages == []


def test_the_model_saying_none_is_unresolvable(decisions):
    linker, _ = make_linker(decisions, FakeWikidata({"Mika": [DARIO]}), [_answer(("n1", None, "high"))])
    (result,) = linker.resolve([mention(1, "Mika")], context=CTX)
    assert result.status == "unresolvable"


def test_low_confidence_is_unresolvable_but_the_guess_is_remembered(decisions):
    linker, _ = make_linker(decisions, FakeWikidata({"Truman": [FILM]}), [_answer(("n1", "Q214801", "low"))])
    (result,) = linker.resolve([mention(1, "Truman")], context=CTX)
    assert result.status == "unresolvable"
    row = decisions.get("truman", POD)
    assert (row.qid, row.confidence) == ("Q214801", "low")


def test_a_blacklisted_pair_is_never_linked(decisions):
    linker, _ = make_linker(decisions, FakeWikidata({"Truman": [PRESIDENT]}), [pick_first_candidate])
    (result,) = linker.resolve([mention(1, "Truman")], context=CTX, is_blacklisted=lambda s, q: q == "Q11613")
    assert result.status == "unresolvable"
    assert decisions.get("truman", POD).qid is None


def test_a_remembered_link_is_dropped_if_it_was_blacklisted_since(decisions):
    wikidata = FakeWikidata({"Truman": [PRESIDENT]})
    linker, _ = make_linker(decisions, wikidata, [pick_first_candidate])
    linker.resolve([mention(1, "Truman")], context=CTX)
    (again,) = linker.resolve([mention(2, "Truman")], context=CTX, is_blacklisted=lambda s, q: True)
    assert again.status == "unresolvable"


def test_the_second_episode_is_answered_from_the_cache(decisions):
    wikidata = FakeWikidata({"Dario Amodei": [DARIO]})
    linker, provider = make_linker(decisions, wikidata, [pick_first_candidate])
    linker.resolve([mention(1, "Dario Amodei")], context=CTX)
    (result,) = linker.resolve([mention(2, "Dario Amodei", episode_id="ep-2")], context=CTX)
    assert result.entity.wikidata_qid == "Q100" and result.entity.canonical_name == "Dario Amodei"
    assert wikidata.searched == ["Dario Amodei"] and len(provider.user_messages) == 1
    assert decisions.get("dario amodei", POD).hits == 1


def test_a_spoken_variant_becomes_an_alias_only_when_it_resembles_the_name(decisions):
    wikidata = FakeWikidata({"Amodei": [DARIO], "the boss": [DARIO]})
    linker, _ = make_linker(decisions, wikidata, [pick_first_candidate])
    results = {r.mention_id: r for r in linker.resolve([mention(1, "Amodei"), mention(2, "the boss")], context=CTX)}
    assert results[1].entity.aliases == ["Amodei"]
    assert results[2].entity.aliases == []


def test_p31_can_move_an_entity_to_another_bucket(decisions):
    wikidata = FakeWikidata({"Truman": [FILM]}, p31={"Q214801": ["Q11424"]})  # instance of: film
    linker, _ = make_linker(decisions, wikidata, [pick_first_candidate])
    (result,) = linker.resolve([mention(1, "Truman", label="person")], context=CTX)
    assert result.entity.type != EntityType.PERSON


# --- failures ----------------------------------------------------------------


def test_a_wikidata_outage_for_one_name_keeps_the_rest_and_raises(decisions):
    wikidata = FakeWikidata({"Dario Amodei": [DARIO], "Ed Zitron": WikidataUnavailable("503")})
    linker, _ = make_linker(decisions, wikidata, [pick_first_candidate])
    with pytest.raises(LinkerUnavailableError) as err:
        linker.resolve([mention(1, "Dario Amodei"), mention(2, "Ed Zitron")], context=CTX)
    assert [r.mention_id for r in err.value.results] == [1] and err.value.unanswered_names == 1
    # the failed name was not written off, and the good one is remembered for the retry
    assert decisions.get("ed zitron", POD) is None
    assert decisions.get("dario amodei", POD).qid == "Q100"


def test_an_llm_outage_raises_unavailable_and_records_no_unresolvables(decisions):
    wikidata = FakeWikidata({"Dario Amodei": [DARIO]})
    linker, _ = make_linker(decisions, wikidata, [TimeoutError("down"), TimeoutError("down")])
    with pytest.raises(LinkerUnavailableError) as err:
        linker.resolve([mention(1, "Dario Amodei")], context=CTX)
    assert err.value.results == []
    assert decisions.get("dario amodei", POD) is None


def test_a_few_names_the_model_skipped_stay_pending_and_the_rest_succeed(decisions):
    names = ["A One", "B Two", "C Three", "D Four"]
    wikidata = FakeWikidata({n: [WikidataSearchHit(f"Q{i + 1}", n, "person")] for i, n in enumerate(names)})
    skip_first = lambda msg: {
        "choices": [c for c in pick_first_candidate(msg)["choices"] if c["id"] != "n1"]
    }  # noqa: E731
    linker, _ = make_linker(decisions, wikidata, [skip_first, {"choices": []}])
    results = linker.resolve([mention(i + 1, n) for i, n in enumerate(names)], context=CTX)
    assert sorted(r.mention_id for r in results) == [2, 3, 4]


def test_a_model_that_answers_nothing_useful_is_broken_and_nothing_is_kept(decisions):
    names = ["A One", "B Two", "C Three", "D Four"]
    wikidata = FakeWikidata({n: [WikidataSearchHit(f"Q{i + 1}", n, "person")] for i, n in enumerate(names)})
    invented = _answer(*[(f"n{i + 1}", "Q999999", "high") for i in range(4)])
    linker, _ = make_linker(decisions, wikidata, [invented])
    with pytest.raises(EntityLinkerBrokenError):
        linker.resolve([mention(i + 1, n) for i, n in enumerate(names)], context=CTX)
    assert all(decisions.get(n.casefold(), POD) is None for n in names)


def test_one_invented_qid_among_good_answers_is_not_broken(decisions):
    names = ["A One", "B Two", "C Three", "D Four"]
    wikidata = FakeWikidata({n: [WikidataSearchHit(f"Q{i + 1}", n, "person")] for i, n in enumerate(names)})
    answer = _answer(("n1", "Q999999", "high"), ("n2", "Q2", "high"), ("n3", "Q3", "high"), ("n4", "Q4", "high"))
    linker, _ = make_linker(decisions, wikidata, [answer])
    results = linker.resolve([mention(i + 1, n) for i, n in enumerate(names)], context=CTX)
    assert sorted(r.mention_id for r in results) == [2, 3, 4]
    assert decisions.get("a one", POD) is None


def test_link_writes_nothing(decisions):
    wikidata = FakeWikidata({"Dario Amodei": [DARIO]})
    linker, _ = make_linker(decisions, wikidata, [pick_first_candidate])
    outcome = linker.link(group_mentions([mention(1, "Dario Amodei")]), CTX)
    assert outcome.decisions["dario amodei"].qid == "Q100"
    assert decisions.get("dario amodei", POD) is None


def test_no_mentions_is_no_work(decisions):
    linker, provider = make_linker(decisions, FakeWikidata({}), [])
    assert linker.resolve([]) == []
