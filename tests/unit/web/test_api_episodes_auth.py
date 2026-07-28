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

"""Auth gating for /api/episodes.

``bulk/process`` and ``{id}/retry`` mutate the pipeline and are
admin-only; the list/failure reads require any authenticated session
(via the router-level require_auth applied at registration).
"""

import pytest

from thestill.web.routes import api_episodes

from .auth_harness import ADMIN_USER, PLAIN_USER, auth_client

ROUTERS = [(api_episodes.router, "/api/episodes")]

ADMIN_ENDPOINTS = [
    ("POST", "/api/episodes/bulk/process", {"episode_ids": ["ep-1"]}),
    ("POST", "/api/episodes/ep-1/retry", None),
]

READ_ENDPOINTS = [
    ("GET", "/api/episodes", None),
    ("GET", "/api/episodes/failed", None),
    ("GET", "/api/episodes/ep-1/failure", None),
]


class TestAdminEndpoints:
    @pytest.mark.parametrize("method,path,body", ADMIN_ENDPOINTS)
    def test_anonymous_gets_401(self, method, path, body):
        client, _ = auth_client(ROUTERS, multi_user=True, current_user=None)
        assert client.request(method, path, json=body).status_code == 401

    @pytest.mark.parametrize("method,path,body", ADMIN_ENDPOINTS)
    def test_non_admin_gets_403(self, method, path, body):
        client, _ = auth_client(ROUTERS, multi_user=True, current_user=PLAIN_USER)
        assert client.request(method, path, json=body).status_code == 403

    @pytest.mark.parametrize("method,path,body", ADMIN_ENDPOINTS)
    def test_admin_passes_gate(self, method, path, body):
        client, _ = auth_client(ROUTERS, multi_user=True, current_user=ADMIN_USER)
        assert client.request(method, path, json=body).status_code not in (401, 403)

    @pytest.mark.parametrize("method,path,body", ADMIN_ENDPOINTS)
    def test_single_user_mode_passes_gate(self, method, path, body):
        client, _ = auth_client(ROUTERS, multi_user=False, current_user=PLAIN_USER)
        assert client.request(method, path, json=body).status_code not in (401, 403)


class TestReadEndpoints:
    @pytest.mark.parametrize("method,path,body", READ_ENDPOINTS)
    def test_anonymous_gets_401(self, method, path, body):
        client, _ = auth_client(ROUTERS, multi_user=True, current_user=None)
        assert client.request(method, path, json=body).status_code == 401

    @pytest.mark.parametrize("method,path,body", READ_ENDPOINTS)
    def test_non_admin_passes_gate(self, method, path, body):
        client, _ = auth_client(ROUTERS, multi_user=True, current_user=PLAIN_USER)
        assert client.request(method, path, json=body).status_code not in (401, 403)
