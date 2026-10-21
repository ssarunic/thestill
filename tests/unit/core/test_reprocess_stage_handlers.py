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

"""Spec #106 — clean and reindex behave differently inside a reprocess run.

Transcribe has its own tests in ``test_handle_transcribe_idempotent.py``.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

from thestill.core.queue_manager import Task, TaskStage, TaskStatus
from thestill.core.task_handlers import handle_clean, handle_reindex
from thestill.models.annotated_transcript import AnnotatedSegment, AnnotatedTranscript

REPROCESS = {
    "run_full_pipeline": True,
    "initiated_by": "reprocess",
    "reprocess": {"from_stage": "clean", "refresh_episode_facts": True, "requested_at": "2026-10-21T12:00:00+00:00"},
}


def _task(stage: TaskStage, metadata: Optional[Dict[str, Any]] = None) -> Task:
    return Task(
        id=str(uuid.uuid4()),
        episode_id="ep-1",
        stage=stage,
        status=TaskStatus.PROCESSING,
        metadata=metadata or {},
    )


def _state(episode) -> MagicMock:
    podcast = MagicMock()
    podcast.slug = "the-show"
    podcast.language = "en"
    state = MagicMock()
    state.repository.get_episode.return_value = (podcast, episode)
    return state


class TestReindex:
    def _run(self, tmp_path: Path, metadata) -> MagicMock:
        sidecar = tmp_path / "ep_cleaned.json"
        sidecar.write_text(
            AnnotatedTranscript(
                episode_id="ep-1",
                segments=[
                    AnnotatedSegment(id=0, start=0, end=1, speaker="A", text="hello there", source_segment_ids=[0])
                ],
            ).model_dump_json(),
            encoding="utf-8",
        )
        episode = MagicMock()
        episode.id = "ep-1"
        episode.clean_transcript_json_path = "the-show/ep_cleaned.json"
        state = _state(episode)
        state.path_manager.clean_transcript_file.return_value = sidecar
        writer = MagicMock()
        writer.write_episode.return_value = 1
        with patch("thestill.repositories.factory.make_chunk_writer", return_value=writer):
            handle_reindex(_task(TaskStage.REINDEX, metadata), state)
        return writer

    def test_first_pass_leaves_existing_chunks_alone(self, tmp_path):
        writer = self._run(tmp_path, {})
        assert writer.write_episode.call_args.kwargs["force"] is False

    def test_reprocess_re_embeds(self, tmp_path):
        writer = self._run(tmp_path, REPROCESS)
        assert writer.write_episode.call_args.kwargs["force"] is True


class TestClean:
    def _clean_kwargs(self, tmp_path: Path, metadata) -> Dict[str, Any]:
        episode = MagicMock()
        episode.raw_transcript_path = "the-show/ep_transcript.json"
        state = _state(episode)
        state.path_manager.raw_transcript_file.return_value = Path("/data/raw/the-show/ep_transcript.json")
        state.path_manager.clean_transcripts_dir.return_value = tmp_path
        state.config.file_storage.read_text.return_value = "{}"
        processor = MagicMock()
        captured: Dict[str, Any] = {}

        def stop(**kwargs):
            captured.update(kwargs)
            raise RuntimeError("stop after the call under test")

        processor.clean_transcript.side_effect = stop
        with (
            patch("thestill.core.llm_provider.create_llm_provider_from_config"),
            patch("thestill.core.transcript_cleaning_processor.TranscriptCleaningProcessor", return_value=processor),
            pytest.raises(Exception),
        ):
            handle_clean(_task(TaskStage.CLEAN, metadata), state)
        return captured

    def test_first_pass_reuses_episode_facts(self, tmp_path):
        assert self._clean_kwargs(tmp_path, {})["refresh_episode_facts"] is False

    def test_reprocess_passes_the_refresh_option(self, tmp_path):
        assert self._clean_kwargs(tmp_path, REPROCESS)["refresh_episode_facts"] is True
