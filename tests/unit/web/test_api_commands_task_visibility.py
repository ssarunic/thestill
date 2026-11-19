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

"""Who may read a task's status (/api/commands).

In-memory command tasks (add podcast, refresh) belong to the user who
started them: only that user or an admin can read them. Pipeline queue
tasks have no single initiator (one task processes an episode for every
follower), so they are readable by an admin or by a user who has the
episode in their library: following its podcast, or holding it in the
inbox. Everyone else gets the same 404 as an unknown id, so ids cannot be
probed.
"""

from types import SimpleNamespace

import pytest

from thestill.core.queue_manager import Task, TaskStage, TaskStatus
from thestill.web.routes import api_commands
from thestill.web.task_manager import TaskManager, TaskType

from .auth_harness import ADMIN_USER, PLAIN_USER, auth_client

ROUTERS = [(api_commands.router, "/api/commands"), (api_commands.admin_router, "/api/commands")]

OTHER_USER_ID = "user-other"
EPISODE_ID = "ep-1"
PODCAST_ID = "pod-1"


def _client(current_user, *, follows=False, inbox=False, task=None):
    client, state = auth_client(ROUTERS, multi_user=True, current_user=current_user)
    state.task_manager = TaskManager()
    state.repository.get_episode.return_value = SimpleNamespace(id=EPISODE_ID, podcast_id=PODCAST_ID)
    state.follower_service.is_following.return_value = follows
    state.inbox_service.get_entry.return_value = object() if inbox else None
    state.queue_manager.get_task.return_value = task
    state.queue_manager.get_tasks_for_episode.return_value = [task] if task else []
    state.progress_store.get.return_value = None
    return client, state


def _episode_task():
    return Task(id="task-1", episode_id=EPISODE_ID, stage=TaskStage.TRANSCRIBE, status=TaskStatus.PROCESSING)


def _feed_task():
    return Task(id="task-2", podcast_id=PODCAST_ID, stage=TaskStage.REFRESH_FEED, status=TaskStatus.PENDING)


class TestAddPodcastStatus:
    def test_another_users_add_is_not_found_by_id(self):
        client, state = _client(PLAIN_USER)
        task = state.task_manager.start_task(TaskType.ADD_PODCAST, user_id=OTHER_USER_ID)
        resp = client.get("/api/commands/add/status", params={"task_id": task.task_id})
        assert resp.status_code == 404

    def test_own_add_is_readable_by_id(self):
        client, state = _client(PLAIN_USER)
        task = state.task_manager.start_task(TaskType.ADD_PODCAST, user_id=PLAIN_USER.id)
        resp = client.get("/api/commands/add/status", params={"task_id": task.task_id})
        assert resp.status_code == 200
        assert resp.json()["status"] == "running"

    def test_admin_reads_any_add_by_id(self):
        client, state = _client(ADMIN_USER)
        task = state.task_manager.start_task(TaskType.ADD_PODCAST, user_id=OTHER_USER_ID)
        assert client.get("/api/commands/add/status", params={"task_id": task.task_id}).status_code == 200

    def test_without_id_returns_the_callers_latest_add_not_a_newer_one(self):
        client, state = _client(PLAIN_USER)
        mine = state.task_manager.start_task(TaskType.ADD_PODCAST, "mine", user_id=PLAIN_USER.id)
        state.task_manager.complete_task(TaskType.ADD_PODCAST, message="my add finished")
        state.task_manager.start_task(TaskType.ADD_PODCAST, "theirs", user_id=OTHER_USER_ID)
        body = client.get("/api/commands/add/status").json()
        assert body["message"] == "my add finished"
        assert mine.task_id  # the caller's own run

    def test_without_id_and_no_own_add_reports_none(self):
        client, state = _client(PLAIN_USER)
        state.task_manager.start_task(TaskType.ADD_PODCAST, user_id=OTHER_USER_ID)
        assert client.get("/api/commands/add/status").json()["status"] == "none"


class TestRefreshAndAllStatus:
    def test_non_admin_does_not_see_the_refresh(self):
        client, state = _client(PLAIN_USER)
        state.task_manager.start_task(TaskType.REFRESH, user_id=ADMIN_USER.id)
        assert client.get("/api/commands/refresh/status").json()["status"] == "none"

    def test_admin_sees_the_refresh(self):
        client, state = _client(ADMIN_USER)
        state.task_manager.start_task(TaskType.REFRESH, user_id=ADMIN_USER.id)
        assert client.get("/api/commands/refresh/status").json()["status"] == "running"

    def test_all_status_lists_only_the_callers_tasks(self):
        client, state = _client(PLAIN_USER)
        state.task_manager.start_task(TaskType.REFRESH, user_id=ADMIN_USER.id)
        state.task_manager.start_task(TaskType.ADD_PODCAST, user_id=OTHER_USER_ID)
        assert client.get("/api/commands/status").json()["tasks"] == {}

    def test_all_status_admin_sees_everything(self):
        client, state = _client(ADMIN_USER)
        state.task_manager.start_task(TaskType.REFRESH, user_id=ADMIN_USER.id)
        state.task_manager.start_task(TaskType.ADD_PODCAST, user_id=OTHER_USER_ID)
        assert set(client.get("/api/commands/status").json()["tasks"]) == {"refresh", "add_podcast"}


QUEUE_TASK_PATHS = [
    "/api/commands/task/task-1",
    "/api/commands/task/task-1/progress/current",
]
EPISODE_TASKS_PATH = f"/api/commands/episode/{EPISODE_ID}/tasks"


class TestQueueTasks:
    @pytest.mark.parametrize("path", QUEUE_TASK_PATHS + ["/api/commands/task/task-1/progress"])
    def test_stranger_gets_404(self, path):
        client, _ = _client(PLAIN_USER, task=_episode_task())
        assert client.get(path).status_code == 404

    def test_stranger_sees_an_empty_episode_task_list(self):
        # 200 + [] rather than 404: the reader polls this for every viewer.
        client, _ = _client(PLAIN_USER, task=_episode_task())
        resp = client.get(EPISODE_TASKS_PATH)
        assert resp.status_code == 200
        assert resp.json()["tasks"] == []

    @pytest.mark.parametrize(
        "follows,inbox,user", [(True, False, PLAIN_USER), (False, True, PLAIN_USER), (False, False, ADMIN_USER)]
    )
    def test_library_holder_or_admin_sees_episode_tasks(self, follows, inbox, user):
        client, _ = _client(user, follows=follows, inbox=inbox, task=_episode_task())
        assert [t["id"] for t in client.get(EPISODE_TASKS_PATH).json()["tasks"]] == ["task-1"]

    @pytest.mark.parametrize("path", QUEUE_TASK_PATHS)
    def test_follower_can_read(self, path):
        client, _ = _client(PLAIN_USER, follows=True, task=_episode_task())
        assert client.get(path).status_code == 200

    @pytest.mark.parametrize("path", QUEUE_TASK_PATHS)
    def test_inbox_holder_can_read(self, path):
        client, _ = _client(PLAIN_USER, inbox=True, task=_episode_task())
        assert client.get(path).status_code == 200

    @pytest.mark.parametrize("path", QUEUE_TASK_PATHS)
    def test_admin_can_read(self, path):
        client, _ = _client(ADMIN_USER, task=_episode_task())
        assert client.get(path).status_code == 200

    def test_feed_task_needs_a_follow(self):
        client, _ = _client(PLAIN_USER, task=_feed_task())
        assert client.get("/api/commands/task/task-2").status_code == 404
        client, _ = _client(PLAIN_USER, follows=True, task=_feed_task())
        assert client.get("/api/commands/task/task-2").status_code == 200

    def test_single_user_mode_reads_everything(self):
        client, state = auth_client(ROUTERS, multi_user=False, current_user=PLAIN_USER)
        state.queue_manager.get_task.return_value = _episode_task()
        state.follower_service.is_following.return_value = False
        state.inbox_service.get_entry.return_value = None
        assert client.get("/api/commands/task/task-1").status_code == 200
