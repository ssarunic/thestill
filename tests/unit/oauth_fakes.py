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

"""A stand-in for authlib's AsyncOAuth2Client in ``AuthService`` tests."""

from types import SimpleNamespace

from thestill.services import auth_service as auth_module


def install_fake_google(monkeypatch, *, sub: str = "g-1", email: str = "user@example.com", name: str = "User"):
    """Make ``handle_google_callback`` see one Google identity, with no
    network. Call again to switch identity."""
    userinfo = {"sub": sub, "email": email, "name": name, "picture": None}

    class FakeGoogleOAuthClient:
        def __init__(self, **_):
            self.token = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def fetch_token(self, *_, **__):
            return {"access_token": "t"}

        async def get(self, _url):
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: dict(userinfo))

    monkeypatch.setattr(auth_module, "AsyncOAuth2Client", FakeGoogleOAuthClient)
