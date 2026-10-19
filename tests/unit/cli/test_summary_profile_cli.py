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

"""CliRunner tests for ``set-summary-profile`` and ``summary-profiles`` (spec #98)."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from thestill.cli import main
from thestill.models.podcast import Podcast
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository


@pytest.fixture
def repo(tmp_path, monkeypatch) -> SqlitePodcastRepository:
    """Tmp single-user SQLite install with two podcasts, wired into the CLI via env."""
    storage = tmp_path / "data"
    storage.mkdir()
    monkeypatch.setenv("STORAGE_PATH", str(storage))
    monkeypatch.setenv("THESTILL_ENV_FILE", str(tmp_path / ".no-such-env"))
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.delenv("SUMMARY_PROFILES_ENABLED", raising=False)
    repository = SqlitePodcastRepository(db_path=str(storage / "podcasts.db"))
    repository.save(
        Podcast(
            rss_url="https://example.com/crime.xml",
            title="Case Files",
            slug="case-files",
            description="",
            primary_category="True Crime",
        )
    )
    repository.save(
        Podcast(rss_url="https://example.com/tech.xml", title="AI Weekly", slug="ai-weekly", description="")
    )
    return repository


def _invoke(*args: str):
    return CliRunner().invoke(main, ["-q", *args])


def test_summary_profiles_lists_each_podcast_with_its_reason(repo):
    result = _invoke("summary-profiles")

    assert result.exit_code == 0, result.output
    assert "Available profiles: general" in result.output
    assert "AI Weekly: general (default)" in result.output
    assert "Case Files: general (category: True Crime → true_crime, not defined yet)" in result.output


def test_set_summary_profile_pins_and_clears_the_override(repo):
    result = _invoke("set-summary-profile", "--podcast-id", "case-files", "--profile", "general")

    assert result.exit_code == 0, result.output
    assert "✓ Case Files: summary profile general (override)." in result.output
    assert repo.get_by_url("https://example.com/crime.xml").summary_profile == "general"
    assert "Case Files: general (override)" in _invoke("summary-profiles").output

    result = _invoke("set-summary-profile", "--podcast-id", "case-files", "--profile", "auto")

    assert result.exit_code == 0, result.output
    assert repo.get_by_url("https://example.com/crime.xml").summary_profile is None
    assert "category: True Crime → true_crime, not defined yet" in result.output


def test_set_summary_profile_rejects_an_undefined_profile(repo):
    result = _invoke("set-summary-profile", "--podcast-id", "case-files", "--profile", "true_crime")

    assert result.exit_code == 2
    assert "Unknown profile 'true_crime'" in result.output
    assert repo.get_by_url("https://example.com/crime.xml").summary_profile is None


def test_set_summary_profile_unknown_podcast(repo):
    result = _invoke("set-summary-profile", "--podcast-id", "no-such-show", "--profile", "general")

    assert result.exit_code == 1
    assert "Podcast not found" in result.output
