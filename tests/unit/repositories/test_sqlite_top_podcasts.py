"""Tests for the top_podcasts + top_podcast_rankings tables, JSON-driven seeding,
mtime-gated smart refresh, dedupe, and the free-tier lookup helper.
"""

import json
import os
import sqlite3
import time

import pytest

from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository


def _write_chart(path, region, entries):
    """Write a top_podcasts_<region>.json file with given entries."""
    path.write_text(json.dumps(entries))


def _entry(name, rss_url, rank, **fields):
    """Helper: minimal chart entry dict."""
    return {
        "name": name,
        "rss_url": rss_url,
        "rank": rank,
        **fields,
    }


@pytest.fixture
def repo_with_charts(tmp_path, monkeypatch):
    """Repo whose chart-data dir is monkeypatched to a tmp dir we control."""
    chart_dir = tmp_path / "charts"
    chart_dir.mkdir()
    monkeypatch.setattr(SqlitePodcastRepository, "_TOP_PODCASTS_DIR", chart_dir)

    def _build(charts: dict[str, list[dict]] | None = None):
        for region, entries in (charts or {}).items():
            _write_chart(chart_dir / f"top_podcasts_{region}.json", region, entries)
        return SqlitePodcastRepository(str(tmp_path / "t.db"))

    return _build, chart_dir


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestSchema:
    def test_all_three_tables_created(self, repo_with_charts):
        build, _ = repo_with_charts
        repo = build({"us": []})
        with repo._get_connection() as conn:
            tables = {
                row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
        assert {"top_podcasts", "top_podcast_rankings", "top_podcasts_meta"} <= tables

    def test_top_podcasts_rss_url_is_unique(self, repo_with_charts):
        build, _ = repo_with_charts
        repo = build()
        with repo._get_connection() as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO top_podcasts (name, rss_url, first_seen_at, last_seen_at) " "VALUES (?, ?, ?, ?)",
                    ("A", "https://e/x", "2026-01-01", "2026-01-01"),
                )
                conn.execute(
                    "INSERT INTO top_podcasts (name, rss_url, first_seen_at, last_seen_at) " "VALUES (?, ?, ?, ?)",
                    ("A", "https://e/x", "2026-01-01", "2026-01-01"),
                )

    def test_ranking_pk_prevents_same_podcast_twice_per_region(self, repo_with_charts):
        build, _ = repo_with_charts
        repo = build()
        with repo._get_connection() as conn:
            conn.execute(
                "INSERT INTO top_podcasts (name, rss_url, first_seen_at, last_seen_at) " "VALUES (?, ?, ?, ?)",
                ("X", "https://e/x", "2026-01-01", "2026-01-01"),
            )
            pid = conn.execute("SELECT id FROM top_podcasts WHERE rss_url = 'https://e/x'").fetchone()["id"]
            conn.execute(
                "INSERT INTO top_podcast_rankings VALUES (?, 'us', 1, NULL, '2026-01-01')",
                (pid,),
            )
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO top_podcast_rankings VALUES (?, 'us', 2, NULL, '2026-01-01')",
                    (pid,),
                )


# ---------------------------------------------------------------------------
# Seeding behavior
# ---------------------------------------------------------------------------


class TestSeeding:
    def test_seeds_one_region(self, repo_with_charts):
        build, _ = repo_with_charts
        repo = build(
            {
                "us": [
                    _entry("A", "https://e/a", 1, category="News"),
                    _entry("B", "https://e/b", 2, category="Comedy"),
                ]
            }
        )
        with repo._get_connection() as conn:
            n_meta = conn.execute("SELECT COUNT(*) AS n FROM top_podcasts").fetchone()["n"]
            n_rank = conn.execute("SELECT COUNT(*) AS n FROM top_podcast_rankings").fetchone()["n"]
        assert n_meta == 2
        assert n_rank == 2

    def test_overlap_between_regions_dedupes_metadata(self, repo_with_charts):
        """Same podcast in two regions = one metadata row, two rankings rows."""
        build, _ = repo_with_charts
        repo = build(
            {
                "us": [_entry("Shared", "https://e/shared", 1)],
                "gb": [_entry("Shared", "https://e/shared", 5)],
            }
        )
        with repo._get_connection() as conn:
            n_meta = conn.execute("SELECT COUNT(*) AS n FROM top_podcasts").fetchone()["n"]
            n_rank = conn.execute("SELECT COUNT(*) AS n FROM top_podcast_rankings").fetchone()["n"]
        assert n_meta == 1
        assert n_rank == 2

    def test_dedupes_within_region_keeping_better_rank(self, repo_with_charts):
        """Apple sometimes lists the same RSS under two track_ids; keep lower rank."""
        build, _ = repo_with_charts
        repo = build(
            {
                "us": [
                    _entry("Twin", "https://e/twin", 50),
                    _entry("Twin", "https://e/twin", 12),  # better rank wins
                ]
            }
        )
        with repo._get_connection() as conn:
            rank = conn.execute("SELECT rank FROM top_podcast_rankings WHERE region = 'us'").fetchone()["rank"]
        assert rank == 12

    def test_resolves_category_to_fk(self, repo_with_charts):
        build, _ = repo_with_charts
        repo = build(
            {
                "us": [
                    _entry("X", "https://e/x", 1, category="News", subcategory="Politics"),
                ]
            }
        )
        with repo._get_connection() as conn:
            row = conn.execute(
                "SELECT c.name AS cat FROM top_podcasts p "
                "JOIN categories c ON c.id = p.category_id WHERE p.rss_url = 'https://e/x'"
            ).fetchone()
        # Most-specific resolution → subcategory name (Politics).
        assert row["cat"] == "Politics"

    def test_skips_entries_with_no_rss_url(self, repo_with_charts):
        build, _ = repo_with_charts
        repo = build(
            {
                "us": [
                    _entry("Good", "https://e/good", 1),
                    {"name": "NoRss", "rank": 2},
                ]
            }
        )
        with repo._get_connection() as conn:
            n = conn.execute("SELECT COUNT(*) AS n FROM top_podcasts").fetchone()["n"]
        assert n == 1


# ---------------------------------------------------------------------------
# Smart-refresh (mtime-gated re-seeding)
# ---------------------------------------------------------------------------


class TestSmartRefresh:
    def test_reopen_with_unchanged_files_is_silent_noop(self, repo_with_charts):
        build, chart_dir = repo_with_charts
        repo1 = build({"us": [_entry("A", "https://e/a", 1)]})
        with repo1._get_connection() as conn:
            seeded_before = conn.execute("SELECT seeded_at FROM top_podcasts_meta WHERE region = 'us'").fetchone()[
                "seeded_at"
            ]
        # Re-open without touching files — should NOT re-seed (seeded_at unchanged).
        repo2 = SqlitePodcastRepository(str(chart_dir.parent / "t.db"))
        with repo2._get_connection() as conn:
            seeded_after = conn.execute("SELECT seeded_at FROM top_podcasts_meta WHERE region = 'us'").fetchone()[
                "seeded_at"
            ]
        assert seeded_before == seeded_after

    def test_bumping_mtime_triggers_reseed_for_that_region_only(self, repo_with_charts):
        build, chart_dir = repo_with_charts
        repo1 = build(
            {
                "us": [_entry("A", "https://e/a", 1)],
                "gb": [_entry("B", "https://e/b", 1)],
            }
        )
        with repo1._get_connection() as conn:
            before = {
                row["region"]: row["seeded_at"]
                for row in conn.execute("SELECT region, seeded_at FROM top_podcasts_meta").fetchall()
            }
        # Bump only the us file's mtime.
        new_mtime = time.time() + 100  # clearly newer than original
        os.utime(chart_dir / "top_podcasts_us.json", (new_mtime, new_mtime))
        repo2 = SqlitePodcastRepository(str(chart_dir.parent / "t.db"))
        with repo2._get_connection() as conn:
            after = {
                row["region"]: row["seeded_at"]
                for row in conn.execute("SELECT region, seeded_at FROM top_podcasts_meta").fetchall()
            }
        # us was re-seeded; gb was not.
        assert after["us"] != before["us"]
        assert after["gb"] == before["gb"]

    def test_replacing_chart_replaces_rankings_atomically(self, repo_with_charts):
        build, chart_dir = repo_with_charts
        repo = build(
            {
                "us": [
                    _entry("Old1", "https://e/old1", 1),
                    _entry("Old2", "https://e/old2", 2),
                ]
            }
        )
        # Rewrite the file with totally different entries + bump mtime.
        new_path = chart_dir / "top_podcasts_us.json"
        new_path.write_text(json.dumps([_entry("New1", "https://e/new1", 1)]))
        new_mtime = time.time() + 100
        os.utime(new_path, (new_mtime, new_mtime))
        SqlitePodcastRepository(str(chart_dir.parent / "t.db"))
        with repo._get_connection() as conn:
            urls = sorted(
                row["rss_url"]
                for row in conn.execute(
                    "SELECT p.rss_url FROM top_podcast_rankings r "
                    "JOIN top_podcasts p ON p.id = r.top_podcast_id "
                    "WHERE r.region = 'us'"
                ).fetchall()
            )
        assert urls == ["https://e/new1"]


# ---------------------------------------------------------------------------
# Free-tier lookup helper
# ---------------------------------------------------------------------------


class TestIsTopPodcastInRegion:
    def test_returns_true_for_charted_podcast_in_correct_region(self, repo_with_charts):
        build, _ = repo_with_charts
        repo = build({"us": [_entry("A", "https://e/a", 1)]})
        assert repo.is_top_podcast_in_region("https://e/a", "us") is True

    def test_returns_false_for_charted_podcast_in_other_region(self, repo_with_charts):
        build, _ = repo_with_charts
        repo = build({"us": [_entry("A", "https://e/a", 1)]})
        assert repo.is_top_podcast_in_region("https://e/a", "gb") is False

    def test_returns_false_for_unknown_url(self, repo_with_charts):
        build, _ = repo_with_charts
        repo = build({"us": [_entry("A", "https://e/a", 1)]})
        assert repo.is_top_podcast_in_region("https://e/nope", "us") is False

    def test_region_lookup_is_case_insensitive(self, repo_with_charts):
        build, _ = repo_with_charts
        repo = build({"us": [_entry("A", "https://e/a", 1)]})
        assert repo.is_top_podcast_in_region("https://e/a", "US") is True

    def test_handles_none_and_empty_inputs(self, repo_with_charts):
        build, _ = repo_with_charts
        repo = build({"us": [_entry("A", "https://e/a", 1)]})
        assert repo.is_top_podcast_in_region("", "us") is False
        assert repo.is_top_podcast_in_region("https://e/a", "") is False


class TestGetTopPodcasts:
    def test_returns_rows_ordered_by_rank(self, repo_with_charts):
        build, _ = repo_with_charts
        repo = build(
            {
                "us": [
                    _entry("Two", "https://e/two", 2),
                    _entry("One", "https://e/one", 1),
                    _entry("Three", "https://e/three", 3),
                ]
            }
        )
        rows = repo.get_top_podcasts("us")
        assert [r["rank"] for r in rows] == [1, 2, 3]
        assert [r["name"] for r in rows] == ["One", "Two", "Three"]

    def test_filters_by_category(self, repo_with_charts):
        build, _ = repo_with_charts
        repo = build(
            {
                "us": [
                    _entry("A", "https://e/a", 1, category="News"),
                    _entry("B", "https://e/b", 2, category="Comedy"),
                    _entry("C", "https://e/c", 3, category="News"),
                ]
            }
        )
        rows = repo.get_top_podcasts("us", category="News")
        assert [r["name"] for r in rows] == ["A", "C"]

    def test_respects_limit(self, repo_with_charts):
        build, _ = repo_with_charts
        repo = build({"us": [_entry(f"P{i}", f"https://e/{i}", i) for i in range(1, 11)]})
        assert len(repo.get_top_podcasts("us", limit=3)) == 3

    def test_empty_region_returns_empty_list(self, repo_with_charts):
        build, _ = repo_with_charts
        repo = build({"us": [_entry("A", "https://e/a", 1)]})
        assert repo.get_top_podcasts("nope") == []
