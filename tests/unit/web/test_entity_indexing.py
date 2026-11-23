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
        # A person who takes part: host, recurring voice or guest.
        (facts(role=True), True),  # a guest with no quotes yet
        (facts(role=True, said=40, about=12, episodes=9), True),
        # A person only talked about, however much.
        (facts(about=200, episodes=30), False),
        (facts(said=50, about=10, episodes=5), False),  # quoted, but no role on record
        # Things are never indexed, even with a role-like record.
        (facts("topic", about=100, episodes=20), False),
        (facts("company", about=50, episodes=10), False),
        (facts("product", said=5, about=50, episodes=10, role=True), False),
    ],
)
def test_only_people_with_a_role_are_indexed(case, expected):
    assert entity_is_indexable(case) is expected


def test_type_test_uses_wikidata_only_when_it_is_known():
    # A person page for something Wikidata says is not a human.
    assert not entity_is_indexable(facts(role=True, qid="Q1", instance_of=["Q11424"]))
    assert entity_is_indexable(facts(role=True, qid="Q1", instance_of=["Q5"]))
    # instance_of not fetched yet: the role decides.
    assert entity_is_indexable(facts(role=True, qid="Q1", instance_of=[]))
    # No Wikidata link at all (most guests): the role decides.
    assert entity_is_indexable(facts(role=True))


def test_the_noindex_list_wins():
    assert not entity_is_indexable(facts(role=True), noindex={"person:x"})


@pytest.mark.parametrize("stored", [["Q5", "Q215627"], '["Q5", "Q215627"]'])
def test_instance_of_reads_both_backends(stored):
    assert instance_of_list(stored) == ("Q5", "Q215627")


def test_instance_of_tolerates_junk():
    assert instance_of_list("not json") == ()
    assert instance_of_list(None) == ()
