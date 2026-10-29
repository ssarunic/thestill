"""CliRunner tests for ``thestill analytics`` (spec #95 Phase 0)."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from tests.unit.analytics_fakes import NOW, FakeAnalyticsRepository
from thestill import cli as cli_module
from thestill.cli import main
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.services.analytics_service import AnalyticsService


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    """Tmp single-user SQLite install wired into the CLI via env."""
    storage = tmp_path / "data"
    storage.mkdir()
    monkeypatch.setenv("STORAGE_PATH", str(storage))
    monkeypatch.setenv("THESTILL_ENV_FILE", str(tmp_path / ".no-such-env"))
    SqlitePodcastRepository(db_path=str(storage / "podcasts.db"))


def test_single_user_says_off_and_exits_zero(cli_env):
    result = CliRunner().invoke(main, ["-q", "analytics"])
    assert result.exit_code == 0, result.output
    assert "Analytics is off" in result.output


def test_single_user_json(cli_env):
    result = CliRunner().invoke(main, ["-q", "analytics", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"available": False}


@pytest.fixture
def available(cli_env, monkeypatch):
    """Swap the CLI's service for one over canned data (the SQL has its own suite)."""
    monkeypatch.setattr(
        cli_module,
        "AnalyticsService",
        lambda repository, enabled: AnalyticsService(FakeAnalyticsRepository(), enabled=True, clock=lambda: NOW),
    )


def test_json_report(available):
    result = CliRunner().invoke(main, ["-q", "analytics", "--range", "7d", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["available"] is True
    assert payload["range"] == "7d"
    assert payload["engagement"]["opened_ratio"] == 0.75
    assert payload["content"]["podcasts"][0]["title"] == "Hard Fork"


def test_text_report(available):
    result = CliRunner().invoke(main, ["-q", "analytics"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "Signups: 3" in out
    assert "Followed a show within 24 h" in out
    assert "33.3% (1/3)" in out
    assert "2026-08-03" in out  # cohort row
    assert "Briefings opened: 3 of 4 (75%)" in out
    assert "Note: Admin accounts" in out


def test_text_report_content_block(available):
    out = CliRunner().invoke(main, ["-q", "analytics"]).output
    assert "Content: top podcasts by readers" in out
    hard_fork = next(line for line in out.splitlines() if line.lstrip().startswith("Hard Fork"))
    assert hard_fork.split()[2:4] == ["9", "+2"]  # readers 9, previously 7
    quiet = next(line for line in out.splitlines() if line.lstrip().startswith("Quiet Show"))
    assert quiet.split()[2:4] == ["1", "new"]
    assert "Hard Fork — The AI Episode" in out
    assert hard_fork.split()[-1] == "31"  # public views
    assert "12 public views" in out
    assert "Technology" in out and "10 readers (−2)" in out
    assert "Note: A reader opened an episode" in out


def test_rejects_unknown_range(cli_env):
    assert CliRunner().invoke(main, ["-q", "analytics", "--range", "1y"]).exit_code == 2


def test_scorecard_leads_the_text_report(available):
    out = CliRunner().invoke(main, ["-q", "analytics"]).output
    assert out.index("Scorecard (spec #111)") < out.index("Signups: 3")
    assert "Activation         new      25.0% (1/4)  week of 2026-09-28" in out
    assert "last 2 weeks: 25%  —" in out
    assert "Guardrail: Sign-in completion: see PostHog" in out
    assert "Opted out of usage analytics: 2. Connector days since: 2026-10-29." in out


def test_scorecard_only(available):
    out = CliRunner().invoke(main, ["-q", "analytics", "--scorecard"]).output
    assert "Scorecard (spec #111)" in out and "Signups" not in out
    result = CliRunner().invoke(main, ["-q", "analytics", "--scorecard", "--json"])
    payload = json.loads(result.stdout)
    assert payload["available"] is True and set(payload) == {"available", "scorecard"}
    assert [m["key"] for m in payload["scorecard"]["metrics"]] == [
        "activation",
        "engagement",
        "retention_w4",
        "weekly_return",
    ]
