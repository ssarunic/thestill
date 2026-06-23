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

"""Retrying a failed episode must CONTINUE the full pipeline.

Regression: ``retry_failed_episode`` re-queued the failed stage with no
metadata, so ``run_full_pipeline`` was absent and the chain stopped the moment
the retried stage succeeded (e.g. a retried transcribe never advanced to
clean). The retry must carry ``run_full_pipeline=True``.
"""

from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

from thestill.models.podcast import Episode, Podcast
from thestill.web.dependencies import AppState

PODCAST_ID = "00000000-0000-0000-0000-0000000000aa"
EPISODE_ID = "11111111-1111-1111-1111-1111111111aa"


def _seed_failed_episode(app_state: AppState, failed_stage: str = "transcribe") -> None:
    app_state.repository.save(
        Podcast(
            id=PODCAST_ID,
            rss_url="https://example.com/retry-feed.xml",
            title="Retry Feed",
            description="",
            episodes=[
                Episode(
                    id=EPISODE_ID,
                    external_id="ep-1",
                    title="Failed Ep",
                    description="",
                    pub_date=datetime(2026, 1, 1),
                    audio_url="https://example.com/ep1.mp3",
                    duration=60,
                )
            ],
        )
    )
    app_state.repository.mark_episode_failed(
        episode_id=EPISODE_ID,
        failed_at_stage=failed_stage,
        failure_reason="dalston down",
        failure_type="transient",
    )


def test_retry_failed_episode_continues_full_pipeline(client: TestClient, app_state: AppState) -> None:
    _seed_failed_episode(app_state, failed_stage="transcribe")

    resp = client.post(f"/api/episodes/{EPISODE_ID}/retry")
    assert resp.status_code == 200, resp.text
    assert resp.json()["stage"] == "transcribe"

    tasks = app_state.queue_manager.get_tasks_for_episode(EPISODE_ID)
    transcribe_tasks = [t for t in tasks if t.stage.value == "transcribe"]
    assert len(transcribe_tasks) == 1
    # The crux: the retried stage must carry run_full_pipeline so it chains
    # transcribe -> clean -> summarize rather than stopping after transcribe.
    assert transcribe_tasks[0].metadata.get("run_full_pipeline") is True
