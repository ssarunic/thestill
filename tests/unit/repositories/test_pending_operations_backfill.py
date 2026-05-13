# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Spec #40 — backfill migration test.

Seeds ``tmp_path/pending_operations/`` with legacy JSON files (one ElevenLabs,
one Google), runs the podcast repo constructor (which fires the migration),
and asserts the rows are populated correctly and the source files have been
moved to ``.migrated/``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from thestill.repositories.sqlite_pending_operations_repository import SqlitePendingOperationsRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository


def _write_elevenlabs_file(pending_dir: Path) -> Path:
    pending_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "provider": "elevenlabs",
        "transcription_id": "el-job-abc",
        "audio_path": "downsampled_audio/foo/ep.wav",
        "language": "en",
        "episode_id": "ep-eleven",
        "podcast_slug": "foo",
        "episode_slug": "ep-one",
        "created_at": "2026-05-13T10:00:00+00:00",
        "state": "pending",
    }
    p = pending_dir / "elevenlabs_el-job-abc.json"
    p.write_text(json.dumps(payload, indent=2))
    return p


def _write_google_file(pending_dir: Path) -> Path:
    pending_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "operation_id": "g-op-xyz",
        "operation_name": "projects/p/locations/eu/operations/g-op-xyz",
        "episode_id": "ep-google",
        "podcast_slug": "bar",
        "episode_slug": "ep-two",
        "audio_gcs_uri": "gs://bucket/audio.wav",
        "output_gcs_uri": "gs://bucket/out/",
        "language": "en",
        "chunk_index": None,
        "chunk_start_ms": None,
        "chunk_end_ms": None,
        "total_chunks": None,
        "state": "pending",
        "created_at": "2026-05-13T11:00:00+00:00",
        "completed_at": None,
        "error": None,
        "transcript_gcs_uri": None,
        "local_transcript_path": None,
    }
    p = pending_dir / "g-op-xyz.json"
    p.write_text(json.dumps(payload, indent=2))
    return p


@pytest.fixture
def data_root(tmp_path) -> Path:
    """A staged ``data/`` root with the ``pending_operations/`` dir adjacent
    to the DB path the migration will read."""
    root = tmp_path / "data"
    root.mkdir()
    return root


class TestBackfillMigration:
    def test_both_providers_imported_into_table(self, data_root: Path):
        _write_elevenlabs_file(data_root / "pending_operations")
        _write_google_file(data_root / "pending_operations")

        # Trigger the migration by constructing the podcast repo against the
        # data root's DB path. ``__init__`` runs _run_migrations which fires
        # the spec #40 block + backfill.
        SqlitePodcastRepository(db_path=str(data_root / "podcasts.db"))

        # Read back through the pending-ops repo.
        repo = SqlitePendingOperationsRepository(db_path=str(data_root / "podcasts.db"))
        elevenlabs = repo.list_by_provider("elevenlabs")
        google = repo.list_by_provider("google")

        assert len(elevenlabs) == 1
        assert elevenlabs[0].operation_id == "el-job-abc"
        assert elevenlabs[0].episode_id == "ep-eleven"
        assert elevenlabs[0].payload["audio_path"] == "downsampled_audio/foo/ep.wav"

        assert len(google) == 1
        assert google[0].operation_id == "g-op-xyz"
        assert google[0].episode_id == "ep-google"
        assert google[0].payload["audio_gcs_uri"] == "gs://bucket/audio.wav"

    def test_source_files_moved_to_migrated_subdir(self, data_root: Path):
        elevenlabs_file = _write_elevenlabs_file(data_root / "pending_operations")
        google_file = _write_google_file(data_root / "pending_operations")

        SqlitePodcastRepository(db_path=str(data_root / "podcasts.db"))

        assert not elevenlabs_file.exists()
        assert not google_file.exists()
        migrated = data_root / "pending_operations" / ".migrated"
        assert (migrated / "elevenlabs_el-job-abc.json").exists()
        assert (migrated / "g-op-xyz.json").exists()

    def test_idempotent_on_repeat_construction(self, data_root: Path):
        # The migration only runs once (table-existence guard). A second
        # construction must not re-process files (there are none left) or
        # otherwise double-insert.
        _write_elevenlabs_file(data_root / "pending_operations")
        SqlitePodcastRepository(db_path=str(data_root / "podcasts.db"))
        SqlitePodcastRepository(db_path=str(data_root / "podcasts.db"))  # second run

        repo = SqlitePendingOperationsRepository(db_path=str(data_root / "podcasts.db"))
        rows = repo.list_by_provider("elevenlabs")
        assert len(rows) == 1  # not duplicated

    def test_missing_pending_operations_dir_is_noop(self, data_root: Path):
        # Fresh install, never had any pending ops files.
        SqlitePodcastRepository(db_path=str(data_root / "podcasts.db"))
        repo = SqlitePendingOperationsRepository(db_path=str(data_root / "podcasts.db"))
        assert repo.list_by_provider("elevenlabs") == []
        assert repo.list_by_provider("google") == []

    def test_malformed_file_does_not_block_other_imports(self, data_root: Path):
        pending = data_root / "pending_operations"
        pending.mkdir(parents=True, exist_ok=True)
        # Garbage JSON shouldn't break the other backfill rows.
        (pending / "elevenlabs_broken.json").write_text("{not valid json")
        _write_google_file(pending)

        SqlitePodcastRepository(db_path=str(data_root / "podcasts.db"))

        repo = SqlitePendingOperationsRepository(db_path=str(data_root / "podcasts.db"))
        # Google still imported.
        assert len(repo.list_by_provider("google")) == 1
        # Broken file stays in place (not moved to .migrated/) so an operator
        # can inspect what went wrong.
        assert (pending / "elevenlabs_broken.json").exists()

    def test_already_migrated_files_skipped_on_replay(self, data_root: Path):
        # An operator might re-run the migration in the wild. The .migrated/
        # subdir is excluded from the backfill scan.
        pending = data_root / "pending_operations"
        pending.mkdir(parents=True, exist_ok=True)
        already = pending / ".migrated"
        already.mkdir()
        # Put a "previously-migrated" file in .migrated/ that, if scanned,
        # would import incorrectly.
        (already / "elevenlabs_old.json").write_text(json.dumps({"episode_id": "ghost", "provider": "elevenlabs"}))

        SqlitePodcastRepository(db_path=str(data_root / "podcasts.db"))

        repo = SqlitePendingOperationsRepository(db_path=str(data_root / "podcasts.db"))
        assert repo.list_by_provider("elevenlabs") == []

    def test_file_missing_episode_id_skipped(self, data_root: Path):
        pending = data_root / "pending_operations"
        pending.mkdir(parents=True, exist_ok=True)
        # Legacy file from before the episode_id-required guard existed.
        (pending / "elevenlabs_legacy.json").write_text(
            json.dumps({"provider": "elevenlabs", "transcription_id": "legacy"})
        )

        SqlitePodcastRepository(db_path=str(data_root / "podcasts.db"))

        repo = SqlitePendingOperationsRepository(db_path=str(data_root / "podcasts.db"))
        assert repo.list_by_provider("elevenlabs") == []
        # The unparseable-but-not-malformed file is left in place (not moved
        # to .migrated/) so an operator can decide what to do with it.
        assert (pending / "elevenlabs_legacy.json").exists()
