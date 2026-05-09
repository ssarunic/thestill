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

"""Tests for the narrated-digest API endpoints (spec #33 Phase 3)."""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from thestill.models.user import User
from thestill.services.narration import NarrationRunnerError
from thestill.services.narration.models import NarrationContent, NarrationStats
from thestill.services.narration.narration_runner import NarrationRun
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
    state.config = MagicMock()
    state.config.narration_default_duration_seconds = 300
    state.narration_runner = MagicMock()
    return state


@pytest.fixture
def disabled_app_state(storage):
    state = MagicMock()
    state.path_manager = storage
    state.config = MagicMock()
    state.config.narration_default_duration_seconds = 300
    state.narration_runner = None
    return state


def _make_app(state, user):
    app = FastAPI()
    app.include_router(api_narrations.router, prefix="/api/narrations")
    app.dependency_overrides[api_narrations.get_app_state] = lambda: state
    app.dependency_overrides[api_narrations.require_auth] = lambda: user
    return app


@pytest.fixture
def client(mock_app_state, mock_user):
    return TestClient(_make_app(mock_app_state, mock_user))


@pytest.fixture
def disabled_client(disabled_app_state, mock_user):
    return TestClient(_make_app(disabled_app_state, mock_user))


def _narration_run(
    *,
    slug: str = "morning",
    digest_id: str = "digest-001",
    mode: str = "narrated",
    json_path: Path | None = None,
    markdown_path: Path | None = None,
    fallback_reason: str | None = None,
) -> NarrationRun:
    stats = NarrationStats(
        target_duration_seconds=300,
        actual_duration_seconds=292.0,
        narration_words=620,
        quote_seconds=72.0,
        episodes_covered=2,
        episodes_in_tail=1,
        quote_count=3,
        fallback_reason=fallback_reason,
    )
    content = NarrationContent(
        blocks=[],
        quotes=[],
        stats=stats,
        episode_ids_covered=["e1", "e2"],
        episode_ids_in_tail=["e3"],
        mode=mode,
        markdown="# briefing\n",
        generated_at=datetime(2026, 5, 8, 7, 0, tzinfo=timezone.utc),
        json_script_path=json_path,
        markdown_path=markdown_path,
    )
    return NarrationRun(digest_id=digest_id, slug=slug, content=content)


class TestCreateNarration:
    def test_returns_201_on_success(self, client, mock_app_state, storage):
        json_path = storage.narrations_dir() / "2026-05-08-morning.json"
        md_path = storage.narrations_dir() / "2026-05-08-morning.md"
        json_path.write_text("{}", encoding="utf-8")
        md_path.write_text("# briefing\n", encoding="utf-8")
        mock_app_state.narration_runner.run.return_value = _narration_run(
            json_path=json_path, markdown_path=md_path,
        )
        response = client.post("/api/narrations", json={"target_duration": 300})
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "ok"
        assert data["id"] == "2026-05-08-morning"
        assert data["mode"] == "narrated"
        assert data["digest_id"] == "digest-001"
        assert data["episodes_covered"] == ["e1", "e2"]
        assert data["script_path"].endswith("2026-05-08-morning.json")

    def test_returns_503_when_runner_disabled(self, disabled_client):
        response = disabled_client.post("/api/narrations", json={})
        assert response.status_code == 503

    def test_404_when_runner_raises(self, client, mock_app_state):
        mock_app_state.narration_runner.run.side_effect = NarrationRunnerError(
            "no digests found"
        )
        response = client.post("/api/narrations", json={"digest_id": "nope"})
        assert response.status_code == 404

    def test_resolves_target_duration_string(self, client, mock_app_state):
        mock_app_state.narration_runner.run.return_value = _narration_run()
        response = client.post(
            "/api/narrations", json={"target_duration": "short"}
        )
        assert response.status_code == 201
        kwargs = mock_app_state.narration_runner.run.call_args.kwargs
        assert kwargs["target_duration_seconds"] == 180

    def test_rejects_unparseable_duration_string(self, client):
        response = client.post(
            "/api/narrations", json={"target_duration": "abc"}
        )
        assert response.status_code == 400

    def test_rejects_non_positive_int_duration(self, client):
        response = client.post("/api/narrations", json={"target_duration": 0})
        assert response.status_code == 400

    def test_rejects_traversal_slug(self, client):
        response = client.post(
            "/api/narrations", json={"slug": "../etc/passwd"}
        )
        assert response.status_code == 422

    def test_falls_back_uses_default_when_duration_omitted(
        self, client, mock_app_state
    ):
        mock_app_state.narration_runner.run.return_value = _narration_run()
        response = client.post("/api/narrations", json={})
        assert response.status_code == 201
        kwargs = mock_app_state.narration_runner.run.call_args.kwargs
        assert kwargs["target_duration_seconds"] == 300


class TestGetNarration:
    def test_reads_artefacts_from_disk(self, client, storage):
        narration_id = "2026-05-08-morning"
        narrations_dir = storage.narrations_dir()
        (narrations_dir / f"{narration_id}.json").write_text(
            json.dumps({"schema_version": "phase2", "blocks": []}),
            encoding="utf-8",
        )
        (narrations_dir / f"{narration_id}.md").write_text("# Briefing\n", encoding="utf-8")
        response = client.get(f"/api/narrations/{narration_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == narration_id
        assert data["script"]["schema_version"] == "phase2"
        assert data["markdown"].startswith("# Briefing")

    def test_returns_null_markdown_when_only_json_exists(self, client, storage):
        narration_id = "fallback-only"
        (storage.narrations_dir() / f"{narration_id}.json").write_text(
            json.dumps({"mode": "fallback"}),
            encoding="utf-8",
        )
        response = client.get(f"/api/narrations/{narration_id}")
        assert response.status_code == 200
        assert response.json()["markdown"] is None

    def test_404_when_missing(self, client):
        response = client.get("/api/narrations/2099-12-31-missing")
        assert response.status_code == 404


class TestGetNarrationScript:
    def test_returns_script_json(self, client, storage):
        narration_id = "tts-target"
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
