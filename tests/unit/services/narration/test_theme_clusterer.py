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

"""Tests for the theme clusterer (spec #33 Phase 2 stage 1)."""

import json
from typing import List

import pytest

from thestill.services.narration.models import EpisodeBrief
from thestill.services.narration.theme_clusterer import ThemeClusterer

from tests.conftest import MockLLMProvider


def _briefs(*ids: str) -> List[EpisodeBrief]:
    return [
        EpisodeBrief(
            episode_id=eid,
            podcast_title=f"Pod {eid}",
            episode_title=f"Title {eid}",
            topics=("ai",) if "ai" in eid else (),
            gist=f"Gist for {eid}.",
        )
        for eid in ids
    ]


def _theme_response(payload: dict) -> str:
    return json.dumps(payload)


def test_returns_empty_plan_when_no_episodes() -> None:
    plan = ThemeClusterer(MockLLMProvider()).cluster(briefs=[], target_duration_seconds=300)
    assert plan.segments == ()
    assert plan.tail_ids == ()


def test_groups_episodes_into_segments_and_tail() -> None:
    provider = MockLLMProvider()
    provider.add_response(
        "target spoken duration",
        _theme_response(
            {
                "segments": [
                    {
                        "theme": "AI agents in production",
                        "angle": "Two PMs disagree on shipping by non-engineers",
                        "episode_ids": ["ai-1", "ai-2"],
                        "rank": 1,
                    },
                    {
                        "theme": "Capital markets",
                        "angle": "Q1 capital flows recap",
                        "episode_ids": ["finance-1"],
                        "rank": 2,
                    },
                ],
                "tail": ["misc-1"],
            }
        ),
    )
    plan = ThemeClusterer(provider).cluster(
        briefs=_briefs("ai-1", "ai-2", "finance-1", "misc-1"),
        target_duration_seconds=300,
    )
    assert [s.rank for s in plan.segments] == [1, 2]
    assert plan.segments[0].episode_ids == ("ai-1", "ai-2")
    assert plan.segments[1].episode_ids == ("finance-1",)
    assert plan.tail_ids == ("misc-1",)


def test_drops_unknown_episode_ids_from_model_output() -> None:
    provider = MockLLMProvider()
    provider.add_response(
        "target spoken duration",
        _theme_response(
            {
                "segments": [
                    {
                        "theme": "Mixed",
                        "angle": "Half-real-half-hallucinated lineup",
                        "episode_ids": ["ai-1", "hallucinated-id"],
                        "rank": 1,
                    }
                ],
                "tail": [],
            }
        ),
    )
    plan = ThemeClusterer(provider).cluster(
        briefs=_briefs("ai-1", "ai-2"),
        target_duration_seconds=300,
    )
    assert plan.segments[0].episode_ids == ("ai-1",)
    assert plan.tail_ids == ("ai-2",)


def test_caps_segments_at_four() -> None:
    provider = MockLLMProvider()
    provider.add_response(
        "target spoken duration",
        _theme_response(
            {
                "segments": [
                    {
                        "theme": f"Theme {i}",
                        "angle": f"Angle {i}",
                        "episode_ids": [f"ep-{i}"],
                        "rank": i,
                    }
                    for i in range(1, 7)
                ],
                "tail": [],
            }
        ),
    )
    plan = ThemeClusterer(provider).cluster(
        briefs=_briefs(*[f"ep-{i}" for i in range(1, 7)]),
        target_duration_seconds=300,
    )
    assert len(plan.segments) == 4
    # Episodes whose segments were truncated land in the tail so nothing
    # is silently dropped from the run.
    assert set(plan.tail_ids) == {"ep-5", "ep-6"}


def test_falls_back_to_tail_only_on_llm_error() -> None:
    class _BoomProvider(MockLLMProvider):
        def generate_structured(self, *args, **kwargs):  # type: ignore[override]
            raise RuntimeError("upstream timeout")

    plan = ThemeClusterer(_BoomProvider()).cluster(
        briefs=_briefs("ai-1", "ai-2"),
        target_duration_seconds=300,
    )
    assert plan.segments == ()
    assert set(plan.tail_ids) == {"ai-1", "ai-2"}
