"""HTTP-level tests for the spec #116 sync track surface.

- ``GET /api/podcasts/{slug}/episodes/{slug}/sync-track/{generation}`` serves
  the binary track, only for the episode's current generation.
- The transcript payload names a sync track only when the cleaned transcript
  was stamped with the generation that is still current.
"""

from __future__ import annotations

import pytest

from tests.integration.web.test_api_transcript_words import (
    _build_annotated_transcript,
    _build_raw_transcript,
    _seed_episode,
)
from tests.unit.core.sync_track_fixtures import chunks, frames, seconds, stream
from thestill.core.sync_track import SyncTrack
from thestill.repositories.sqlite_sync_track_repository import SqliteSyncTrackRepository
from thestill.services.sync_track_service import SyncTrackService

EPISODE_ID = "22222222-2222-2222-2222-222222222222"
AUDIO = stream(frames(1, seconds(30)))


@pytest.fixture
def sync_tracks(app_state) -> SyncTrackService:
    service = SyncTrackService(app_state.config, SqliteSyncTrackRepository(str(app_state.repository.db_path)))
    app_state.sync_track_service = service
    return service


def _seed(app_state, tmp_path, *, stamp):
    annotated = _build_annotated_transcript(episode_id=EPISODE_ID)
    annotated["sync_track_generation"] = stamp
    return _seed_episode(
        app_state=app_state, tmp_path=tmp_path, raw_transcript=_build_raw_transcript(), annotated_transcript=annotated
    )


def _publish(service: SyncTrackService, audio: bytes = AUDIO) -> str:
    service.build_pending(
        episode_id=EPISODE_ID,
        podcast_id="11111111-1111-1111-1111-111111111111",
        podcast_slug="test-podcast",
        episode_slug="test-episode",
        chunks=chunks(audio),
        source="local",
    )
    return service.publish(episode_id=EPISODE_ID).generation


def test_serves_the_current_track_as_an_immutable_binary(client, app_state, tmp_path, sync_tracks):
    podcast, episode = _seed(app_state, tmp_path, stamp=None)
    generation = _publish(sync_tracks)

    response = client.get(f"/api/podcasts/{podcast}/episodes/{episode}/sync-track/{generation}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["cache-control"] == "private, max-age=31536000, immutable"
    track = SyncTrack.from_bytes(response.content)
    assert track.generation == generation
    assert track.audio_bytes == len(AUDIO)


def test_superseded_and_unknown_generations_are_not_found(client, app_state, tmp_path, sync_tracks):
    podcast, episode = _seed(app_state, tmp_path, stamp=None)
    old = _publish(sync_tracks)
    new = _publish(sync_tracks, stream(frames(2, seconds(30))))
    base = f"/api/podcasts/{podcast}/episodes/{episode}/sync-track"

    assert client.get(f"{base}/{old}").status_code == 404
    assert client.get(f"{base}/not-a-generation").status_code == 404
    assert client.get(f"{base}/{new}").status_code == 200


def test_pending_track_is_not_served(client, app_state, tmp_path, sync_tracks):
    podcast, episode = _seed(app_state, tmp_path, stamp=None)
    pending = sync_tracks.build_pending(
        episode_id=EPISODE_ID,
        podcast_id=None,
        podcast_slug="test-podcast",
        episode_slug="test-episode",
        chunks=chunks(AUDIO),
        source="local",
    )
    response = client.get(f"/api/podcasts/{podcast}/episodes/{episode}/sync-track/{pending.generation}")
    assert response.status_code == 404


def test_unknown_episode_and_missing_service_are_not_found(client, app_state, tmp_path):
    assert client.get("/api/podcasts/nope/episodes/nope/sync-track/abc").status_code == 404
    podcast, episode = _seed(app_state, tmp_path, stamp="abc")
    assert client.get(f"/api/podcasts/{podcast}/episodes/{episode}/sync-track/abc").status_code == 404


def _transcript_generation(client, podcast, episode):
    body = client.get(f"/api/podcasts/{podcast}/episodes/{episode}/transcript").json()
    return body["segments"]["sync_track_generation"]


def test_transcript_names_the_track_it_was_stamped_with(client, app_state, tmp_path, sync_tracks):
    # The clean stage stamps the generation that was current when it ran;
    # here the stamp is written into the fixture after publishing.
    podcast, episode = _seed(app_state, tmp_path, stamp="placeholder")
    generation = _publish(sync_tracks)
    sidecar = app_state.path_manager.clean_transcripts_dir() / "test-episode_annotated.json"
    sidecar.write_text(sidecar.read_text().replace("placeholder", generation), encoding="utf-8")

    assert _transcript_generation(client, podcast, episode) == generation


def test_transcript_names_no_track_between_re_transcribe_and_re_clean(client, app_state, tmp_path, sync_tracks):
    """The visible cleaned transcript still carries the old stamp while the
    episode's current track already belongs to the new raw transcript."""
    podcast, episode = _seed(app_state, tmp_path, stamp="placeholder")
    old = _publish(sync_tracks)
    sidecar = app_state.path_manager.clean_transcripts_dir() / "test-episode_annotated.json"
    sidecar.write_text(sidecar.read_text().replace("placeholder", old), encoding="utf-8")
    _publish(sync_tracks, stream(frames(2, seconds(30))))  # re-transcribed

    assert _transcript_generation(client, podcast, episode) is None


def test_unstamped_transcript_and_missing_service_name_no_track(client, app_state, tmp_path, sync_tracks):
    podcast, episode = _seed(app_state, tmp_path, stamp=None)
    _publish(sync_tracks)
    assert _transcript_generation(client, podcast, episode) is None

    app_state.sync_track_service = None
    assert _transcript_generation(client, podcast, episode) is None
