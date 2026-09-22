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

"""Spec #81 - a human correction makes the live linker decide the name again."""

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.unit.core.test_entity_review import _corp, _FakeQueue, _FakeRepo
from thestill.core.entity_linking.cache import invalidate_link_decisions
from thestill.models.entities import EntityRecord, EntityType

ROOT = Path(__file__).resolve().parents[4] / "thestill"


def test_the_spoken_name_is_folded_before_deleting():
    decisions = MagicMock()
    decisions.delete.return_value = 2
    assert invalidate_link_decisions(decisions, "  ANTHROPIC ") == 2
    decisions.delete.assert_called_once_with("anthropic")


def test_a_caller_built_without_the_repository_is_a_no_op():
    assert invalidate_link_decisions(None, "Anthropic") == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(action="blacklist", wrong_qid="Q240581"),
        dict(action="drop"),
        dict(action="force_unresolvable"),
        dict(action="force_entity", target_entity_id="company:anthropic"),
    ],
)
def test_every_kind_of_correction_invalidates_before_it_re_resolves(kwargs):
    order = []
    decisions = MagicMock()
    decisions.delete.side_effect = lambda key: order.append(("invalidate", key)) or 1
    target = EntityRecord(id="company:anthropic", type=EntityType.COMPANY, canonical_name="Anthropic")
    repo = _FakeRepo(entities={target.id: target}, mentions=[(1, "ep1")])
    queue = _FakeQueue()
    original_add = queue.add_task

    def add_task(*a, **k):
        order.append(("enqueue",))
        return original_add(*a, **k)

    queue.add_task = add_task
    _corp(repo, queue, surface_form="Anthropic", link_decisions=decisions, **kwargs)
    assert order[0] == ("invalidate", "anthropic")
    assert ("enqueue",) in order[1:]


def test_no_module_records_a_correction_without_invalidating():
    """A fifth write path cannot be added without this failing: any module
    outside the repositories that writes an override or a blacklist entry
    must also call the shared helper."""
    write = re.compile(r"\.(add_override|add_blacklist_entry)\(")
    offenders = []
    for path in ROOT.rglob("*.py"):
        if "repositories" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        if write.search(source) and "invalidate_link_decisions(" not in source:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
