"""Spec #116 — sync tracks in the download and transcribe handlers.

Production transcribes through Dalston's URL fetch, where Thestill never
holds the audio. The track must then be cut from the original Dalston
stored for the job, and a transcript must never keep a track cut from
other bytes. A sync track problem must never fail transcription.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tests.unit.core.sync_track_fixtures import (
    EPISODE_ID,
    PODCAST_ID,
    chunks,
    frames,
    make_sqlite_sync_track_repo,
    seconds,
    stream,
)
from thestill.core.queue_manager import Task, TaskStage, TaskStatus
from thestill.core.sync_track import align
from thestill.core.task_handlers import handle_download, handle_transcribe
from thestill.models.transcript import Segment, Transcript
from thestill.repositories.sync_track_repository import STATE_PENDING
from thestill.services.sync_track_service import SyncTrackService
from thestill.utils.file_storage.local import LocalFileStorage
from thestill.utils.path_manager import PathManager

JOB_ID = "0b2f6a54-3c1d-4e6f-9a70-5d2c8e1f4b3a"
AUDIO = stream(frames(1, seconds(60)))
OTHER_AUDIO = stream(frames(2, seconds(45)))


def _task(stage: TaskStage) -> Task:
    return Task(id=str(uuid.uuid4()), episode_id=EPISODE_ID, stage=stage, status=TaskStatus.PROCESSING)


def _transcript(job_id=JOB_ID, end: float = 55.0) -> Transcript:
    return Transcript(
        audio_file="the-show/ep",
        language="en",
        text="hello world",
        segments=[Segment(id=0, start=0.0, end=end, text="hello world")],
        processing_time=1.0,
        model_used="dalston",
        timestamp=0.0,
        provider_metadata={"provider": "dalston", "job_id": job_id} if job_id else None,
    )


@pytest.fixture
def env(tmp_path):
    path_manager = PathManager(str(tmp_path))
    config = SimpleNamespace(
        transcription_provider="dalston",
        path_manager=path_manager,
        file_storage=LocalFileStorage(base_path=str(tmp_path)),
        delete_audio_after_processing=False,
        sync_tracks_enabled=True,
        dalston_base_url="http://dalston.test",
        dalston_api_key="key",
        max_audio_bytes=10**9,
    )
    repo = make_sqlite_sync_track_repo(tmp_path)
    service = SyncTrackService(config, repo)

    episode = MagicMock()
    episode.id = EPISODE_ID
    episode.slug = "ep"
    episode.title = "Some Episode"
    episode.external_id = "ext-1"
    episode.audio_url = "https://example.com/audio.mp3"
    episode.downsampled_audio_path = None
    episode.duration = None
    podcast = MagicMock()
    podcast.id = PODCAST_ID
    podcast.slug = "the-show"
    podcast.rss_url = "https://example.com/rss"
    podcast.language = "en"

    state = MagicMock()
    state.config = config
    state.path_manager = path_manager
    state.repository.get_episode.return_value = (podcast, episode)
    state.sync_track_service = service
    return SimpleNamespace(state=state, service=service, repo=repo, root=tmp_path, config=config)


def _transcribe(env, transcript, job_audio):
    """Run handle_transcribe on the Dalston URL path with a stubbed provider."""
    transcriber = MagicMock()
    transcriber.transcribe_audio.return_value = transcript
    with (
        patch("thestill.core.task_handlers.create_transcriber", return_value=transcriber),
        patch("thestill.core.task_handlers.iter_job_audio", side_effect=job_audio) as fetch,
    ):
        handle_transcribe(_task(TaskStage.TRANSCRIBE), env.state)
    return fetch


def test_dalston_path_cuts_the_track_from_the_jobs_stored_audio(env):
    fetch = _transcribe(env, _transcript(), lambda *a, **k: chunks(AUDIO))

    fetch.assert_called_once()
    assert fetch.call_args.args[0] == JOB_ID
    assert fetch.call_args.kwargs["base_url"] == "http://dalston.test"
    current = env.service.current(EPISODE_ID)
    assert current is not None and current.source == "dalston"
    assert current.audio_bytes == len(AUDIO)
    # The published track really describes that audio.
    result = align(env.service.load(current), chunks(AUDIO))
    assert result.coverage > 0.95 and result.spans[0].offset_s == pytest.approx(0, abs=0.01)
    env.state.feed_manager.mark_episode_processed.assert_called_once()


def test_re_transcription_replaces_the_track(env):
    _transcribe(env, _transcript(), lambda *a, **k: chunks(AUDIO))
    first = env.service.current(EPISODE_ID)
    # A reprocess: drop the stored transcript so the provider is called again.
    for path in (env.root / "raw_transcripts").rglob("*.json"):
        path.unlink()

    _transcribe(env, _transcript(job_id=str(uuid.uuid4()), end=40.0), lambda *a, **k: chunks(OTHER_AUDIO))

    second = env.service.current(EPISODE_ID)
    assert second.generation != first.generation
    assert second.audio_bytes == len(OTHER_AUDIO)
    assert not (env.root / first.path).exists()


def test_failed_audio_fetch_does_not_fail_transcription_or_keep_the_old_track(env):
    _transcribe(env, _transcript(), lambda *a, **k: chunks(AUDIO))
    old = env.service.current(EPISODE_ID)
    for path in (env.root / "raw_transcripts").rglob("*.json"):
        path.unlink()

    def purged(*args, **kwargs):
        raise ConnectionError("410 audio purged")

    _transcribe(env, _transcript(job_id=str(uuid.uuid4())), purged)

    # Transcription succeeded twice ...
    assert env.state.feed_manager.mark_episode_processed.call_count == 2
    # ... and the new transcript is not paired with the old audio's track.
    assert env.service.current(EPISODE_ID) is None
    assert not (env.root / old.path).exists()


def test_pending_track_from_an_earlier_download_is_not_published_on_the_dalston_path(env):
    env.service.build_pending(
        episode_id=EPISODE_ID,
        podcast_id=PODCAST_ID,
        podcast_slug="the-show",
        episode_slug="ep",
        chunks=chunks(OTHER_AUDIO),
        source="local",
    )

    _transcribe(env, _transcript(job_id=None), lambda *a, **k: chunks(AUDIO))

    # No job id in the transcript, so no track could be cut from Dalston's
    # copy; the leftover local track must not be used in its place.
    assert env.service.current(EPISODE_ID) is None
    assert env.repo.get(EPISODE_ID, STATE_PENDING) is None


def test_state_without_the_service_transcribes_as_before(env):
    env.state.sync_track_service = None
    fetch = _transcribe(env, _transcript(), lambda *a, **k: chunks(AUDIO))
    fetch.assert_not_called()
    env.state.feed_manager.mark_episode_processed.assert_called_once()


def test_download_leaves_a_pending_track_that_readers_cannot_see(env):
    class _Downloader:
        def __init__(self, work_dir, **kwargs):
            self.work_dir = Path(work_dir)

        def download_episode(self, episode, podcast):
            target = self.work_dir / "the-show" / "ep.mp3"
            target.parent.mkdir(parents=True)
            target.write_bytes(AUDIO)
            return "the-show/ep.mp3"

    with (
        patch("thestill.core.task_handlers.AudioDownloader", _Downloader),
        patch("thestill.utils.duration.get_audio_duration", return_value=60),
    ):
        handle_download(_task(TaskStage.DOWNLOAD), env.state)

    pending = env.repo.get(EPISODE_ID, STATE_PENDING)
    assert pending is not None and pending.source == "local"
    assert pending.audio_bytes == len(AUDIO)
    assert env.service.current(EPISODE_ID) is None
    env.state.feed_manager.mark_episode_downloaded.assert_called_once()
