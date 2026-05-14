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


# --- Dalston ------------------------------------------------------------------


class TestDalstonPersistence:
    """Restart-resume support — see ``DalstonTranscriber._save_pending_operation``.

    Dalston jobs survive a thestill restart because they run on the Dalston
    server. Persisting the ``job_id`` lets a follow-up transcribe attempt
    for the same episode re-poll the existing job instead of submitting a
    duplicate that the original watcher abandoned.
    """

    def _transcriber(self, repo):
        from thestill.core.dalston_transcriber import DalstonTranscriber

        # Avoid the SDK import in ``load_model`` — only state methods run.
        t = DalstonTranscriber.__new__(DalstonTranscriber)
        t.pending_ops_repository = repo
        t.base_url = "http://localhost:8000"
        return t

    @staticmethod
    def _save(t, job_id: str, episode_id: str | None = "ep-1") -> None:
        t._save_pending_operation(
            job_id=job_id,
            audio_path=f"/{job_id}.wav",
            language="en",
            episode_id=episode_id,
            podcast_slug="p",
            episode_slug="e",
            audio_url=None,
        )

    def test_save_creates_row_with_job_id_keyed_by_episode(self, repo):
        t = self._transcriber(repo)
        t._save_pending_operation(
            job_id="dal-job-1",
            audio_path="downsampled_audio/foo/ep.wav",
            language="en",
            episode_id="ep-1",
            podcast_slug="foo",
            episode_slug="ep-one",
            audio_url=None,
        )
        op = repo.get("dal-job-1")
        assert op is not None
        assert op.provider == "dalston"
        assert op.episode_id == "ep-1"
        assert op.payload["job_id"] == "dal-job-1"
        assert op.payload["base_url"] == "http://localhost:8000"

    def test_remove_deletes_row(self, repo):
        t = self._transcriber(repo)
        self._save(t, "dal-1")
        t._remove_pending_operation("dal-1")
        assert repo.get("dal-1") is None

    def test_find_pending_job_id_returns_most_recent(self, repo):
        t = self._transcriber(repo)
        self._save(t, "dal-old")
        self._save(t, "dal-new")
        # ``list_by_episode`` orders oldest-first; resume picks the newest
        # so we don't latch onto a job that may already have been cleaned up.
        assert t._find_pending_job_id("ep-1") == "dal-new"

    def test_find_pending_job_id_returns_none_when_no_pending(self, repo):
        t = self._transcriber(repo)
        assert t._find_pending_job_id("ep-unknown") is None

    def test_find_pending_job_id_returns_none_without_episode_id(self, repo):
        # CLI / test paths that don't thread an episode_id through.
        t = self._transcriber(repo)
        self._save(t, "dal-x")
        assert t._find_pending_job_id(None) is None

    def test_find_pending_job_id_ignores_other_providers(self, repo):
        t = self._transcriber(repo)
        # A foreign-provider row keyed to the same episode must not be
        # returned as a Dalston resume candidate.
        repo.create(
            operation_id="el-x",
            provider="elevenlabs",
            episode_id="ep-mixed",
            payload={"transcription_id": "el-x"},
        )
        assert t._find_pending_job_id("ep-mixed") is None

    def test_list_pending_operations_returns_payloads(self, repo):
        t = self._transcriber(repo)
        self._save(t, "a", episode_id="ep-a")
        self._save(t, "b", episode_id="ep-b")
        ops = t.list_pending_operations()
        assert sorted(o["job_id"] for o in ops) == ["a", "b"]
        assert ops[0]["provider"] == "dalston"

    def test_no_repository_means_silent_no_op(self):
        from thestill.core.dalston_transcriber import DalstonTranscriber

        t = DalstonTranscriber.__new__(DalstonTranscriber)
        t.pending_ops_repository = None
        t.base_url = "http://localhost:8000"
        # Save + remove + find + list must all be no-ops; legacy callers
        # that haven't been threaded yet keep working unchanged.
        self._save(t, "x", episode_id="ep")
        t._remove_pending_operation("x")
        assert t._find_pending_job_id("ep") is None
        assert t.list_pending_operations() == []

    def test_save_skipped_when_episode_id_missing(self, repo):
        # Without an episode_id we couldn't find the row again at resume
        # time, so persistence is a no-op rather than orphaning a row.
        t = self._transcriber(repo)
        self._save(t, "dal-no-ep", episode_id=None)
        assert repo.get("dal-no-ep") is None

    def test_save_swallows_duplicate_operation_id(self, repo):
        # Duplicate operation_id is a caller bug (see
        # ``SqlitePendingOperationsRepository.create`` docstring). The handler
        # branches on IntegrityError to log at ERROR rather than propagate,
        # so the live polling loop continues even when persistence collides.
        from unittest.mock import patch

        t = self._transcriber(repo)
        self._save(t, "dal-dup")
        with patch("thestill.core.dalston_transcriber.logger") as mock_logger:
            self._save(t, "dal-dup")  # must not raise
            mock_logger.error.assert_called_once()
            assert "already exists" in mock_logger.error.call_args.args[0]


class TestDalstonResumeBranch:
    """Verify ``transcribe_audio`` resumes an existing job instead of resubmitting.

    The bug this guards against: pre-fix, a server restart while 3-5 Dalston
    jobs were in flight produced an equal number of duplicate jobs on the
    next pipeline trigger. The originals would finish unwatched and their
    transcripts would be discarded.
    """

    def test_resumes_existing_job_when_pending_op_present(self, repo, tmp_path):
        from unittest.mock import MagicMock

        from thestill.core.dalston_transcriber import DalstonTranscriber
        from thestill.models.transcription import TranscribeOptions

        # Pre-populate a pending op as if a previous run had submitted but
        # not yet polled to completion.
        pre_existing_job_id = "dal-resume-job"
        episode_id = "ep-resume"
        repo.create(
            operation_id=pre_existing_job_id,
            provider="dalston",
            episode_id=episode_id,
            payload={
                "provider": "dalston",
                "job_id": pre_existing_job_id,
                "audio_path": "downsampled/ep.wav",
                "language": "en",
                "episode_id": episode_id,
                "podcast_slug": "p",
                "episode_slug": "e",
                "audio_url": "https://example.com/audio.mp3",
                "base_url": "http://localhost:8000",
            },
        )

        t = DalstonTranscriber.__new__(DalstonTranscriber)
        t.pending_ops_repository = repo
        t.base_url = "http://localhost:8000"
        t.api_key = None
        t.model = None
        t.timeout = 120.0
        t.enable_diarization = True
        t.num_speakers = None
        t.language = None
        t.path_manager = None

        # Mocked Dalston client: ``transcribe`` MUST NOT be called when an
        # in-flight job already exists for this episode. ``wait_for_completion``
        # is called with the pre-existing job id.
        mock_client = MagicMock()
        mock_client.transcribe.side_effect = AssertionError(
            "transcribe() was called despite a pending op for this episode"
        )
        completed_job = MagicMock()
        completed_job.id = pre_existing_job_id
        completed_job.transcript.text = "hello world"
        completed_job.transcript.language = "en"
        completed_job.transcript.segments = []
        mock_client.wait_for_completion.return_value = completed_job
        t._client = mock_client

        # ``load_model`` would try to import the real SDK; stub it.
        t.load_model = lambda: None  # type: ignore[assignment]

        options = TranscribeOptions(
            language="en",
            episode_id=episode_id,
            podcast_slug="p",
            episode_slug="e",
            audio_url="https://example.com/audio.mp3",
        )

        result = t.transcribe_audio(audio_path="downsampled/ep.wav", options=options)
        assert result is not None
        # The resume path polled the pre-existing job id, not a fresh one.
        mock_client.wait_for_completion.assert_called_once()
        assert mock_client.wait_for_completion.call_args.args[0] == pre_existing_job_id
        mock_client.transcribe.assert_not_called()
        # On success the pending row is cleared so the next attempt submits fresh.
        assert repo.get(pre_existing_job_id) is None
