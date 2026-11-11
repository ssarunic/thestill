"""Spec #116 — SyncTrackService: a transcript is only ever paired with a
track cut from the same audio, and nothing here can fail a pipeline stage."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.unit.core.sync_track_fixtures import (
    EPISODE_ID,
    FRAME_SECONDS,
    PODCAST_ID,
    RecordingLogger,
    chunks,
    frames,
    make_sqlite_sync_track_repo,
    seconds,
    stream,
)
from thestill.repositories.sync_track_repository import STATE_PENDING
from thestill.services.sync_track_service import SyncTrackService
from thestill.utils.file_storage.local import LocalFileStorage
from thestill.utils.path_manager import PathManager


@pytest.fixture
def env(tmp_path, monkeypatch):
    log = RecordingLogger()
    monkeypatch.setattr("thestill.services.sync_track_service.logger", log)
    config = SimpleNamespace(
        path_manager=PathManager(str(tmp_path)),
        file_storage=LocalFileStorage(base_path=str(tmp_path)),
        sync_tracks_enabled=True,
    )
    repo = make_sqlite_sync_track_repo(tmp_path)
    return SimpleNamespace(config=config, repo=repo, service=SyncTrackService(config, repo), root=tmp_path, log=log)


def _build(service, audio: bytes, source: str = "local"):
    return service.build_pending(
        episode_id=EPISODE_ID,
        podcast_id=PODCAST_ID,
        podcast_slug="the-show",
        episode_slug="ep",
        chunks=chunks(audio),
        source=source,
    )


AUDIO_60 = stream(frames(1, seconds(60)))
AUDIO_90 = stream(frames(2, seconds(90)))


def test_build_then_publish_makes_the_track_current(env):
    record = _build(env.service, AUDIO_60)
    assert env.service.current(EPISODE_ID) is None  # pending is invisible

    published = env.service.publish(episode_id=EPISODE_ID, transcribed_duration_s=60, transcript_end_s=58.2)

    assert published.generation == record.generation
    assert published.path == f"sync_tracks/the-show/ep.{record.generation}.sync"
    track = env.service.load(published)
    assert track.generation == record.generation
    assert track.duration_s == pytest.approx(seconds(60) * FRAME_SECONDS)

    written = env.log.named("sync_track_written")[0]
    assert written["episode_id"] == EPISODE_ID and written["source"] == "local"
    assert written["anchors_kept"] == len(track.anchors)
    assert written["audio_bytes"] == len(AUDIO_60)
    switched = env.log.named("sync_track_switched")[0]
    assert switched["old_generation"] is None and switched["new_generation"] == record.generation


def test_new_transcript_replaces_the_track_and_deletes_the_old_file(env):
    first = _build(env.service, AUDIO_60)
    env.service.publish(episode_id=EPISODE_ID)
    second = _build(env.service, AUDIO_90)
    assert env.service.current(EPISODE_ID).generation == first.generation  # until published

    env.service.publish(episode_id=EPISODE_ID)

    assert env.service.current(EPISODE_ID).generation == second.generation
    assert not (env.root / first.path).exists()
    assert (env.root / second.path).exists()


def test_track_of_other_audio_is_never_published(env):
    """The pending track is 60 s; the transcribed audio was 90 s."""
    old = _build(env.service, AUDIO_90)
    env.service.publish(episode_id=EPISODE_ID)
    stale = _build(env.service, AUDIO_60)

    result = env.service.publish(episode_id=EPISODE_ID, transcribed_duration_s=90)

    assert result is None
    mismatch = env.log.named("sync_track_transcript_mismatch")[0]
    assert mismatch["log_level"] == "error"
    assert mismatch["delta_s"] == pytest.approx(-30, abs=0.5)
    # Neither the mismatched track nor the previous transcript's track survives.
    assert env.service.current(EPISODE_ID) is None
    assert env.repo.get(EPISODE_ID, STATE_PENDING) is None
    assert not (env.root / stale.path).exists()
    assert not (env.root / old.path).exists()


def test_transcript_running_past_the_audio_is_a_mismatch(env):
    _build(env.service, AUDIO_60)
    assert env.service.publish(episode_id=EPISODE_ID, transcript_end_s=75.0) is None
    assert env.log.named("sync_track_transcript_mismatch")
    assert env.service.current(EPISODE_ID) is None


def test_transcript_ending_before_the_audio_is_fine(env):
    """An episode that ends on music has its last word well before the end."""
    _build(env.service, AUDIO_60)
    assert env.service.publish(episode_id=EPISODE_ID, transcript_end_s=31.0) is not None


def test_publish_without_pending_retires_the_previous_track(env):
    old = _build(env.service, AUDIO_60)
    env.service.publish(episode_id=EPISODE_ID)

    assert env.service.publish(episode_id=EPISODE_ID) is None

    assert env.service.current(EPISODE_ID) is None
    assert not (env.root / old.path).exists()


def test_non_mp3_audio_writes_nothing(env):
    assert _build(env.service, bytes(range(256)) * 2000) is None
    assert env.log.named("sync_track_failed")[0]["reason"] == "unsupported_codec"
    assert env.repo.get(EPISODE_ID, STATE_PENDING) is None
    assert not (env.root / "sync_tracks" / "the-show").exists()


def test_disabled_flag_writes_nothing(env):
    env.config.sync_tracks_enabled = False
    assert _build(env.service, AUDIO_60) is None
    assert env.repo.get(EPISODE_ID, STATE_PENDING) is None


def test_failures_are_logged_and_never_raised(env):
    def broken():
        yield AUDIO_60[:5000]
        raise ConnectionError("stream died")

    result = env.service.build_pending(
        episode_id=EPISODE_ID,
        podcast_id=PODCAST_ID,
        podcast_slug="the-show",
        episode_slug="ep",
        chunks=broken(),
        source="dalston",
    )
    assert result is None
    failed = env.log.named("sync_track_failed")[0]
    assert failed["reason"] == "error" and "stream died" in failed["error"]

    env.config.file_storage = None  # publish and discard hit a broken dependency
    env.repo.get = None
    assert env.service.publish(episode_id=EPISODE_ID) is None
    env.service.discard_pending(EPISODE_ID)


def test_discard_pending_removes_row_and_file(env):
    record = _build(env.service, AUDIO_60)
    env.service.discard_pending(EPISODE_ID)
    assert env.repo.get(EPISODE_ID, STATE_PENDING) is None
    assert not (env.root / record.path).exists()
