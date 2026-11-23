"""Spec #115 — the admin-only ``get_analytics`` connector tool.

Exercised through the mounted Streamable HTTP transport (real token rows,
real admin resolution per request). The harness runs on SQLite, where
analytics is unavailable; the available path swaps in the canned report.
"""

from __future__ import annotations

import json

import pytest

from tests.unit.analytics_fakes import NOW, FakeAnalyticsRepository
from tests.unit.web.test_mcp_http import USER_A, USER_B, Harness, isolated_env  # noqa: F401
from thestill.services.analytics_service import AnalyticsService


@pytest.fixture
def harness(isolated_env, tmp_path):  # noqa: F811
    return Harness(tmp_path)


@pytest.fixture
def fake_repo():
    return FakeAnalyticsRepository()


@pytest.fixture
def harness_with_data(isolated_env, tmp_path, monkeypatch, fake_repo):  # noqa: F811
    monkeypatch.setattr(
        "thestill.mcp.tools.AnalyticsService",
        lambda repository, enabled, **kwargs: AnalyticsService(fake_repo, enabled=True, clock=lambda: NOW, **kwargs),
    )
    return Harness(tmp_path)


def _tool_names(client, token):
    response = Harness.rpc(client, token, "tools/list")
    assert response.status_code == 200, response.content
    return {t["name"] for t in response.json()["result"]["tools"]}


def _call(client, token, arguments=None):
    response = Harness.rpc(client, token, "tools/call", {"name": "get_analytics", "arguments": arguments or {}})
    assert response.status_code == 200, response.content
    result = response.json()["result"]
    return result, json.loads(result["content"][0]["text"]) if result["content"][0]["text"].startswith("{") else None


class TestVisibility:
    def test_listed_for_admins_only(self, harness):
        admin = harness.mint(USER_B, is_admin=True)
        reader = harness.mint(USER_A)
        with harness.client() as c:
            assert "get_analytics" in _tool_names(c, admin)
            assert "get_analytics" not in _tool_names(c, reader)

    def test_non_admin_call_reads_as_an_unknown_tool(self, harness):
        reader = harness.mint(USER_A)
        with harness.client() as c:
            _, payload = _call(c, reader)
        assert payload == {"success": False, "error": "Unknown tool: get_analytics"}

    def test_demoted_admin_loses_it_on_the_next_request(self, harness):
        admin = harness.mint(USER_B, is_admin=True)
        with harness.client() as c:
            assert "get_analytics" in _tool_names(c, admin)
            harness.repos.user.set_admin(USER_B, False)
            assert "get_analytics" not in _tool_names(c, admin)
            _, payload = _call(c, admin)
        assert payload["success"] is False and "Unknown tool" in payload["error"]


class TestReport:
    def test_unavailable_on_sqlite(self, harness):
        admin = harness.mint(USER_B, is_admin=True)
        with harness.client() as c:
            _, payload = _call(c, admin)
        assert payload["available"] is False and "Postgres" in payload["reason"]

    def test_admin_gets_the_report_with_traffic(self, harness_with_data, fake_repo):
        admin = harness_with_data.mint(USER_B, is_admin=True)
        with harness_with_data.client() as c:
            _, payload = _call(c, admin)
        assert payload["available"] is True
        assert payload["range"] == "7d"  # the tool's default, not the page's 28d
        assert {"scorecard", "signups", "activation", "cohorts", "engagement", "content", "traffic"} <= set(payload)
        assert payload["traffic"]["total"] == 50
        assert payload["traffic"]["top_pages"][0]["path"] == "/podcasts/hard-fork/episodes/the-ai-episode"
        traffic_calls = [args for name, args in fake_repo.calls if name == "traffic"]
        assert len(traffic_calls) == 1 and traffic_calls[0][0].days == 7

    def test_range_is_passed_through(self, harness_with_data):
        admin = harness_with_data.mint(USER_B, is_admin=True)
        with harness_with_data.client() as c:
            _, payload = _call(c, admin, {"range": "28d"})
        assert payload["range"] == "28d" and payload["window"]["days"] == 28

    def test_bad_range_is_refused_by_the_schema(self, harness_with_data):
        admin = harness_with_data.mint(USER_B, is_admin=True)
        with harness_with_data.client() as c:
            result, _ = _call(c, admin, {"range": "1y"})
        assert result["isError"] is True and "Input validation error" in result["content"][0]["text"]

    def test_report_carries_no_emails(self, harness_with_data):
        admin = harness_with_data.mint(USER_B, is_admin=True)
        with harness_with_data.client() as c:
            result, _ = _call(c, admin)
        assert "@" not in result["content"][0]["text"]


class TestDetailAndCache:
    def test_summary_is_the_default_and_drops_the_per_source_funnel(self, harness_with_data):
        admin = harness_with_data.mint(USER_B, is_admin=True)
        with harness_with_data.client() as c:
            _, summary = _call(c, admin)
            _, full = _call(c, admin, {"detail": "full"})
        assert summary["detail"] == "summary" and "detail=full" in summary["trimmed"]
        assert summary["activation"]["by_source"] == [] and summary["activation"]["overall"]["steps"]
        assert full["detail"] == "full" and "trimmed" not in full
        assert [g["source"] for g in full["activation"]["by_source"]] == ["episode", "direct"]
        # Totals are never trimmed.
        assert summary["traffic"]["total"] == full["traffic"]["total"] == 50

    def test_summary_cuts_ranked_lists(self):
        from thestill.mcp.analytics_tools import SUMMARY_COHORT_ROWS, SUMMARY_ROWS, _summary

        rows = [{"n": i} for i in range(25)]
        payload = {
            "activation": {"overall": {}, "by_source": rows},
            "cohorts": {"weeks": [1], "rows": rows},
            "content": {"podcasts": rows, "episodes": rows, "categories": rows},
            "traffic": {"total": 99, "top_pages": rows},
        }
        out = _summary(payload)
        assert len(out["content"]["podcasts"]) == len(out["content"]["episodes"]) == SUMMARY_ROWS
        assert len(out["traffic"]["top_pages"]) == SUMMARY_ROWS and out["traffic"]["total"] == 99
        assert out["cohorts"]["rows"] == rows[-SUMMARY_COHORT_ROWS:]  # the newest weeks
        assert len(out["content"]["categories"]) == 25
        assert len(payload["content"]["podcasts"]) == 25  # the cached report is not mutated

    def test_report_is_cached_per_range(self, harness_with_data, fake_repo):
        admin = harness_with_data.mint(USER_B, is_admin=True)
        with harness_with_data.client() as c:
            _call(c, admin)
            _call(c, admin, {"detail": "full"})
            _call(c, admin, {"range": "28d"})
        assert [args[0].days for name, args in fake_repo.calls if name == "traffic"] == [7, 28]
