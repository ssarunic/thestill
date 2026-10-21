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

"""Spec #105 decision 3 — which entity pages are indexed."""

import pytest

from thestill.web.public_access import EntityIndexFacts, entity_is_indexable, instance_of_list


def facts(entity_type="person", said=0, about=0, episodes=1, role=False, qid=None, instance_of=()):
    return EntityIndexFacts(f"{entity_type}:x", entity_type, said, about, episodes, role, qid, tuple(instance_of))


@pytest.mark.parametrize(
    "case, expected",
    [
        # Value: the entity speaks.
        (facts(said=5), True),
        (facts(said=4), False),
        (facts(role=True), True),  # a guest with no quotes yet
        # Value: discussed in depth.
        (facts("topic", about=10), True),
        (facts("topic", about=9), False),
        # Value: discussed widely.
        (facts("company", about=5, episodes=2), True),
        (facts("company", about=4, episodes=2), False),
        (facts("company", about=9, episodes=1), False),
        (facts(said=2, about=3, episodes=2), True),
        # The old 3-quote bar alone is not enough.
        (facts("topic", about=3, episodes=1), False),
    ],
)
def test_value_test(case, expected):
    assert entity_is_indexable(case) is expected


def test_type_test_uses_wikidata_only_when_it_is_known():
    # A person page for something Wikidata says is not a human.
    assert not entity_is_indexable(facts(said=20, qid="Q1", instance_of=["Q11424"]))
    assert entity_is_indexable(facts(said=20, qid="Q1", instance_of=["Q5"]))
    # instance_of not fetched yet: the value test decides.
    assert entity_is_indexable(facts(said=20, qid="Q1", instance_of=[]))
    # No Wikidata link at all (most guests): the value test decides.
    assert entity_is_indexable(facts(role=True))
    # A thing whose Wikidata item is a human fails the same way.
    assert not entity_is_indexable(facts("company", about=20, qid="Q2", instance_of=["Q5"]))


def test_the_noindex_list_wins():
    assert not entity_is_indexable(facts(said=50), noindex={"person:x"})


@pytest.mark.parametrize("stored", [["Q5", "Q215627"], '["Q5", "Q215627"]'])
def test_instance_of_reads_both_backends(stored):
    assert instance_of_list(stored) == ("Q5", "Q215627")


def test_instance_of_tolerates_junk():
    assert instance_of_list("not json") == ()
    assert instance_of_list(None) == ()
