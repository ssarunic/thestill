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

"""Spec #66 config knobs: the MIGRATE_ON_STARTUP getter and the friendly
fail-fast when DATABASE_URL is set without the [postgres] extra installed."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from thestill.utils.config import is_migrate_on_startup_enabled, load_config


class TestMigrateOnStartupFlag:
    def test_default_is_off(self, monkeypatch):
        monkeypatch.delenv("MIGRATE_ON_STARTUP", raising=False)
        assert is_migrate_on_startup_enabled() is False

    @pytest.mark.parametrize("value", ["true", "1", "yes", "on", "TRUE"])
    def test_truthy_values_enable(self, monkeypatch, value):
        monkeypatch.setenv("MIGRATE_ON_STARTUP", value)
        assert is_migrate_on_startup_enabled() is True

    @pytest.mark.parametrize("value", ["false", "0", "", "banana"])
    def test_other_values_stay_off(self, monkeypatch, value):
        monkeypatch.setenv("MIGRATE_ON_STARTUP", value)
        assert is_migrate_on_startup_enabled() is False


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """Isolate load_config from the developer's real .env (same trick as
    tests/unit/security/test_app_hardening.py::isolated_env)."""
    empty_env = tmp_path / ".env"
    empty_env.touch()
    monkeypatch.setenv("THESTILL_ENV_FILE", str(empty_env))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    return monkeypatch


class TestPsycopgFriendlyError:
    def test_database_url_without_psycopg_raises_actionable_error(self, clean_env):
        clean_env.setenv("DATABASE_URL", "postgresql://x:y@localhost:5432/thestill")
        # None in sys.modules makes ``import psycopg`` raise ImportError
        # even when the package is installed.
        with patch.dict("sys.modules", {"psycopg": None}):
            with pytest.raises(ValueError, match=r"thestill\[postgres\]"):
                load_config()

    def test_database_url_with_psycopg_passes(self, clean_env):
        pytest.importorskip("psycopg")
        clean_env.setenv("DATABASE_URL", "postgresql://x:y@localhost:5432/thestill")
        config = load_config()
        assert config.database_url == "postgresql://x:y@localhost:5432/thestill"

    def test_no_database_url_never_imports_psycopg(self, clean_env):
        # SQLite installs must not require psycopg (spec #44 contract).
        with patch.dict("sys.modules", {"psycopg": None}):
            config = load_config()
        assert config.database_url == ""
