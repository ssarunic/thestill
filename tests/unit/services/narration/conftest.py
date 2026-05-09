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

"""Shared fixtures for narrated-digest (spec #33) unit tests."""

from datetime import datetime, timezone
from typing import List

import pytest
from pydantic import HttpUrl

from thestill.models.podcast import Episode, Podcast
from thestill.services.narration.transcript_loader import ResolvedTurn


@pytest.fixture
def sample_podcast() -> Podcast:
    return Podcast(
        id="pod-001",
        title="Test Podcast",
        description="A test podcast.",
        rss_url=HttpUrl("https://example.com/feed.xml"),
        slug="test-podcast",
    )


@pytest.fixture
def sample_episode() -> Episode:
    episode = Episode(
        external_id="ep-guid-001",
        podcast_id="pod-001",
        title="The First Episode",
        description="Episode description.",
        pub_date=datetime(2026, 5, 6, 7, 0, 0, tzinfo=timezone.utc),
        audio_url=HttpUrl("https://example.com/audio.mp3"),
        slug="the-first-episode",
        duration=3600,
    )
    return episode


def make_turn(
    *,
    episode_id: str = "ep-001",
    podcast_title: str = "Test Podcast",
    segment_id: int = 0,
    speaker_label: str = "SPEAKER_00",
    speaker_name: str = "Jane Doe",
    speaker_role: str = "host",
    text: str = "",
    start_seconds: float = 0.0,
    end_seconds: float = 0.0,
    is_ad_adjacent: bool = False,
) -> ResolvedTurn:
    return ResolvedTurn(
        episode_id=episode_id,
        podcast_title=podcast_title,
        segment_id=segment_id,
        speaker_label=speaker_label,
        speaker_name=speaker_name,
        speaker_role=speaker_role,
        text=text,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        is_ad_adjacent=is_ad_adjacent,
    )


@pytest.fixture
def make_turn_factory():
    """Convenience factory exposed as a fixture for parameterised tests."""
    return make_turn


def well_formed_quote_text(words: int = 50) -> str:
    """Build a sample quote that passes self-containment heuristics.

    The text starts with a noun phrase (no leading pronoun), avoids
    dangling-reference markers, and ends on a sentence boundary so the
    selector's containment penalties stay at zero.
    """
    seed = (
        "The shipping pipeline cleared every test in the new run, "
        "the monitoring dashboard went green for the first time, "
        "and the on-call rotation reported zero pages overnight. "
        "Tomorrow we plan to expand the rollout to the second region. "
    )
    repeated: List[str] = []
    while sum(len(s.split()) for s in repeated) < words:
        repeated.append(seed)
    text = " ".join(repeated)
    tokens = text.split()
    if len(tokens) > words:
        tokens = tokens[:words]
        # Ensure a sentence boundary.
        last = tokens[-1]
        if not last.endswith(("." , "!", "?")):
            tokens[-1] = last.rstrip(",;:") + "."
    return " ".join(tokens)
