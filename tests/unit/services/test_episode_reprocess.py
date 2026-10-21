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

"""Spec #106 — where a reprocess starts, and what it refuses.

The endpoint tests (``tests/integration/web/test_api_episode_reprocess.py``)
cover the HTTP mapping on a real queue; these pin the stage selection.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from thestill.core.queue_manager import TaskStage
from thestill.core.reprocess_metadata import reprocess_options, reprocess_requested_at
from thestill.models.podcast import Episode
from thestill.services.episode_reprocess import ReprocessRejected, queue_reprocess


def _episode(**overrides) -> Episode:
    fields = dict(
        id="ep-1",
        external_id="ext-1",
        title="Ep",
        description="",
        pub_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        audio_url="https://example.com/ep.mp3",
        raw_transcript_path="show/ep_transcript.json",
        clean_transcript_path="show/ep_cleaned.md",
        clean_transcript_json_path="show/ep_cleaned.json",
        summary_path="show/ep_cleaned_summary.md",
    )
    fields.update(overrides)
    return Episode(**fields)


def _config(*, provider: str = "whisper", files: tuple = ()) -> MagicMock:
    config = MagicMock()
    config.transcription_provider = provider
    config.path_manager.downsampled_audio_file.side_effect = lambda p: Path("downsampled") / p
    config.path_manager.original_audio_file.side_effect = lambda p: Path("original") / p
    config.path_manager.to_relative.side_effect = str
    config.file_storage.exists.side_effect = lambda key: key in files
    return config


def _queue(*, busy: bool = False) -> MagicMock:
    queue = MagicMock()
    queue.has_active_task_for_episode.return_value = busy
    if busy:
        queue.add_task_if_episode_idle.return_value = None
    else:
        queue.add_task_if_episode_idle.return_value.id = "task-1"
    return queue


def _reprocess(episode: Episode, from_stage: str, *, config=None, queue=None, **kwargs):
    repository = MagicMock()
    repository.get_episode.return_value = (MagicMock(), episode)
    return queue_reprocess(
        repository=repository,
        queue_manager=queue or _queue(),
        config=config or _config(),
        episode_id=episode.id,
        from_stage=from_stage,
        **kwargs,
    )


class TestTranscribeEntryStage:
    def test_dalston_fetches_the_url_itself(self):
        queued = _reprocess(_episode(), "transcribe", config=_config(provider="dalston"))
        assert queued.starting_stage == TaskStage.TRANSCRIBE

    def test_downsampled_audio_still_there(self):
        config = _config(files=("downsampled/show/ep.wav",))
        queued = _reprocess(_episode(downsampled_audio_path="show/ep.wav"), "transcribe", config=config)
        assert queued.starting_stage == TaskStage.TRANSCRIBE

    def test_only_the_original_audio_left(self):
        config = _config(files=("original/show/ep.mp3",))
        episode = _episode(audio_path="show/ep.mp3", downsampled_audio_path="show/ep.wav")
        assert _reprocess(episode, "transcribe", config=config).starting_stage == TaskStage.DOWNSAMPLE

    def test_no_audio_left_downloads_again(self):
        episode = _episode(audio_path="show/ep.mp3", downsampled_audio_path="show/ep.wav")
        assert _reprocess(episode, "transcribe").starting_stage == TaskStage.DOWNLOAD

    def test_a_youtube_page_is_not_fetchable_by_dalston(self):
        episode = _episode(audio_url="https://www.youtube.com/watch?v=abc123")
        queued = _reprocess(episode, "transcribe", config=_config(provider="dalston"))
        assert queued.starting_stage == TaskStage.DOWNLOAD


class TestRefusals:
    def test_unknown_stage(self):
        with pytest.raises(ReprocessRejected) as rejected:
            _reprocess(_episode(), "download")
        assert rejected.value.kind == "invalid_stage"

    def test_unknown_episode(self):
        repository = MagicMock()
        repository.get_episode.return_value = None
        with pytest.raises(ReprocessRejected) as rejected:
            queue_reprocess(
                repository=repository, queue_manager=_queue(), config=_config(), episode_id="x", from_stage="clean"
            )
        assert rejected.value.kind == "not_found"

    @pytest.mark.parametrize(
        "from_stage, missing",
        [
            ("clean", "raw_transcript_path"),
            ("summarize", "clean_transcript_path"),
            ("entities", "clean_transcript_json_path"),
        ],
    )
    def test_missing_input(self, from_stage, missing):
        with pytest.raises(ReprocessRejected) as rejected:
            _reprocess(_episode(**{missing: None}), from_stage)
        assert rejected.value.kind == "missing_input"

    def test_no_audio_and_no_url(self):
        # ``audio_url`` is required by the model; rows written before that
        # (or by hand) can still lack it.
        episode = _episode().model_copy(update={"audio_url": None})
        with pytest.raises(ReprocessRejected) as rejected:
            _reprocess(episode, "transcribe")
        assert rejected.value.kind == "missing_input"

    def test_busy_episode_queues_nothing(self):
        # The busy check lives inside the queue's guarded insert, so the
        # service never calls the coalescing ``add_task``.
        queue = _queue(busy=True)
        with pytest.raises(ReprocessRejected) as rejected:
            _reprocess(_episode(), "clean", queue=queue)
        assert rejected.value.kind == "busy"
        queue.add_task.assert_not_called()

    def test_busy_episode_is_refused_on_a_dry_run_too(self):
        queue = _queue(busy=True)
        with pytest.raises(ReprocessRejected) as rejected:
            _reprocess(_episode(), "clean", queue=queue, dry_run=True)
        assert rejected.value.kind == "busy"
        queue.add_task_if_episode_idle.assert_not_called()


def test_dry_run_checks_everything_and_queues_nothing():
    queue = _queue()
    queued = _reprocess(_episode(), "summarize", queue=queue, dry_run=True)
    assert queued.task_id is None
    assert queued.starting_stage == TaskStage.SUMMARIZE
    queue.add_task_if_episode_idle.assert_not_called()


def test_queued_metadata_round_trips_through_the_helpers():
    queue = _queue()
    _reprocess(_episode(), "clean", queue=queue, refresh_episode_facts=True, requested_by="user-1")
    metadata = queue.add_task_if_episode_idle.call_args.kwargs["metadata"]
    options = reprocess_options(metadata)
    assert options["from_stage"] == "clean"
    assert options["refresh_episode_facts"] is True
    assert options["requested_by"] == "user-1"
    assert reprocess_requested_at(metadata).tzinfo is not None
    assert reprocess_options({"run_full_pipeline": True}) is None
