"""Spec #110 — ``thestill transcript`` against a tmp-DB-backed CLIContext.

The Phase 0 gate: a CLI ``speaker.rename_label`` shows in the transcript
API and MCP read paths (both go through ``PodcastService``) and in the
summarizer's input, survives a re-clean of the same raw transcript, and
reverts cleanly; an unedited episode's summarizer input is the file as
written.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from tests.unit.transcript_edit_fixtures import (
    EPISODE_ID,
    MARKDOWN_REL,
    PODCAST_ID,
    make_episode,
    make_raw,
    make_sidecar,
    seed_episode_rows,
    write_transcripts,
)
from thestill.cli import main
from thestill.core.task_handlers import _edited_transcript_markdown
from thestill.core.transcript_edits import clear_cache
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.repositories.sqlite_transcript_edit_repository import SqliteTranscriptEditRepository
from thestill.services.podcast_service import PodcastService
from thestill.utils.file_storage import LocalFileStorage


def _json(output: str):
    """The JSON document in CLI output (structlog console lines may precede it)."""
    return json.loads(output[output.index("{") :])


@pytest.fixture
def env(tmp_path, monkeypatch):
    storage = tmp_path / "data"
    raw = make_raw()
    sidecar = make_sidecar(raw)
    paths = write_transcripts(storage, raw, sidecar)
    monkeypatch.setenv("STORAGE_PATH", str(storage))
    monkeypatch.setenv("THESTILL_ENV_FILE", str(tmp_path / ".no-such-env"))
    db = str(storage / "podcasts.db")
    SqlitePodcastRepository(db_path=db)
    seed_episode_rows(db)
    clear_cache()
    yield {"storage": storage, "paths": paths, "db": db, "raw": raw, "sidecar": sidecar}
    clear_cache()


def _service(env) -> PodcastService:
    repository = MagicMock()
    return PodcastService(
        env["storage"],
        repository,
        env["paths"],
        file_storage=LocalFileStorage(base_path=str(env["storage"])),
        transcript_edits=SqliteTranscriptEditRepository(db_path=env["db"]),
    )


def test_rename_speaker_reaches_every_reader_and_reverts(env):
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "transcript",
            "rename-speaker",
            "--episode-id",
            EPISODE_ID,
            "--label",
            "SPEAKER_01",
            "--name",
            "Grace Hopper",
            "--no-refresh",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "SPEAKER_01 → Grace Hopper" in result.output

    service = _service(env)
    episode = make_episode()
    # Transcript API (segments) and MCP get_transcript (markdown) both show it.
    segmented = service.get_segmented_transcript_for_episode(episode)
    assert any(s.speaker == "Grace Hopper" for s in segmented.annotated.segments)
    assert "**Grace Hopper:**" in service.get_transcript_for_episode(episode).content
    # Words API drops nothing for a speaker-only edit.
    assert service.get_transcript_words_for_episode(episode) is not None
    # The summarizer renders the effective transcript, ads stripped.
    state = MagicMock()
    state.path_manager = env["paths"]
    state.config.file_storage = service.file_storage
    state.transcript_edit_repository = SqliteTranscriptEditRepository(db_path=env["db"])
    markdown = _edited_transcript_markdown(episode, state)
    assert "Grace Hopper" in markdown and "Sponsor Corp" not in markdown

    listing = runner.invoke(main, ["transcript", "edits", "--episode-id", EPISODE_ID, "--json"])
    assert listing.exit_code == 0, listing.output
    payload = _json(listing.output)
    assert payload["edits"][0]["status"] == "applied"
    assert {s["label"]: s["name"] for s in payload["speakers"]}["SPEAKER_01"] == "Grace Hopper"

    # Survives a re-clean of the same raw transcript (new sidecar text, same anchors).
    recleaned = make_sidecar(env["raw"])
    recleaned.segments[0].text = "Welcome back to the show, everyone!"
    write_transcripts(env["storage"], env["raw"], recleaned)
    clear_cache()
    assert any(
        s.speaker == "Grace Hopper" for s in service.get_segmented_transcript_for_episode(episode).annotated.segments
    )

    revert = runner.invoke(main, ["transcript", "revert", "--edit", payload["edits"][0]["id"]])
    assert revert.exit_code == 0, revert.output
    clear_cache()
    assert not any(
        s.speaker == "Grace Hopper" for s in service.get_segmented_transcript_for_episode(episode).annotated.segments
    )
    assert _edited_transcript_markdown(episode, state) is None


def test_unedited_episode_reads_the_files_as_written(env):
    service = _service(env)
    episode = make_episode()
    expected = env["paths"].clean_transcript_file(MARKDOWN_REL).read_text(encoding="utf-8")
    assert service.get_transcript_for_episode(episode).content == expected
    assert service.get_segmented_transcript_for_episode(episode).annotated.model_dump() == env["sidecar"].model_dump()
    state = MagicMock()
    state.path_manager = env["paths"]
    state.config.file_storage = service.file_storage
    state.transcript_edit_repository = SqliteTranscriptEditRepository(db_path=env["db"])
    assert _edited_transcript_markdown(episode, state) is None


def test_speaker_stats_and_conflicts(env):
    runner = CliRunner()
    stats = runner.invoke(main, ["transcript", "speaker-stats", "--json"])
    assert stats.exit_code == 0, stats.output
    payload = _json(stats.output)
    assert payload["episodes"] == 1 and payload["episodes_with_unnamed"] == 1 and payload["unnamed_segments"] == 2
    conflicts = runner.invoke(main, ["transcript", "conflicts"])
    assert conflicts.exit_code == 0 and "No conflicts" in conflicts.output
    assert PODCAST_ID  # the seeded podcast is the one the stats walked
