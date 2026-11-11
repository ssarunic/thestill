"""CliRunner tests for ``thestill sync-track`` (spec #116): backfill and show."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests.unit.core.sync_track_fixtures import chunks, frames, seconds, stream
from thestill.cli import main
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository

PODCAST_ID = "22222222-2222-4222-8222-222222222222"
EPISODES = {
    "new": "11111111-1111-4111-8111-111111111111",
    "old": "33333333-3333-4333-8333-333333333333",
    "whisper": "44444444-4444-4444-8444-444444444444",
}
AUDIO = stream(frames(1, seconds(60)))


class _Gone(Exception):
    """Stands in for httpx.HTTPStatusError on a purged job."""

    response = type("Response", (), {"status_code": 410})()


@pytest.fixture
def storage(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("STORAGE_PATH", str(root))
    monkeypatch.setenv("THESTILL_ENV_FILE", str(tmp_path / ".no-such-env"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_path = str(root / "podcasts.db")
    SqlitePodcastRepository(db_path=db_path)
    (root / "raw_transcripts" / "show").mkdir(parents=True)
    (root / "clean_transcripts" / "show").mkdir(parents=True)

    def transcript(provider, job_id):
        return {
            "audio_file": "a",
            "language": "en",
            "text": "hello",
            "segments": [{"id": 0, "start": 0.0, "end": 50.0, "text": "hello"}],
            "processing_time": 1.0,
            "model_used": provider,
            "timestamp": 0.0,
            "provider_metadata": {"provider": provider, "job_id": job_id} if job_id else None,
        }

    rows = [
        ("new", "2026-11-09T10:00:00+00:00", transcript("dalston", "job-new")),
        ("old", "2026-09-01T10:00:00+00:00", transcript("dalston", "job-old")),
        ("whisper", "2026-11-08T10:00:00+00:00", transcript("whisper", None)),
    ]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, 'https://x/f.xml', 'Show', 'show')",
            (PODCAST_ID,),
        )
        for slug, pub_date, raw in rows:
            (root / "raw_transcripts" / "show" / f"{slug}_transcript.json").write_text(json.dumps(raw))
            sidecar = {"episode_id": EPISODES[slug], "segments": [], "sync_track_generation": None}
            (root / "clean_transcripts" / "show" / f"{slug}_cleaned.json").write_text(json.dumps(sidecar))
            conn.execute(
                "INSERT INTO episodes (id, podcast_id, external_id, title, slug, description, audio_url, pub_date,"
                " raw_transcript_path, clean_transcript_json_path)"
                " VALUES (?, ?, ?, ?, ?, '', 'https://x/a.mp3', ?, ?, ?)",
                (
                    EPISODES[slug],
                    PODCAST_ID,
                    f"ext-{slug}",
                    f"Episode {slug}",
                    slug,
                    pub_date,
                    f"show/{slug}_transcript.json",
                    f"show/{slug}_cleaned.json",
                ),
            )
    return root


@pytest.fixture
def dalston(monkeypatch):
    """Dalston still has ``job-new``; ``job-old`` was purged."""
    calls = []

    def fake_iter_job_audio(job_id, **kwargs):
        calls.append(job_id)
        if job_id != "job-new":
            raise _Gone()
        return chunks(AUDIO)

    monkeypatch.setattr("thestill.services.sync_track_backfill.iter_job_audio", fake_iter_job_audio)
    return calls


def _run(*args):
    result = CliRunner().invoke(main, ["sync-track", *args], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return result.output


def _show(slug):
    return json.loads(_run("show", "--podcast-id", "show", "--episode", slug, "--json").strip().splitlines()[-1])


def _stamp(storage, slug):
    return json.loads((storage / "clean_transcripts" / "show" / f"{slug}_cleaned.json").read_text())[
        "sync_track_generation"
    ]


def test_dry_run_reports_and_writes_nothing(storage, dalston):
    output = _run("backfill", "--dry-run")

    assert "would_build" in output and "show/new" in output and "show/old" in output
    assert "not_dalston 1" in output
    assert dalston == []  # no audio fetched
    assert not (storage / "sync_tracks" / "show").exists()
    assert _stamp(storage, "new") is None
    assert _show("new")["has_track"] is False


def test_backfill_builds_stamps_and_reports_gone_audio(storage, dalston):
    output = _run("backfill")

    assert dalston == ["job-new", "job-old"]  # newest episode first
    assert "built 1" in output and "audio_gone 1" in output and "not_dalston 1" in output
    assert "cleaned transcripts stamped: 1" in output

    shown = _show("new")
    assert shown["has_track"] is True and shown["source"] == "dalston"
    assert shown["audio_bytes"] == len(AUDIO)
    assert 55 <= shown["anchors_per_minute"] <= 61
    assert shown["usable_by_reader"] is True
    assert _stamp(storage, "new") == shown["generation"]

    assert _show("old")["has_track"] is False
    assert _stamp(storage, "old") is None


def test_backfill_is_idempotent(storage, dalston):
    _run("backfill")
    generation = _show("new")["generation"]

    output = _run("backfill")

    assert "has_track 1" in output
    assert dalston.count("job-new") == 1  # not fetched again
    assert _show("new")["generation"] == generation


def test_cleaned_transcript_older_than_the_raw_one_is_not_stamped(storage, dalston):
    """A reprocess re-transcribed the episode; the visible cleaned transcript
    still belongs to the previous raw transcript."""
    sidecar = storage / "clean_transcripts" / "show" / "new_cleaned.json"
    raw = storage / "raw_transcripts" / "show" / "new_transcript.json"
    os.utime(sidecar, (raw.stat().st_mtime - 3600, raw.stat().st_mtime - 3600))

    _run("backfill")

    shown = _show("new")
    assert shown["has_track"] is True
    assert shown["usable_by_reader"] is False
    assert _stamp(storage, "new") is None


def test_stops_after_a_row_of_purged_jobs(storage, dalston):
    output = _run("backfill", "--stop-after-gone", "1", "--podcast-id", "show")
    assert "built 1" in output and "Stopping" in output


def test_max_episodes_limits_attempts(storage, dalston):
    _run("backfill", "--max-episodes", "1")
    assert dalston == ["job-new"]


def test_show_human_output_for_an_episode_without_a_track(storage):
    output = _run("show", "--podcast-id", "show", "--episode", "whisper")
    assert "no sync track" in output and "usable by the reader      no" in output
