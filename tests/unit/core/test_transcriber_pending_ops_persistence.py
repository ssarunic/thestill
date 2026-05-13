# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Spec #40 — verify each transcriber routes pending-op persistence
through SqlitePendingOperationsRepository instead of JSON files.

Only the persistence methods are exercised; nothing here talks to the
actual ElevenLabs / Google APIs. The transcribers are constructed in
their minimum-viable shape (no api_key, no project_id required for the
state-only flow).
"""

from __future__ import annotations

import pytest

from thestill.models.podcast import TranscriptionOperation, TranscriptionOperationState
from thestill.repositories.sqlite_pending_operations_repository import SqlitePendingOperationsRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository


@pytest.fixture
def repo(tmp_path) -> SqlitePendingOperationsRepository:
    db_path = tmp_path / "podcasts.db"
    SqlitePodcastRepository(db_path=str(db_path))
    return SqlitePendingOperationsRepository(db_path=str(db_path))


# --- ElevenLabs ---------------------------------------------------------------


class TestElevenLabsPersistence:
    def _transcriber(self, repo):
        from thestill.core.elevenlabs_transcriber import ElevenLabsTranscriber

        # api_key is required at construction; pass a stub. None of the
        # network paths run in this test — only the state methods.
        return ElevenLabsTranscriber(
            api_key="stub-key",
            pending_ops_repository=repo,
        )

    def test_save_creates_row(self, repo):
        t = self._transcriber(repo)
        t._save_pending_operation(
            transcription_id="el-job-1",
            audio_path="downsampled_audio/foo/ep.wav",
            language="en",
            episode_id="ep-1",
            podcast_slug="foo",
            episode_slug="ep-one",
        )
        op = repo.get("el-job-1")
        assert op is not None
        assert op.provider == "elevenlabs"
        assert op.episode_id == "ep-1"
        assert op.payload["transcription_id"] == "el-job-1"
        assert op.payload["audio_path"] == "downsampled_audio/foo/ep.wav"

    def test_remove_deletes_row(self, repo):
        t = self._transcriber(repo)
        t._save_pending_operation("x", "a", "en", "ep-1", "p", "e")
        t._remove_pending_operation("x")
        assert repo.get("x") is None

    def test_list_pending_operations_returns_payloads(self, repo):
        t = self._transcriber(repo)
        t._save_pending_operation("a", "/a", "en", "ep-a", "p", "ea")
        t._save_pending_operation("b", "/b", "en", "ep-b", "p", "eb")

        ops = t.list_pending_operations()
        assert sorted(o["transcription_id"] for o in ops) == ["a", "b"]
        # The dict shape matches the legacy JSON-file return shape so
        # external consumers (logs, dashboards) keep working.
        assert "audio_path" in ops[0]
        assert "language" in ops[0]

    def test_no_repository_means_silent_no_op(self):
        from thestill.core.elevenlabs_transcriber import ElevenLabsTranscriber

        t = ElevenLabsTranscriber(api_key="stub-key", pending_ops_repository=None)
        # Save + remove + list must all be no-ops; tests / legacy callers
        # that haven't been threaded yet keep working unchanged.
        t._save_pending_operation("x", "/a", "en", "ep", "p", "e")
        t._remove_pending_operation("x")
        assert t.list_pending_operations() == []


# --- Google -------------------------------------------------------------------


class TestGooglePersistence:
    def _transcriber(self, repo):
        from thestill.core.google_transcriber import GoogleCloudTranscriber

        # ``_initialize_clients`` fails without real credentials. We don't
        # exercise it here — just the state methods. Construct the bare
        # object via ``__new__`` and set the fields the state methods read.
        t = GoogleCloudTranscriber.__new__(GoogleCloudTranscriber)
        t.pending_ops_repository = repo
        return t

    def _make_op(self, op_id: str, episode_id: str = "ep-1") -> TranscriptionOperation:
        return TranscriptionOperation(
            operation_id=op_id,
            operation_name=f"projects/p/locations/eu/operations/{op_id}",
            episode_id=episode_id,
            podcast_slug="foo",
            episode_slug="ep-one",
            audio_gcs_uri=f"gs://bucket/{op_id}.wav",
            output_gcs_uri="gs://bucket/out/",
            language="en",
            state=TranscriptionOperationState.PENDING,
        )

    def test_save_creates_row(self, repo):
        t = self._transcriber(repo)
        t._save_operation(self._make_op("g-1"))

        op = repo.get("g-1")
        assert op is not None
        assert op.provider == "google"
        assert op.payload["audio_gcs_uri"] == "gs://bucket/g-1.wav"
        assert op.payload["state"] == "pending"

    def test_load_rehydrates_model(self, repo):
        t = self._transcriber(repo)
        original = self._make_op("g-2", episode_id="ep-rh")
        t._save_operation(original)

        loaded = t._load_operation("g-2")
        assert loaded is not None
        assert loaded.operation_id == "g-2"
        assert loaded.episode_id == "ep-rh"
        assert loaded.audio_gcs_uri == "gs://bucket/g-2.wav"
        assert loaded.state == TranscriptionOperationState.PENDING

    def test_delete_removes_row_and_is_idempotent(self, repo):
        t = self._transcriber(repo)
        t._save_operation(self._make_op("g-3"))
        t._delete_operation("g-3")
        assert repo.get("g-3") is None
        # Second delete must not raise.
        t._delete_operation("g-3")

    def test_list_pending_filters_by_state(self, repo):
        t = self._transcriber(repo)
        pending = self._make_op("g-pending")
        completed = self._make_op("g-done").model_copy(update={"state": TranscriptionOperationState.COMPLETED})
        t._save_operation(pending)
        t._save_operation(completed)

        ops = t.list_pending_operations()
        ids = [o.operation_id for o in ops]
        assert ids == ["g-pending"]

    def test_no_repository_means_silent_no_op(self):
        from thestill.core.google_transcriber import GoogleCloudTranscriber

        t = GoogleCloudTranscriber.__new__(GoogleCloudTranscriber)
        t.pending_ops_repository = None
        t._save_operation(
            TranscriptionOperation(
                operation_id="x",
                operation_name="x",
                episode_id="ep",
                podcast_slug="p",
                episode_slug="e",
                audio_gcs_uri="gs://b/a.wav",
                output_gcs_uri="gs://b/o/",
                language="en",
            )
        )
        assert t._load_operation("x") is None
        t._delete_operation("x")
        assert t.list_pending_operations() == []
