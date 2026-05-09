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

"""Tests for the narration dashboard tile aggregator (spec #33 Phase 5)."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from thestill.utils.path_manager import PathManager
from thestill.web.routes import api_dashboard


@pytest.fixture
def storage(tmp_path: Path) -> PathManager:
    data_root = tmp_path / "data"
    data_root.mkdir()
    pm = PathManager(storage_path=str(data_root))
    pm.ensure_directories_exist()
    return pm


@pytest.fixture
def mock_app_state(storage):
    state = MagicMock()
    state.path_manager = storage
    return state


@pytest.fixture
def client(mock_app_state):
    app = FastAPI()
    app.include_router(api_dashboard.router, prefix="/api/dashboard")
    app.dependency_overrides[api_dashboard.get_app_state] = lambda: mock_app_state
    return TestClient(app)


def _write_narration(
    storage: PathManager,
    *,
    narration_id: str,
    mode: str = "narrated",
    target_seconds: int = 300,
    actual_seconds: float = 290.0,
    latency_ms: int | None = 4000,
    generated_at: str = "2026-05-08T07:00:00+00:00",
    fallback_reason: str | None = None,
    digest_id: str | None = None,
) -> None:
    payload: dict = {
        "schema_version": "phase2",
        "mode": mode,
        "target_duration_seconds": target_seconds,
        "actual_duration_seconds": actual_seconds,
        "latency_ms": latency_ms,
        "generated_at": generated_at,
        "fallback_reason": fallback_reason,
        "digest_id": digest_id,
        "blocks": [],
    }
    (storage.narrations_dir() / f"{narration_id}.json").write_text(
        json.dumps(payload), encoding="utf-8",
    )


def test_returns_zeros_when_no_narrations(client):
    response = client.get("/api/dashboard/narration")
    assert response.status_code == 200
    data = response.json()
    assert data["total_runs"] == 0
    assert data["fallback_count"] == 0
    assert data["fallback_rate"] == 0.0
    assert data["avg_actual_duration_seconds"] is None
    assert data["latest"] is None


def test_aggregates_runs_across_variants(client, storage):
    _write_narration(
        storage, narration_id="d1-short", target_seconds=180,
        actual_seconds=170.0, latency_ms=3000,
        generated_at="2026-05-08T06:00:00+00:00",
    )
    _write_narration(
        storage, narration_id="d1-medium", target_seconds=300,
        actual_seconds=290.0, latency_ms=4000,
        generated_at="2026-05-08T07:00:00+00:00",
    )
    _write_narration(
        storage, narration_id="d2-medium", target_seconds=300,
        actual_seconds=280.0, latency_ms=5000,
        generated_at="2026-05-08T08:00:00+00:00",
    )
    response = client.get("/api/dashboard/narration")
    assert response.status_code == 200
    data = response.json()
    assert data["total_runs"] == 3
    assert data["fallback_count"] == 0
    assert data["fallback_rate"] == 0.0
    # Averages: actual = (170+290+280)/3 = 246.67; target = (180+300+300)/3 = 260
    assert abs(data["avg_actual_duration_seconds"] - 246.67) < 0.01
    assert abs(data["avg_target_duration_seconds"] - 260.0) < 0.01
    assert data["avg_latency_ms"] == 4000
    # Latest = highest generated_at = d2-medium.
    assert data["latest"]["narration_id"] == "d2-medium"


def test_fallback_rate_reflects_mode_field(client, storage):
    _write_narration(storage, narration_id="d1-medium", mode="narrated")
    _write_narration(
        storage, narration_id="d2-medium", mode="fallback",
        fallback_reason="word_budget_high",
        generated_at="2026-05-09T07:00:00+00:00",
    )
    response = client.get("/api/dashboard/narration")
    data = response.json()
    assert data["total_runs"] == 2
    assert data["fallback_count"] == 1
    assert data["fallback_rate"] == 0.5
    assert data["latest"]["mode"] == "fallback"
    assert data["latest"]["fallback_reason"] == "word_budget_high"


def test_skips_corrupt_json(client, storage):
    _write_narration(storage, narration_id="d1-medium")
    (storage.narrations_dir() / "d1-broken.json").write_text(
        "{not valid", encoding="utf-8",
    )
    response = client.get("/api/dashboard/narration")
    data = response.json()
    assert data["total_runs"] == 1


def test_handles_missing_latency_field(client, storage):
    _write_narration(storage, narration_id="d1-medium", latency_ms=None)
    response = client.get("/api/dashboard/narration")
    data = response.json()
    assert data["total_runs"] == 1
    # Average is None when no run has latency captured.
    assert data["avg_latency_ms"] is None


def test_latest_surfaces_digest_id_from_header(client, storage):
    """Phase 5 hardening: the API surfaces ``digest_id`` straight from
    the JSON header so the frontend doesn't parse the filename. Slugs
    can contain hyphens (``custom-450s``) so filename parsing is
    ambiguous; reading the persisted field is correct.
    """
    _write_narration(
        storage,
        narration_id="abc-custom-450s",
        digest_id="abc",
    )
    response = client.get("/api/dashboard/narration")
    data = response.json()
    assert data["latest"]["digest_id"] == "abc"
    assert data["latest"]["narration_id"] == "abc-custom-450s"


def test_latest_digest_id_is_null_for_legacy_artefacts(client, storage):
    """Older artefacts written before the runner persisted ``digest_id``
    surface ``None`` so the tile can hide its deep-link without
    inventing a fragile filename split.
    """
    _write_narration(
        storage,
        narration_id="legacy-medium",
        digest_id=None,
    )
    response = client.get("/api/dashboard/narration")
    data = response.json()
    assert data["latest"]["digest_id"] is None
