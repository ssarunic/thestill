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

"""Auth gating for /api/entities.

The review queue and corrections endpoints mutate/expose global entity
resolution state (a correction re-resolves mentions for every user), so
they are admin-only. The per-episode entity reads stay plain-auth via
the router-level require_auth applied at registration.
"""

import pytest

from thestill.web.routes import api_entities

from .auth_harness import ADMIN_USER, PLAIN_USER, auth_client

ROUTERS = [(api_entities.router, "/api")]

ADMIN_ENDPOINTS = [
    ("GET", "/api/entities/review-queue", None),
    ("POST", "/api/entities/corrections", {"action": "blacklist", "surface_form": "Acme", "wrong_qid": "Q1"}),
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
