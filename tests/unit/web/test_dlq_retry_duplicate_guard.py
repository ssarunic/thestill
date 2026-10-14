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

"""``POST /dlq/{id}/retry`` refuses a row whose episode stage is already queued.

An episode that failed several times has one DLQ row per attempt. Once one is
retried (or the episode's own Retry started a fresh run), retrying another must
say so with a 409 instead of queueing the same work again.
"""

from thestill.core.queue_manager import Task, TaskStage, TaskStatus
from thestill.web.routes import api_commands

from .auth_harness import ADMIN_USER, auth_client

ROUTERS = [(api_commands.router, "/api/commands"), (api_commands.admin_router, "/api/commands")]


def _dead_task() -> Task:
    return Task(id="task-1", episode_id="ep-1", stage=TaskStage.TRANSCRIBE, status=TaskStatus.DEAD)


def test_retry_is_409_while_the_stage_is_already_queued():
    client, state = auth_client(ROUTERS, multi_user=True, current_user=ADMIN_USER)
    state.queue_manager.get_task.return_value = _dead_task()
    state.queue_manager.has_pending_task.return_value = True

    resp = client.post("/api/commands/dlq/task-1/retry")

    assert resp.status_code == 409
    assert "already queued" in resp.json()["detail"]
    state.queue_manager.has_pending_task.assert_called_once_with("ep-1", TaskStage.TRANSCRIBE)
    state.queue_manager.retry_dead_task.assert_not_called()
    state.repository.clear_episode_failure.assert_not_called()


def test_retry_goes_through_when_the_stage_is_free():
    client, state = auth_client(ROUTERS, multi_user=True, current_user=ADMIN_USER)
    state.queue_manager.get_task.return_value = _dead_task()
    state.queue_manager.has_pending_task.return_value = False
    state.queue_manager.retry_dead_task.return_value = Task(
        id="task-1", episode_id="ep-1", stage=TaskStage.TRANSCRIBE, status=TaskStatus.PENDING
    )

    resp = client.post("/api/commands/dlq/task-1/retry")

    assert resp.status_code == 200
    assert resp.json()["new_status"] == "pending"
    state.queue_manager.retry_dead_task.assert_called_once_with("task-1")
    state.repository.clear_episode_failure.assert_called_once_with("ep-1")
