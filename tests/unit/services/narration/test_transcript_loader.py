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

"""Tests for the cleaned-transcript turn loader (spec #33 Phase 1)."""

import json
from pathlib import Path

import pytest

from thestill.core.facts_manager import FactsManager
from thestill.models.facts import EpisodeFacts
from thestill.services.narration.transcript_loader import (
    TranscriptTurnLoader,
    _classify_role,
)
from thestill.utils.path_manager import PathManager


@pytest.fixture
def staged_storage(tmp_path: Path, sample_podcast, sample_episode):
    """Lay out a real ``data/`` tree with a sidecar JSON + facts file."""
    data_root = tmp_path / "data"
    data_root.mkdir()
    pm = PathManager(storage_path=str(data_root))
    pm.ensure_directories_exist()

    # Mark the episode as having a sidecar so the loader resolves a path.
    sidecar_md = "the-first-episode_abc_cleaned.md"
    sample_episode.clean_transcript_path = sidecar_md
    sample_episode.clean_transcript_json_path = sidecar_md

    sidecar_path = pm.clean_transcript_json_file(sample_podcast.slug, sidecar_md)
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)

    sidecar_path.write_text(
        json.dumps(
            {
                "episode_id": sample_episode.id,
                "playback_time_offset_seconds": 0.0,
                "algorithm_version": "v1",
                "segments": [
                    {
                        "id": 0,
                        "start": 0.0,
                        "end": 9.0,
                        "speaker": None,
                        "text": "[music intro]",
                        "kind": "intro",
                    },
                    {
                        "id": 1,
                        "start": 10.0,
                        "end": 30.0,
                        "speaker": "SPEAKER_00",
                        "text": "Good morning, this is the show. Today we have a guest with us.",
                        "kind": "content",
                    },
                    {
                        "id": 2,
                        "start": 30.5,
                        "end": 60.0,
                        "speaker": "SPEAKER_01",
                        "text": "Thanks for having me.",
                        "kind": "content",
                    },
                    {
                        "id": 3,
                        "start": 90.0,
                        "end": 120.0,
                        "speaker": None,
                        "text": "Sponsored by AcmeCorp.",
                        "kind": "ad_break",
                        "sponsor": "AcmeCorp",
                    },
                    {
                        "id": 4,
                        "start": 130.0,
                        "end": 165.0,
                        "speaker": "SPEAKER_00",
                        "text": "Continuing on after the break with our guest.",
                        "kind": "content",
                    },
                    {
                        "id": 5,
                        "start": 600.0,
                        "end": 640.0,
                        "speaker": "SPEAKER_01",
                        "text": "Final thoughts before we wrap up the episode.",
                        "kind": "content",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    facts_manager = FactsManager(pm)
    facts = EpisodeFacts(
        episode_title=sample_episode.title,
        speaker_mapping={
            "SPEAKER_00": "Jane Anchor (Host)",
            "SPEAKER_01": "Bob Guest (Guest)",
        },
        guests=["Bob Guest - Engineer"],
        topics_keywords=["shipping", "rollout"],
        ad_sponsors=["AcmeCorp"],
    )
    facts_manager.save_episode_facts(sample_podcast.slug, sample_episode.slug, facts)
    return pm, facts_manager


def test_loader_returns_only_content_segments(
    staged_storage, sample_podcast, sample_episode
) -> None:
    pm, fm = staged_storage
    loader = TranscriptTurnLoader(pm, fm)
    turns = loader.load(sample_podcast, sample_episode)
    kinds_present = {t.segment_id for t in turns}
    # The intro segment (id 0) and ad_break segment (id 3) must be dropped.
    assert 0 not in kinds_present
    assert 3 not in kinds_present
    assert kinds_present == {1, 2, 4, 5}


def test_loader_resolves_speakers_via_facts(
    staged_storage, sample_podcast, sample_episode
) -> None:
    pm, fm = staged_storage
    loader = TranscriptTurnLoader(pm, fm)
    turns = loader.load(sample_podcast, sample_episode)
    by_segment = {t.segment_id: t for t in turns}
    assert by_segment[1].speaker_name == "Jane Anchor"
    assert by_segment[1].speaker_role == "host"
    assert by_segment[2].speaker_name == "Bob Guest"
    assert by_segment[2].speaker_role == "guest"


def test_loader_flags_ad_adjacent_turns(
    staged_storage, sample_podcast, sample_episode
) -> None:
    pm, fm = staged_storage
    loader = TranscriptTurnLoader(pm, fm)
    turns = loader.load(sample_podcast, sample_episode)
    by_segment = {t.segment_id: t for t in turns}
    # Segment 4 starts 10s after the ad_break ends — within 30s window.
    assert by_segment[4].is_ad_adjacent is True
    # Segment 5 starts 480s after the ad_break — outside the window.
    assert by_segment[5].is_ad_adjacent is False
    # Segment 2 ends ~29.5s before the ad_break — boundary case, inside.
    assert by_segment[2].is_ad_adjacent is True


def test_loader_returns_empty_when_no_sidecar_path(
    staged_storage, sample_podcast, sample_episode
) -> None:
    pm, fm = staged_storage
    sample_episode.clean_transcript_json_path = None
    loader = TranscriptTurnLoader(pm, fm)
    assert loader.load(sample_podcast, sample_episode) == []


def test_loader_returns_empty_when_sidecar_missing_on_disk(
    staged_storage, sample_podcast, sample_episode
) -> None:
    pm, fm = staged_storage
    sample_episode.clean_transcript_json_path = "nonexistent_cleaned.md"
    loader = TranscriptTurnLoader(pm, fm)
    assert loader.load(sample_podcast, sample_episode) == []


def test_loader_returns_unresolved_role_when_facts_missing(
    tmp_path: Path, sample_podcast, sample_episode
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    pm = PathManager(storage_path=str(data_root))
    pm.ensure_directories_exist()
    sidecar = "the-first-episode_abc_cleaned.md"
    sample_episode.clean_transcript_path = sidecar
    sample_episode.clean_transcript_json_path = sidecar
    path = pm.clean_transcript_json_file(sample_podcast.slug, sidecar)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "episode_id": sample_episode.id,
                "segments": [
                    {
                        "id": 0,
                        "start": 0.0,
                        "end": 20.0,
                        "speaker": "SPEAKER_00",
                        "text": "Some words.",
                        "kind": "content",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    fm = FactsManager(pm)
    loader = TranscriptTurnLoader(pm, fm)
    turns = loader.load(sample_podcast, sample_episode)
    assert len(turns) == 1
    # No facts file → no resolved name; selector will skip this turn.
    assert turns[0].speaker_name is None
    assert turns[0].speaker_role == "unknown"


def test_classify_role_recognises_host_guest_unknown() -> None:
    assert _classify_role("Jane (Host)") == "host"
    assert _classify_role("Bob (Guest, Senior PM)") == "guest"
    assert _classify_role("Anonymous") == "unknown"
    assert _classify_role("Sam (CEO)") == "unknown"
