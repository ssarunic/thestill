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

"""Tests for the narrated-digest artefact GET endpoints (spec #33 Phase 3).

The user-visible POST trigger lives at
``POST /api/digests/{digest_id}/narrate`` (see test_api_digests.py); the
endpoints here are for direct artefact access by TTS consumers.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from thestill.models.user import User
from thestill.utils.path_manager import PathManager
from thestill.web.routes import api_narrations


@pytest.fixture
def mock_user():
    return User(id="user-1", email="alice@example.com", name="Alice")


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
def client(mock_app_state, mock_user):
    app = FastAPI()
    app.include_router(api_narrations.router, prefix="/api/narrations")
    app.dependency_overrides[api_narrations.get_app_state] = lambda: mock_app_state
    app.dependency_overrides[api_narrations.require_auth] = lambda: mock_user
    return TestClient(app)


class TestGetNarration:
    def test_reads_artefacts_from_disk(self, client, storage):
        narration_id = "digest-001-medium"
        narrations_dir = storage.narrations_dir()
        (narrations_dir / f"{narration_id}.json").write_text(
            json.dumps({"schema_version": "phase2", "blocks": []}),
            encoding="utf-8",
        )
        (narrations_dir / f"{narration_id}.md").write_text(
            "# Briefing\n", encoding="utf-8",
        )
        response = client.get(f"/api/narrations/{narration_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == narration_id
        assert data["script"]["schema_version"] == "phase2"
        assert data["markdown"].startswith("# Briefing")

    def test_returns_null_markdown_when_only_json_exists(self, client, storage):
        narration_id = "digest-002-medium"
        (storage.narrations_dir() / f"{narration_id}.json").write_text(
            json.dumps({"mode": "fallback"}), encoding="utf-8",
        )
        response = client.get(f"/api/narrations/{narration_id}")
        assert response.status_code == 200
        assert response.json()["markdown"] is None

    def test_404_when_missing(self, client):
        response = client.get("/api/narrations/digest-missing-medium")
        assert response.status_code == 404


class TestGetNarrationScript:
    def test_returns_script_json(self, client, storage):
        narration_id = "digest-tts-target"
        payload = {"schema_version": "phase2", "blocks": [{"kind": "narration"}]}
        (storage.narrations_dir() / f"{narration_id}.json").write_text(
            json.dumps(payload), encoding="utf-8",
        )
        response = client.get(f"/api/narrations/{narration_id}/script.json")
        assert response.status_code == 200
        data = response.json()
        assert data["schema_version"] == "phase2"
        assert data["blocks"][0]["kind"] == "narration"

    def test_404_when_missing(self, client):
        response = client.get("/api/narrations/missing/script.json")
        assert response.status_code == 404
