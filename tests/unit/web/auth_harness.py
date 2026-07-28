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

"""Shared harness for auth-gating tests.

Mirrors ``app.py``'s default-deny registration: every ``/api`` router is
mounted with a router-level ``require_auth``. Unlike the per-file fixtures
that override ``require_auth`` with a stub user, this harness leaves the
real ``require_auth``/``require_admin`` logic in place and drives it
through a mocked ``auth_service``, so the 401/403 branches are actually
exercised.
"""

from unittest.mock import MagicMock

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from thestill.models.user import User
from thestill.web.dependencies import get_app_state, require_auth

ADMIN_USER = User(id="user-admin", email="admin@example.com", name="Admin", is_admin=True)
PLAIN_USER = User(id="user-plain", email="plain@example.com", name="Plain", is_admin=False)


def auth_state(*, multi_user: bool, current_user: User | None) -> MagicMock:
    """AppState mock whose auth_service resolves every token to ``current_user``."""
    state = MagicMock()
    state.config.multi_user = multi_user
    state.auth_service.get_current_user.return_value = current_user
    return state


def auth_client(routers, *, multi_user: bool, current_user: User | None):
    """Build a TestClient mounted the way production mounts routers.

    Args:
        routers: iterable of ``(router, prefix)`` pairs, all mounted with a
            router-level ``require_auth`` exactly like ``app.py``.
        multi_user: value for ``state.config.multi_user``.
        current_user: what the mocked auth_service resolves tokens to
            (``None`` simulates an unauthenticated request).

    Returns:
        ``(client, state)`` — server exceptions are returned as 500s so
        gate assertions (401/403 vs anything else) don't need deep mocks
        for every handler body.
    """
    app = FastAPI()
    for router, prefix in routers:
        app.include_router(router, prefix=prefix, dependencies=[Depends(require_auth)])
    state = auth_state(multi_user=multi_user, current_user=current_user)
    app.dependency_overrides[get_app_state] = lambda: state
    return TestClient(app, raise_server_exceptions=False), state
