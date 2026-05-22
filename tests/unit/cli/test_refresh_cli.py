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

"""CliRunner tests for ``thestill refresh`` error surfacing (spec #42, FM-4).

A refresh where feeds errored must be loud and exit non-zero so cron/CI
treats a silent-fleet event as a failure instead of a clean run.
"""

import pytest
from click.testing import CliRunner

from thestill.cli import main
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.services.refresh_service import RefreshResult


@pytest.fixture
def tmp_cli_env(tmp_path, monkeypatch):
    """Point the CLI at a fresh tmp DB so context construction succeeds."""
    storage = tmp_path / "data"
    storage.mkdir()
    monkeypatch.setenv("STORAGE_PATH", str(storage))
    monkeypatch.setenv("THESTILL_ENV_FILE", str(tmp_path / ".no-such-env"))
    SqlitePodcastRepository(db_path=str(storage / "podcasts.db"))
    return storage


def _fake_refresh_service(result):
    class _FakeRefreshService:
        def __init__(self, *args, **kwargs):
            pass

        def refresh(self, **kwargs):
            return result

    return _FakeRefreshService


def test_refresh_exits_nonzero_when_feeds_errored(tmp_cli_env, monkeypatch):
    result = RefreshResult(total_episodes=0, episodes_by_podcast=[], podcasts_with_errors=2)
    monkeypatch.setattr("thestill.cli.RefreshService", _fake_refresh_service(result))

    res = CliRunner().invoke(main, ["refresh"])

    assert res.exit_code == 1
    assert "2 feed(s) errored" in res.output


def test_refresh_exits_zero_when_clean(tmp_cli_env, monkeypatch):
    result = RefreshResult(total_episodes=0, episodes_by_podcast=[], podcasts_with_errors=0)
    monkeypatch.setattr("thestill.cli.RefreshService", _fake_refresh_service(result))

    res = CliRunner().invoke(main, ["refresh"])

    assert res.exit_code == 0
    assert "errored during refresh" not in res.output
