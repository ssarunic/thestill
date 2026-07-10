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

"""Shared fixtures for eval-run tests.

``SequenceProvider`` scripts judge behaviour per call (FM-5: tests must
exercise malformed, control-char-laden, and schema-violating judge output,
not just a consistent happy-path mock).
"""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import List

import pytest

from tests.conftest import MockLLMProvider
from thestill.evals.models import JudgeInfo
from thestill.evals.runner import EvalRunner, JudgeResolution
from thestill.models.podcast import Episode, Podcast
from thestill.utils.path_manager import PathManager

VALID_RAW_REPORT = {
    "accuracy": {
        "name_errors": {"count": 1, "examples": ["Jon -> John"]},
        "entity_errors": {"count": 0, "examples": []},
        "word_errors": {"count": 2, "examples": ["teh", "adn"]},
        "faithfulness_issues": {"count": 0, "examples": []},
    },
    "completeness": {"missing_sections": {"count": 0, "examples": []}},
    "structure": {
        "ads_detected": True,
        "intro_detected": True,
        "outro_detected": False,
        "speaker_turns_marked": True,
        "timestamps_present": True,
    },
    "scores": {"accuracy": 7, "completeness": 9, "entity_handling": 8, "structural_clarity": 6},
    "summary": {"strengths": ["clear"], "weaknesses": ["typos"], "verdict": "decent"},
}


class SequenceProvider(MockLLMProvider):
    """Mock provider returning scripted responses in order."""

    def __init__(self, responses: List[str], model_name: str = "mock-judge"):
        super().__init__(model_name=model_name)
        self._sequence = list(responses)

    def chat_completion(self, messages, temperature=None, max_tokens=None, response_format=None):
        self.call_count += 1
        self.last_messages = messages
        self.last_temperature = temperature
        if not self._sequence:
            raise AssertionError("SequenceProvider exhausted: more judge calls than scripted responses")
        return self._sequence.pop(0)


def make_judge(responses: List[str], **info_overrides) -> JudgeResolution:
    provider = SequenceProvider(responses)
    defaults = dict(provider="mock", model="mock-judge", temperature=0.0, pinned=True)
    defaults.update(info_overrides)
    return JudgeResolution(provider=provider, info=JudgeInfo(**defaults))


def make_episode(slug: str, podcast_slug: str, **overrides) -> Episode:
    fields = dict(
        external_id=f"guid-{slug}",
        title=slug.replace("-", " "),
        slug=slug,
        description="test episode",
        audio_url="https://example.com/audio.mp3",
        pub_date=datetime(2026, 7, 1, tzinfo=timezone.utc),
        duration=600,
        raw_transcript_path=f"{podcast_slug}/{slug}_transcript.json",
        clean_transcript_path=f"{podcast_slug}/{slug}_cleaned.md",
        summary_path=f"{podcast_slug}/{slug}_summary.md",
    )
    fields.update(overrides)
    return Episode(**fields)


def make_podcast(slug: str, episodes: List[Episode]) -> Podcast:
    return Podcast(
        title=slug.replace("-", " "),
        slug=slug,
        rss_url=f"https://example.com/{slug}/feed.xml",
        description="test podcast",
        episodes=episodes,
    )


@pytest.fixture
def eval_env(tmp_path):
    """A PathManager rooted in tmp_path with one podcast/episode's artifacts on disk."""
    pm = PathManager(str(tmp_path))
    podcast = make_podcast("test-pod", [])
    episode = make_episode("ep-one", "test-pod")
    podcast.episodes.append(episode)

    for resolver, content in (
        (pm.raw_transcript_file(episode.raw_transcript_path), json.dumps({"segments": ["hello world"]})),
        (pm.clean_transcript_file(episode.clean_transcript_path), "**Host:** hello world\n"),
        (pm.summary_file(episode.summary_path), "## 1. \U0001f399️ The Gist\nA test.\n"),
    ):
        resolver.parent.mkdir(parents=True, exist_ok=True)
        resolver.write_text(content, encoding="utf-8")

    config = SimpleNamespace(
        eval_judge_provider="",
        eval_judge_model="",
        eval_judge_temperature=0.0,
        llm_provider="mock",
    )
    feed_manager = SimpleNamespace(list_podcasts=lambda: [podcast])
    runner = EvalRunner(config, pm, feed_manager)
    return SimpleNamespace(
        runner=runner, path_manager=pm, podcast=podcast, episode=episode, config=config, tmp_path=tmp_path
    )
