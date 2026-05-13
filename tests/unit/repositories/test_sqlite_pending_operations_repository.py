# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Spec #40 — SqlitePendingOperationsRepository unit tests.

CRUD round-trip, filtered listings, idempotent delete, payload-JSON
preservation, and the table-level CHECK constraint on ``provider``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from thestill.repositories.sqlite_pending_operations_repository import SqlitePendingOperationsRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository


@pytest.fixture
def db_path(tmp_path) -> str:
    """Initialised SQLite DB with the spec #40 migration run."""
    db = tmp_path / "podcasts.db"
    # SqlitePodcastRepository.__init__ runs migrations including the
    # ``pending_transcription_operations`` table creation.
    SqlitePodcastRepository(db_path=str(db))
    return str(db)


@pytest.fixture
def repo(db_path) -> SqlitePendingOperationsRepository:
    return SqlitePendingOperationsRepository(db_path=db_path)


def _sample_elevenlabs_payload() -> dict:
    return {
        "provider": "elevenlabs",
        "transcription_id": "el-job-123",
        "audio_path": "downsampled_audio/foo/ep.wav",
        "language": "en",
        "episode_id": "ep-abc",
        "podcast_slug": "foo",
        "episode_slug": "ep-one",
        "created_at": "2026-05-13T12:00:00+00:00",
        "state": "pending",
    }


def _sample_google_payload() -> dict:
    return {
        "operation_id": "g-op-456",
        "operation_name": "projects/p/locations/eu/operations/g-op-456",
        "episode_id": "ep-xyz",
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
        "created_at": "2026-05-13T12:00:00+00:00",
        "completed_at": None,
        "error": None,
        "transcript_gcs_uri": None,
        "local_transcript_path": None,
    }


class TestCreateAndGet:
    def test_round_trip_elevenlabs_payload(self, repo):
        payload = _sample_elevenlabs_payload()
        repo.create(operation_id="el-job-123", provider="elevenlabs", episode_id="ep-abc", payload=payload)

        op = repo.get("el-job-123")
        assert op is not None
        assert op.operation_id == "el-job-123"
        assert op.provider == "elevenlabs"
        assert op.episode_id == "ep-abc"
        assert op.payload == payload  # full lossless round-trip

    def test_round_trip_google_payload(self, repo):
        payload = _sample_google_payload()
        repo.create(operation_id="g-op-456", provider="google", episode_id="ep-xyz", payload=payload)

        op = repo.get("g-op-456")
        assert op is not None
        assert op.provider == "google"
        assert op.payload == payload

    def test_get_missing_returns_none(self, repo):
        assert repo.get("does-not-exist") is None

    def test_nested_payload_structures_preserved(self, repo):
        # Google's payload nests datetimes serialized to strings; ElevenLabs's
        # is flat. Confirm a hypothetical nested dict + list also round-trips.
        payload = {
            "operation_id": "x",
            "extras": {"chunks": [{"index": 0, "start": 0}, {"index": 1, "start": 1500}]},
        }
        repo.create(operation_id="x", provider="google", episode_id="ep", payload=payload)
        assert repo.get("x").payload == payload


class TestDuplicateOperationId:
    def test_create_with_duplicate_id_raises_integrity_error(self, repo):
        repo.create("dup", "elevenlabs", "ep1", {"provider": "elevenlabs"})
        with pytest.raises(sqlite3.IntegrityError):
            repo.create("dup", "elevenlabs", "ep1", {"provider": "elevenlabs"})


class TestProviderCheckConstraint:
    def test_unknown_provider_rejected_at_db_layer(self, repo):
        # The table CHECK enforces provider IN ('google','elevenlabs') —
        # belt-and-braces against typos in caller code.
        with pytest.raises(sqlite3.IntegrityError):
            repo.create("x", "azure-speech", "ep", {})


class TestListByProvider:
    def test_filters_by_provider(self, repo):
        repo.create("el-1", "elevenlabs", "ep1", {"k": "v1"})
        repo.create("el-2", "elevenlabs", "ep2", {"k": "v2"})
        repo.create("g-1", "google", "ep3", {"k": "v3"})

        elevenlabs = repo.list_by_provider("elevenlabs")
        google = repo.list_by_provider("google")

        assert sorted(op.operation_id for op in elevenlabs) == ["el-1", "el-2"]
        assert sorted(op.operation_id for op in google) == ["g-1"]

    def test_oldest_first_ordering(self, repo):
        # Same provider, multiple ops — ordering by created_at ascending.
        repo.create("first", "elevenlabs", "ep1", {})
        repo.create("second", "elevenlabs", "ep2", {})
        ops = repo.list_by_provider("elevenlabs")
        assert [op.operation_id for op in ops] == ["first", "second"]

    def test_empty_when_no_matches(self, repo):
        repo.create("g", "google", "ep", {})
        assert repo.list_by_provider("elevenlabs") == []


class TestListByEpisode:
    def test_filters_by_episode(self, repo):
        # Google's chunked transcription writes multiple rows for one episode.
        repo.create("op-1", "google", "shared-ep", {"chunk_index": 0})
        repo.create("op-2", "google", "shared-ep", {"chunk_index": 1})
        repo.create("op-3", "google", "other-ep", {"chunk_index": 0})

        rows = repo.list_by_episode("shared-ep")
        assert sorted(op.operation_id for op in rows) == ["op-1", "op-2"]


class TestUpdatePayload:
    def test_update_replaces_payload(self, repo):
        repo.create("u", "elevenlabs", "ep1", {"state": "pending"})
        repo.update_payload("u", {"state": "polling", "attempts": 3})

        op = repo.get("u")
        assert op.payload == {"state": "polling", "attempts": 3}
        # updated_at advances on update
        assert op.updated_at >= op.created_at

    def test_update_missing_is_silent(self, repo):
        # No row exists — should be a no-op, not an error.
        repo.update_payload("ghost", {"state": "wat"})


class TestDelete:
    def test_delete_removes_row(self, repo):
        repo.create("d", "elevenlabs", "ep1", {})
        repo.delete("d")
        assert repo.get("d") is None

    def test_delete_idempotent_on_missing(self, repo):
        # Repeated deletes don't raise — matches the legacy
        # Path.unlink(missing_ok=True) semantics.
        repo.delete("never-existed")
        repo.delete("never-existed")
