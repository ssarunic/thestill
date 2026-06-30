"""Regression: ``handle_transcribe`` resumes from a persisted transcript.

The transcript artifact is written to durable storage *before* the DB row is
updated. A transient failure in that DB write (observed in production as
``database is locked``) used to force a full re-transcription on retry — for a
long episode that meant re-running a ~20-minute Dalston job, which then tripped
network timeouts and exhausted the retry budget, failing an episode whose
transcript was already on disk.

These tests pin the idempotent-resume contract:

- a valid existing artifact → skip the provider call, still persist the DB row
- no artifact → transcribe and write it
- a corrupt/partial artifact → fall through to a fresh transcription
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from thestill.core.queue_manager import Task, TaskStage, TaskStatus
from thestill.core.task_handlers import handle_transcribe
from thestill.models.transcript import Transcript


def _make_task(episode_id: str = "ep-1") -> Task:
    return Task(
        id=str(uuid.uuid4()),
        episode_id=episode_id,
        stage=TaskStage.TRANSCRIBE,
        status=TaskStatus.PROCESSING,
    )


def _transcript(text: str = "hello world") -> Transcript:
    return Transcript(
        audio_file="the-show/ep_transcript.json",
        language="en",
        text=text,
        segments=[],
        processing_time=1.0,
        model_used="dalston",
        timestamp=0.0,
    )


class _FakeStorage:
    """In-memory FileStorage stand-in keyed by relative path."""

    def __init__(self, files: dict[str, str] | None = None):
        self.files: dict[str, str] = dict(files or {})

    def exists(self, path: str) -> bool:
        return path in self.files

    def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
        return self.files[path]

    def write_text(self, path: str, content: str, *, encoding: str = "utf-8") -> None:
        self.files[path] = content


def _build_state(storage: _FakeStorage):
    """Wire a minimal AppState for the Dalston URL-fetch path.

    ``audio_url`` truthy + no ``downsampled_audio_path`` selects
    ``use_dalston_url``, which skips all local-audio handling.
    """
    episode = MagicMock()
    episode.id = "ep-1"
    episode.slug = "ep"
    episode.title = "Some Episode"
    episode.external_id = "ext-1"
    episode.audio_url = "https://example.com/audio.mp3"
    episode.downsampled_audio_path = None

    podcast = MagicMock()
    podcast.slug = "the-show"
    podcast.rss_url = "https://example.com/rss"
    podcast.language = "en"

    path_manager = MagicMock()
    path_manager.raw_transcripts_dir.return_value = Path("/data/raw_transcripts")
    path_manager.to_relative.side_effect = lambda p: str(p)

    config = MagicMock()
    config.transcription_provider = "dalston"
    config.path_manager = path_manager
    config.file_storage = storage
    config.delete_audio_after_processing = False

    state = MagicMock()
    state.config = config
    state.repository.get_episode.return_value = (podcast, episode)
    return state


# The deterministic relative path the handler computes for this fixture.
_REL_PATH = str(Path("/data/raw_transcripts") / "the-show" / "ep_transcript.json")


def test_reuses_valid_existing_artifact_without_re_transcribing():
    storage = _FakeStorage({_REL_PATH: _transcript("already done").model_dump_json()})
    state = _build_state(storage)

    with patch("thestill.core.task_handlers.create_transcriber") as create:
        handle_transcribe(_make_task(), state)

    # The expensive provider path is never touched.
    create.assert_not_called()
    # But the DB row is still persisted (this is the step that originally failed).
    state.feed_manager.mark_episode_processed.assert_called_once()
    _, kwargs = state.feed_manager.mark_episode_processed.call_args
    assert kwargs["raw_transcript_path"] == "the-show/ep_transcript.json"


def test_transcribes_when_no_artifact_exists():
    storage = _FakeStorage()  # empty
    state = _build_state(storage)

    transcriber = MagicMock()
    transcriber.transcribe_audio.return_value = _transcript("fresh")

    with patch("thestill.core.task_handlers.create_transcriber", return_value=transcriber):
        handle_transcribe(_make_task(), state)

    transcriber.transcribe_audio.assert_called_once()
    # Artifact written, then DB row persisted.
    assert _REL_PATH in storage.files
    state.feed_manager.mark_episode_processed.assert_called_once()


def test_corrupt_artifact_falls_through_to_re_transcription():
    storage = _FakeStorage({_REL_PATH: "{ not valid transcript json"})
    state = _build_state(storage)

    transcriber = MagicMock()
    transcriber.transcribe_audio.return_value = _transcript("recovered")

    with patch("thestill.core.task_handlers.create_transcriber", return_value=transcriber):
        handle_transcribe(_make_task(), state)

    transcriber.transcribe_audio.assert_called_once()
    # The bad file is overwritten with a valid transcript.
    assert Transcript.model_validate_json(storage.files[_REL_PATH]).text == "recovered"
    state.feed_manager.mark_episode_processed.assert_called_once()
