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

"""Spec #106 — a reprocess can ask the cleaner for fresh episode facts.

Episode facts (speaker mapping, guests) are extracted once and reused on
every later clean. ``refresh_episode_facts`` re-extracts them; podcast
facts are user-edited and are never refreshed. The processor is stopped
at the segmenter, right after the facts step.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from thestill.core.transcript_cleaning_processor import TranscriptCleaningProcessor
from thestill.models.facts import EpisodeFacts, PodcastFacts


class _Stop(Exception):
    pass


_TRANSCRIPT = {
    "audio_file": "show/ep.wav",
    "language": "en",
    "text": "hello",
    "segments": [],
    "processing_time": 1.0,
    "model_used": "test",
    "timestamp": 0.0,
}


def _run(*, refresh: bool, existing: EpisodeFacts | None):
    facts_manager = MagicMock()
    facts_manager.load_podcast_facts.return_value = PodcastFacts(podcast_title="Show")
    facts_manager.load_episode_facts.return_value = existing
    extractor = MagicMock()
    extractor.extract_episode_facts.return_value = EpisodeFacts(
        episode_title="Ep", speaker_mapping={"SPEAKER_00": "Rory Stewart (Host)"}
    )
    segmenter = MagicMock()
    segmenter.repair.side_effect = _Stop

    with (
        patch("thestill.core.facts_manager.FactsManager", return_value=facts_manager),
        patch("thestill.core.facts_extractor.FactsExtractor", return_value=extractor),
        patch("thestill.utils.transcript_capabilities.classify_transcript_degeneracy", return_value=None),
        patch("thestill.core.transcript_cleaning_processor.TranscriptSegmenter", return_value=segmenter),
        pytest.raises(_Stop),
    ):
        TranscriptCleaningProcessor(MagicMock()).clean_transcript(
            transcript_data=_TRANSCRIPT,
            podcast_slug="show",
            episode_slug="ep",
            path_manager=MagicMock(),
            language="en",
            refresh_episode_facts=refresh,
        )
    return facts_manager, extractor


_OLD = EpisodeFacts(episode_title="Ep", speaker_mapping={"SPEAKER_00": "Ad Narrator 1"})


def test_existing_episode_facts_are_reused_by_default():
    facts_manager, extractor = _run(refresh=False, existing=_OLD)
    extractor.extract_episode_facts.assert_not_called()
    facts_manager.save_episode_facts.assert_not_called()


def test_refresh_re_extracts_and_replaces_episode_facts():
    facts_manager, extractor = _run(refresh=True, existing=_OLD)
    extractor.extract_episode_facts.assert_called_once()
    [(podcast_slug, episode_slug, saved), _] = facts_manager.save_episode_facts.call_args
    assert (podcast_slug, episode_slug) == ("show", "ep")
    assert saved.speaker_mapping == {"SPEAKER_00": "Rory Stewart (Host)"}


def test_refresh_never_touches_podcast_facts():
    facts_manager, _ = _run(refresh=True, existing=_OLD)
    facts_manager.save_podcast_facts.assert_not_called()
