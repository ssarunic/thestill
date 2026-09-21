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

"""Spec #81 Stage 1 - Wikidata candidate search."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from thestill.core.entity_linking.candidates import WikidataCandidateSource, is_generic_noun
from thestill.core.entity_linking.rate_limiter import WikidataRateLimiter
from thestill.core.entity_linking.types import NameGroup, surface_key
from thestill.core.wikidata_client import WikidataClient, WikidataSearchHit, WikidataUnavailable


def _group(name, label="person"):
    return NameGroup(
        surface_key=surface_key(name), surface_form=name, surface_label=label, mention_ids=[1], excerpts=[]
    )


def _limiter():
    return WikidataRateLimiter(1000, clock=lambda: 0.0, sleep=lambda _s: None)


class ScriptedSearch:
    """Answers per (name, language); an Exception value is raised. Each name
    fails or succeeds on its own (failure-mode catalogue: consistent mocks)."""

    def __init__(self, script):
        self.script = script
        self.calls = []

    def search_entities(self, name, *, language="en", limit=8):
        self.calls.append((name, language))
        answer = self.script.get((name, language), [])
        if isinstance(answer, Exception):
            raise answer
        return answer


HIT = WikidataSearchHit(qid="Q1", label="Dario Amodei", description="American AI researcher")


def test_hits_become_candidates():
    source = WikidataCandidateSource(ScriptedSearch({("Dario Amodei", "en"): [HIT]}), _limiter())
    fetched = source.fetch([_group("Dario Amodei")])
    (candidate,) = fetched.candidates["dario amodei"]
    assert (candidate.qid, candidate.label, candidate.description) == ("Q1", "Dario Amodei", "American AI researcher")
    assert fetched.failed == set()


def test_no_hits_is_an_answer_not_a_failure():
    fetched = WikidataCandidateSource(ScriptedSearch({}), _limiter()).fetch([_group("Zzyzx Person")])
    assert fetched.candidates == {"zzyzx person": []}
    assert fetched.failed == set()


def test_one_failing_name_does_not_lose_the_others():
    search = ScriptedSearch({("Good", "en"): [HIT], ("Bad", "en"): WikidataUnavailable("503")})
    fetched = WikidataCandidateSource(search, _limiter()).fetch([_group("Bad"), _group("Good")])
    assert fetched.failed == {"bad"}
    assert "bad" not in fetched.candidates
    assert len(fetched.candidates["good"]) == 1


def test_falls_back_to_english_only_when_the_episode_language_finds_nothing():
    search = ScriptedSearch({("Tesla", "hr"): [], ("Tesla", "en"): [HIT], ("Zagreb", "hr"): [HIT]})
    WikidataCandidateSource(search, _limiter()).fetch([_group("Tesla"), _group("Zagreb")], language="hr")
    assert search.calls == [("Tesla", "hr"), ("Tesla", "en"), ("Zagreb", "hr")]


def test_generic_nouns_are_not_searched():
    search = ScriptedSearch({})
    fetched = WikidataCandidateSource(search, _limiter()).fetch([_group("founder", "topic")])
    assert search.calls == []
    assert fetched.candidates == {"founder": []}
    assert fetched.skipped_generic == 1


@pytest.mark.parametrize(
    "name, label, generic",
    [
        ("agent", "topic", True),
        ("Founder", "person", True),  # the word, whatever it was tagged as
        ("brain rot", "topic", False),
        # single lowercase words tagged "topic" that ARE entities: a broad
        # rule would remember these as "no such entity" for a month
        ("bitcoin", "topic", False),
        ("ozempic", "topic", False),
        ("kubernetes", "topic", False),
        ("rationalism", "topic", False),
        ("apple", "company", False),
    ],
)
def test_generic_noun_rule(name, label, generic):
    assert is_generic_noun(_group(name, label)) is generic


def test_control_characters_in_wikidata_text_are_stripped():
    dirty = WikidataSearchHit(qid="Q1", label="Dario\x00 Amodei", description=" AI\x07 researcher ")
    fetched = WikidataCandidateSource(ScriptedSearch({("Dario Amodei", "en"): [dirty]}), _limiter()).fetch(
        [_group("Dario Amodei")]
    )
    (candidate,) = fetched.candidates["dario amodei"]
    assert (candidate.label, candidate.description) == ("Dario Amodei", "AI researcher")


def test_retry_after_holds_back_every_later_request():
    limiter = MagicMock()
    search = ScriptedSearch({("Bad", "en"): WikidataUnavailable("429", retry_after_seconds=30)})
    WikidataCandidateSource(search, limiter).fetch([_group("Bad")])
    limiter.hold_off.assert_called_once_with(30)


def test_every_search_waits_for_a_rate_limit_slot():
    limiter = MagicMock()
    WikidataCandidateSource(ScriptedSearch({}), limiter).fetch([_group("A"), _group("B")])
    assert limiter.acquire.call_count == 2


# --- WikidataClient.search_entities -----------------------------------------


def _response(status=200, payload=None, headers=None, bad_json=False):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = headers or {}
    if bad_json:
        resp.json.side_effect = ValueError("nope")
    else:
        resp.json.return_value = payload
    return resp


def _search(resp=None, side_effect=None):
    with patch("thestill.core.wikidata_client.requests.get", return_value=resp, side_effect=side_effect) as get:
        return WikidataClient().search_entities("Dario Amodei", language="en"), get


def test_search_parses_hits_and_skips_malformed_ids():
    payload = {
        "search": [
            {"id": "Q1", "label": "Dario Amodei", "description": "AI researcher"},
            {"id": "P31", "label": "instance of"},
            {"label": "no id"},
            {"id": "Q2"},
        ]
    }
    hits, get = _search(_response(payload=payload))
    assert [(h.qid, h.label, h.description) for h in hits] == [("Q1", "Dario Amodei", "AI researcher"), ("Q2", "", "")]
    params = get.call_args.kwargs["params"]
    assert params["action"] == "wbsearchentities" and params["search"] == "Dario Amodei" and params["maxlag"] == "5"


def test_search_with_no_results_returns_an_empty_list():
    hits, _ = _search(_response(payload={"search": []}))
    assert hits == []


@pytest.mark.parametrize(
    "resp, side_effect",
    [
        (None, requests.ConnectionError("down")),
        (_response(status=503), None),
        (_response(bad_json=True), None),
        (_response(payload={"error": {"code": "maxlag"}}), None),
        (_response(payload={"unexpected": True}), None),
    ],
)
def test_search_failures_raise_instead_of_returning_nothing(resp, side_effect):
    with pytest.raises(WikidataUnavailable):
        _search(resp, side_effect)


def test_search_carries_retry_after_seconds():
    with pytest.raises(WikidataUnavailable) as err:
        _search(_response(status=429, headers={"Retry-After": "12"}))
    assert 11 <= err.value.retry_after_seconds <= 12
