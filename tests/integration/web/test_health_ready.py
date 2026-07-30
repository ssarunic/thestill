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

"""``/health`` stays a zero-dependency liveness check; ``/health/ready``
(spec #66) does one DB round-trip and maps failure to a non-leaky 503."""

from __future__ import annotations

from thestill.web.services.health_service import HealthService


def test_health_liveness_unchanged(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_ready_returns_200_with_working_database(client):
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_ready_returns_503_when_database_ping_fails(client, monkeypatch):
    def boom(self):
        raise RuntimeError("connection to postgresql://user:secret@db failed")

    monkeypatch.setattr(HealthService, "ping_database", boom)
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "unready"
    # Unauthenticated endpoint: the failure detail (DSN, driver internals)
    # must go to the log, never the response body.
    assert "secret" not in response.text
    assert "postgresql" not in response.text
