"""``POST …/drift-outcome`` (spec #116 Phase 2): the browser's alignment report."""

import json

import pytest

from tests.integration.web.test_api_transcript_words import (
    _build_annotated_transcript,
    _build_raw_transcript,
    _seed_episode,
)
from tests.unit.core.sync_track_fixtures import RecordingLogger, chunks, frames, seconds, stream
from thestill.repositories.sqlite_sync_track_repository import SqliteSyncTrackRepository
from thestill.services.sync_track_service import SyncTrackService
from thestill.web.routes import api_podcasts

EPISODE_ID = "22222222-2222-2222-2222-222222222222"


class RecordingSink:
    enabled = True
    browser_key = None

    def __init__(self):
        self.events = []

    def capture(self, event, *, user=None, visitor_key=None, gpc=False):
        self.events.append((event, user))

    def shutdown(self):
        pass


@pytest.fixture
def log(monkeypatch):
    recorder = RecordingLogger()
    monkeypatch.setattr(api_podcasts, "logger", recorder)
    return recorder


@pytest.fixture
def episode(app_state, tmp_path):
    """A seeded episode with a published sync track; returns (url, generation)."""
    service = SyncTrackService(app_state.config, SqliteSyncTrackRepository(str(app_state.repository.db_path)))
    app_state.sync_track_service = service
    podcast, slug = _seed_episode(
        app_state=app_state,
        tmp_path=tmp_path,
        raw_transcript=_build_raw_transcript(),
        annotated_transcript=_build_annotated_transcript(episode_id=EPISODE_ID),
    )
    service.build_pending(
        episode_id=EPISODE_ID,
        podcast_id="11111111-1111-1111-1111-111111111111",
        podcast_slug="test-podcast",
        episode_slug="test-episode",
        chunks=chunks(stream(frames(1, seconds(30)))),
        source="local",
    )
    generation = service.publish(episode_id=EPISODE_ID).generation
    return f"/api/podcasts/{podcast}/episodes/{slug}/drift-outcome", generation


def _aligned(generation, **overrides):
    body = {
        "generation": generation,
        "outcome": "aligned",
        "spans": [
            {"src_start": 0.0, "src_end": 2623.0, "offset": 0.0},
            {"src_start": 2688.7, "src_end": 3282.0, "offset": 40.124},
        ],
        "coverage": 0.97,
        "anchors_total": 3372,
        "anchors_hit": 3301,
        "anchors_inlier": 3299,
        "anchors_outlier": 2,
        "verify_length_ok": True,
        "verify_ranges_checked": 4,
        "verify_ranges_matched": 4,
        "audio_host": "stitcher2.acast.com",
        "audio_bytes": 54000000,
        "fetched_bytes": 54720000,
        "fetch_ms": 2100,
        "fit_ms": 35,
        "browser": "Safari 27",
    }
    body.update(overrides)
    return body


def test_report_is_logged_with_the_episode_and_no_listener(client, app_state, episode, log):
    url, generation = episode

    response = client.post(url, json=_aligned(generation))

    assert response.status_code == 200
    assert response.json()["accepted"] is True
    (event,) = log.named("drift_map_reported")
    assert event["log_level"] == "info"
    assert event["episode_id"] == EPISODE_ID
    assert event["generation"] == generation
    assert event["generation_current"] is True
    assert event["outcome"] == "aligned"
    assert event["span_count"] == 2
    assert event["max_offset_s"] == 40.124
    assert event["spans"][1] == {"src_start": 2688.7, "src_end": 3282.0, "offset": 40.124}
    assert event["audio_host"] == "stitcher2.acast.com"
    assert not {"user_id", "email", "ip", "client_ip", "audio_url"} & set(event)


def test_summary_goes_to_usage_analytics_without_the_episode(client, app_state, episode, log):
    url, generation = episode
    app_state.analytics_sink = sink = RecordingSink()

    assert client.post(url, json=_aligned(generation)).status_code == 200

    ((event, _user),) = sink.events
    props = event.properties()
    assert event.name == "drift_alignment_result"
    assert props["outcome"] == "aligned"
    assert props["span_count"] == 2
    assert props["max_offset_s"] == 40.1
    assert props["audio_host"] == "stitcher2.acast.com"
    assert props["browser"] == "Safari 27"
    assert not {"episode_id", "podcast_id", "generation", "spans"} & set(props)


def test_report_for_a_superseded_generation_is_kept_and_marked(client, episode, log):
    url, _generation = episode

    assert client.post(url, json={"generation": "an-older-one", "outcome": "no_cors"}).status_code == 200

    (event,) = log.named("drift_map_reported")
    assert event["generation_current"] is False
    assert event["outcome"] == "no_cors"
    assert event["span_count"] == 0 and event["max_offset_s"] == 0


def test_applied_map_that_failed_a_range_check_is_an_error(client, episode, log):
    url, generation = episode

    assert client.post(url, json=_aligned(generation, verify_ranges_matched=3)).status_code == 200

    (event,) = log.named("drift_map_reported")
    assert event["log_level"] == "error"


@pytest.mark.parametrize(
    "change",
    [
        {"outcome": "made_up"},
        {"audio_host": "https://host.example/episode.mp3?token=secret"},
        {"audio_host": "host.example:8443"},
        {"browser": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
        {"coverage": 1.5},
        {"spans": [{"src_start": 0, "src_end": 1, "offset": 1e9}]},
        {"spans": [{"src_start": 0, "src_end": 1, "offset": 0, "note": "x"}]},
        {"spans": [{"src_start": 0, "src_end": 1, "offset": 0}] * 65},
        {"generation": "../../etc/passwd"},
        {"user_email": "someone@example.com"},
        {"error": "<script>alert(1)</script>"},
    ],
)
def test_malformed_reports_are_rejected(client, episode, log, change):
    url, generation = episode

    assert client.post(url, json={**_aligned(generation), **change}).status_code == 422
    assert log.named("drift_map_reported") == []


def test_report_over_the_size_cap_is_refused_before_parsing(client, episode, log):
    url, generation = episode
    body = json.dumps(_aligned(generation, error="x" * 5000))

    response = client.post(url, content=body, headers={"content-type": "application/json"})

    assert response.status_code == 413
    assert log.named("drift_map_reported") == []


def test_oversized_body_is_refused_before_it_is_read_as_json(client, episode, log):
    """The cap sits in front of body parsing: malformed JSON over the limit
    is a 413, not the 422 a parsed body would give."""
    url, _generation = episode

    response = client.post(url, content="{" + "x" * 5000, headers={"content-type": "application/json"})

    assert response.status_code == 413
    assert client.post(url, content="{not json", headers={"content-type": "application/json"}).status_code == 422


def test_report_without_a_declared_length_is_refused(client, episode, log):
    url, generation = episode

    def chunks():
        yield json.dumps(_aligned(generation)).encode()

    response = client.post(url, content=chunks(), headers={"content-type": "application/json"})

    assert response.status_code == 411
    assert log.named("drift_map_reported") == []


def test_unknown_episode_is_not_found(client, log):
    response = client.post(
        "/api/podcasts/nope/episodes/nope/drift-outcome", json={"generation": "g", "outcome": "error"}
    )

    assert response.status_code == 404


def test_report_carries_the_play_check(client, app_state, episode, log):
    """Spec #116 §4 step 6 — the verify fields and why a check is missing."""
    url, generation = episode
    app_state.analytics_sink = sink = RecordingSink()

    verified = {**_aligned(generation), "sw_state": "ok"}
    unchecked = {
        **_aligned(generation),
        "sw_state": "element_not_cors",
        "verify_length_ok": None,
        "verify_ranges_checked": None,
        "verify_ranges_matched": None,
    }
    assert client.post(url, json=verified).status_code == 200
    assert client.post(url, json=unchecked).status_code == 200
    assert client.post(url, json={**verified, "sw_state": "made_up"}).status_code == 422

    first, second = log.named("drift_map_reported")
    assert (first["sw_state"], first["verify_ranges_matched"]) == ("ok", 4)
    assert (second["sw_state"], second["verify_ranges_checked"]) == ("element_not_cors", None)
    assert [event.properties()["sw_state"] for event, _ in sink.events] == ["ok", "element_not_cors"]
    assert sink.events[0][0].properties()["verify_ranges_matched"] == 4


def test_a_map_reported_for_another_copy_is_not_an_error(client, episode, log):
    """The browser found the element playing a different copy and said so."""
    url, generation = episode
    body = {**_aligned(generation), "outcome": "variant_mismatch", "verify_ranges_matched": 1, "sw_state": "ok"}

    assert client.post(url, json=body).status_code == 200

    (event,) = log.named("drift_map_reported")
    assert event["log_level"] == "info"


def test_a_later_report_of_the_same_play_is_marked(client, app_state, episode, log):
    """A seek exposed another copy after the play had been reported as aligned."""
    url, generation = episode
    app_state.analytics_sink = sink = RecordingSink()

    assert client.post(url, json=_aligned(generation)).status_code == 200
    later = {**_aligned(generation), "outcome": "variant_mismatch", "verify_ranges_matched": 3, "revision": 1}
    assert client.post(url, json=later).status_code == 200
    assert client.post(url, json={**later, "revision": 99}).status_code == 422

    assert [event["revision"] for event in log.named("drift_map_reported")] == [0, 1]
    assert [event.properties()["revision"] for event, _ in sink.events] == [0, 1]
