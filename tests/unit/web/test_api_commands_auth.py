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

"""Auth gating for /api/commands.

Manual pipeline triggers and queue/DLQ surgery are operator-only
(``admin_router`` + ``require_admin``); status reads and ``/add`` are
plain-auth. The assertions focus on the gate itself: 401 for anonymous,
403 for non-admin, and anything-but-401/403 once the gate passes (handler
bodies run against a bare MagicMock state and may 4xx/5xx — that is fine,
the gate has already let the request through).
"""

import pytest

from thestill.web.routes import api_commands

from .auth_harness import ADMIN_USER, PLAIN_USER, auth_client

ROUTERS = [(api_commands.router, "/api/commands"), (api_commands.admin_router, "/api/commands")]

QUEUE_BODY = {"podcast_slug": "pod", "episode_slug": "ep"}

# Every admin-only endpoint on the commands surface.
ADMIN_ENDPOINTS = [
    ("POST", "/api/commands/refresh", {}),
    ("POST", "/api/commands/download", QUEUE_BODY),
    ("POST", "/api/commands/downsample", QUEUE_BODY),
    ("POST", "/api/commands/transcribe", QUEUE_BODY),
    ("POST", "/api/commands/clean", QUEUE_BODY),
    ("POST", "/api/commands/summarize", QUEUE_BODY),
    ("POST", "/api/commands/run-pipeline", QUEUE_BODY),
    ("POST", "/api/commands/episode/ep-1/cancel-pipeline", None),
    ("GET", "/api/commands/queue/status", None),
    ("GET", "/api/commands/queue/tasks", None),
    ("POST", "/api/commands/queue/task/task-1/bump", None),
    ("POST", "/api/commands/queue/task/task-1/cancel", None),
    ("GET", "/api/commands/dlq", None),
    ("POST", "/api/commands/dlq/task-1/retry", None),
    ("POST", "/api/commands/dlq/task-1/skip", None),
    ("POST", "/api/commands/dlq/retry-all", None),
]

# Plain-auth endpoints: any session suffices, admin not required.
PLAIN_AUTH_ENDPOINTS = [
    ("POST", "/api/commands/add", {"url": "https://example.com/feed.rss"}),
    ("GET", "/api/commands/refresh/status", None),
    ("GET", "/api/commands/status", None),
    ("GET", "/api/commands/add/status", None),
    ("GET", "/api/commands/task/task-1", None),
    ("GET", "/api/commands/episode/ep-1/tasks", None),
    ("GET", "/api/commands/task/task-1/progress/current", None),
]


def _request(client, method, path, body):
    return client.request(method, path, json=body)


class TestAdminEndpoints:
    @pytest.mark.parametrize("method,path,body", ADMIN_ENDPOINTS)
    def test_anonymous_gets_401(self, method, path, body):
        client, _ = auth_client(ROUTERS, multi_user=True, current_user=None)
        assert _request(client, method, path, body).status_code == 401

    @pytest.mark.parametrize("method,path,body", ADMIN_ENDPOINTS)
    def test_non_admin_gets_403(self, method, path, body):
        client, _ = auth_client(ROUTERS, multi_user=True, current_user=PLAIN_USER)
        assert _request(client, method, path, body).status_code == 403

    @pytest.mark.parametrize("method,path,body", ADMIN_ENDPOINTS)
    def test_admin_passes_gate(self, method, path, body):
        client, _ = auth_client(ROUTERS, multi_user=True, current_user=ADMIN_USER)
        assert _request(client, method, path, body).status_code not in (401, 403)

    @pytest.mark.parametrize("method,path,body", ADMIN_ENDPOINTS)
    def test_single_user_mode_passes_gate(self, method, path, body):
        # In single-user mode the local user is the operator regardless of is_admin.
        client, _ = auth_client(ROUTERS, multi_user=False, current_user=PLAIN_USER)
        assert _request(client, method, path, body).status_code not in (401, 403)


class TestPlainAuthEndpoints:
    @pytest.mark.parametrize("method,path,body", PLAIN_AUTH_ENDPOINTS)
    def test_anonymous_gets_401(self, method, path, body):
        client, _ = auth_client(ROUTERS, multi_user=True, current_user=None)
        assert _request(client, method, path, body).status_code == 401

    @pytest.mark.parametrize("method,path,body", PLAIN_AUTH_ENDPOINTS)
    def test_non_admin_passes_gate(self, method, path, body):
        # Regression guard: these must not accidentally widen to require_admin.
        client, _ = auth_client(ROUTERS, multi_user=True, current_user=PLAIN_USER)
        assert _request(client, method, path, body).status_code not in (401, 403)
