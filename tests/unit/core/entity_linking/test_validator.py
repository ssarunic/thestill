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

"""Spec #81 Stage 3 - validating the chooser's answer."""

import pytest

from tests.unit.core.entity_linking.conftest import group
from thestill.core.entity_linking.types import Candidate, ChoiceDecision
from thestill.core.entity_linking.validator import BLACKLISTED, NOT_OFFERED, DecisionValidator, meets_confidence

OFFERED = [
    Candidate("Q11613", "Harry S. Truman", "33rd US president"),
    Candidate("Q214801", "The Truman Show", "1998 film"),
]
TRUMAN = group("Truman")


def _choice(qid, confidence="high"):
    return ChoiceDecision(surface_key="truman", qid=qid, confidence=confidence, reason="why")


def test_an_offered_qid_is_kept_with_its_candidate():
    result = DecisionValidator().validate(_choice("Q214801"), OFFERED, TRUMAN)
    assert result.rejection is None
    assert (result.decision.qid, result.decision.candidate.label) == ("Q214801", "The Truman Show")


def test_a_qid_that_was_not_offered_is_no_decision_at_all():
    result = DecisionValidator().validate(_choice("Q42"), OFFERED, TRUMAN)
    assert result.decision is None and result.rejection == NOT_OFFERED


def test_none_is_a_decision():
    result = DecisionValidator().validate(_choice(None, "medium"), OFFERED, TRUMAN)
    assert result.decision.qid is None and result.decision.confidence == "medium" and result.rejection is None


def test_a_blacklisted_pair_becomes_a_decided_none():
    seen = []

    def is_blacklisted(surface, qid):
        seen.append((surface, qid))
        return qid == "Q11613"

    result = DecisionValidator(is_blacklisted=is_blacklisted).validate(_choice("Q11613"), OFFERED, TRUMAN)
    assert result.decision.qid is None and result.rejection == BLACKLISTED
    assert seen == [("Truman", "Q11613")]


def test_low_confidence_keeps_its_candidate_for_the_review_queue():
    result = DecisionValidator().validate(_choice("Q214801", "low"), OFFERED, TRUMAN)
    assert (result.decision.qid, result.decision.confidence) == ("Q214801", "low")


@pytest.mark.parametrize(
    "confidence, minimum, ok",
    [
        ("high", "medium", True),
        ("medium", "medium", True),
        ("low", "medium", False),
        ("low", "low", True),
        ("medium", "high", False),
    ],
)
def test_meets_confidence(confidence, minimum, ok):
    assert meets_confidence(confidence, minimum) is ok
