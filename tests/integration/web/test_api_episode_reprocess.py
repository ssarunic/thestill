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

"""Spec #106 — ``POST /api/episodes/{id}/reprocess`` and retry of a failed reprocess."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from thestill.core.queue_manager import TaskStage
from thestill.models.podcast import Episode, Podcast
from thestill.web.dependencies import AppState

PODCAST_ID = "00000000-0000-0000-0000-0000000000bb"
EPISODE_ID = "11111111-1111-1111-1111-1111111111bb"


def _seed_summarized_episode(app_state: AppState, **overrides) -> None:
    fields = dict(
        id=EPISODE_ID,
        external_id="ep-1",
        title="Done Ep",
        description="",
        pub_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        audio_url="https://example.com/ep1.mp3",
        duration=60,
        raw_transcript_path="feed/ep1_transcript.json",
        clean_transcript_path="feed/ep1_cleaned.md",
        clean_transcript_json_path="feed/ep1_cleaned.json",
        summary_path="feed/ep1_cleaned_summary.md",
    )
    fields.update(overrides)
    app_state.repository.save(
        Podcast(
            id=PODCAST_ID,
            rss_url="https://example.com/reprocess-feed.xml",
            title="Reprocess Feed",
            description="",
            episodes=[Episode(**fields)],
        )
    )


def _tasks(app_state: AppState):
    return app_state.queue_manager.get_tasks_for_episode(EPISODE_ID)


def test_reprocess_from_clean_queues_a_full_chain_tagged_as_reprocess(client: TestClient, app_state: AppState) -> None:
    _seed_summarized_episode(app_state)

    resp = client.post(
        f"/api/episodes/{EPISODE_ID}/reprocess", json={"from_stage": "clean", "refresh_episode_facts": True}
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["starting_stage"] == "clean"
    [task] = _tasks(app_state)
    assert task.stage == TaskStage.CLEAN
    assert task.metadata["run_full_pipeline"] is True
    assert task.metadata["initiated_by"] == "reprocess"
    assert "target_state" not in task.metadata  # runs through the entity branch
    assert task.metadata["reprocess"]["from_stage"] == "clean"
    assert task.metadata["reprocess"]["refresh_episode_facts"] is True
    assert task.metadata["reprocess"]["requested_at"]


@pytest.mark.parametrize(
    "from_stage, expected",
    [("summarize", TaskStage.SUMMARIZE), ("entities", TaskStage.EXTRACT_ENTITIES)],
)
def test_reprocess_starts_at_the_chosen_stage(
    client: TestClient, app_state: AppState, from_stage: str, expected: TaskStage
) -> None:
    _seed_summarized_episode(app_state)
    resp = client.post(f"/api/episodes/{EPISODE_ID}/reprocess", json={"from_stage": from_stage})
    assert resp.status_code == 200, resp.text
    assert [t.stage for t in _tasks(app_state)] == [expected]


def test_reprocess_from_transcribe_without_audio_downloads_first(client: TestClient, app_state: AppState) -> None:
    # The test config transcribes locally and the audio files are long gone.
    _seed_summarized_episode(app_state)
    resp = client.post(f"/api/episodes/{EPISODE_ID}/reprocess", json={"from_stage": "transcribe"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["starting_stage"] == "download"


def test_unknown_episode_is_404(client: TestClient) -> None:
    resp = client.post("/api/episodes/no-such-episode/reprocess", json={"from_stage": "clean"})
    assert resp.status_code == 404


def test_missing_input_is_400(client: TestClient, app_state: AppState) -> None:
    _seed_summarized_episode(app_state, clean_transcript_json_path=None)
    resp = client.post(f"/api/episodes/{EPISODE_ID}/reprocess", json={"from_stage": "entities"})
    assert resp.status_code == 400
    assert _tasks(app_state) == []


def test_unknown_stage_is_422(client: TestClient, app_state: AppState) -> None:
    _seed_summarized_episode(app_state)
    resp = client.post(f"/api/episodes/{EPISODE_ID}/reprocess", json={"from_stage": "download"})
    assert resp.status_code == 422


def test_any_active_task_on_the_episode_is_409(client: TestClient, app_state: AppState) -> None:
    _seed_summarized_episode(app_state)
    # A plain queued task in a different stage: merging into it would drop
    # the reprocess metadata, so the request is refused outright.
    app_state.queue_manager.add_task(EPISODE_ID, TaskStage.REINDEX)

    resp = client.post(f"/api/episodes/{EPISODE_ID}/reprocess", json={"from_stage": "clean"})

    assert resp.status_code == 409
    assert [t.stage for t in _tasks(app_state)] == [TaskStage.REINDEX]


def test_retry_of_a_failed_reprocess_stays_a_reprocess(client: TestClient, app_state: AppState) -> None:
    _seed_summarized_episode(app_state)
    client.post(f"/api/episodes/{EPISODE_ID}/reprocess", json={"from_stage": "clean"})
    [task] = _tasks(app_state)
    app_state.queue_manager.mark_dead(task.id, "LLM refused")
    app_state.repository.mark_episode_failed(
        episode_id=EPISODE_ID, failed_at_stage="clean", failure_reason="LLM refused", failure_type="fatal"
    )

    resp = client.post(f"/api/episodes/{EPISODE_ID}/retry")

    assert resp.status_code == 200, resp.text
    retried = next(t for t in _tasks(app_state) if t.id != task.id)
    assert retried.metadata["initiated_by"] == "episode-retry"
    assert retried.metadata["reprocess"] == task.metadata["reprocess"]


def test_retry_of_a_first_pass_failure_is_not_a_reprocess(client: TestClient, app_state: AppState) -> None:
    _seed_summarized_episode(app_state, clean_transcript_path=None, summary_path=None)
    app_state.queue_manager.add_task(EPISODE_ID, TaskStage.CLEAN, metadata={"run_full_pipeline": True})
    [task] = _tasks(app_state)
    app_state.queue_manager.mark_dead(task.id, "boom")
    app_state.repository.mark_episode_failed(
        episode_id=EPISODE_ID, failed_at_stage="clean", failure_reason="boom", failure_type="fatal"
    )

    resp = client.post(f"/api/episodes/{EPISODE_ID}/retry")

    assert resp.status_code == 200, resp.text
    retried = next(t for t in _tasks(app_state) if t.id != task.id)
    assert "reprocess" not in retried.metadata
