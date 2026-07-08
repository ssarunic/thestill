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

"""Spec #40 for Dalston — restart-safe transcription polling.

A server restart mid-poll leaves the Dalston job running server-side while
the local task is reset and retried. These tests pin the resume contract:
the retry reattaches to the in-flight job (no duplicate submission), prunes
terminal/unknown jobs before submitting fresh, and keeps the pending op on
transient errors so a later retry can still reattach.

The Dalston SDK client is faked; the pending-ops repository is the real
SQLite implementation.
"""

from __future__ import annotations

from typing import List, Optional

import pytest
from dalston_sdk import ConnectError, JobStatus, NotFoundError

from thestill.core.dalston_transcriber import DalstonTranscriber
from thestill.models.transcription import TranscribeOptions
from thestill.repositories.sqlite_pending_operations_repository import SqlitePendingOperationsRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository

EPISODE_ID = "ep-1"
AUDIO_URL = "https://example.com/audio/ep-1.mp3"


class _FakeTranscript:
    text = "hello world"
    language = "en"
    segments: list = []


class _FakeJob:
    def __init__(self, job_id: str, status: JobStatus):
        self.id = job_id
        self.status = status
        self.transcript = _FakeTranscript()
        self.error = None
        self.progress = None
        self.current_stage = None


class _FakeDalstonClient:
    """Scripted stand-in for ``dalston_sdk.Dalston``.

    ``get_job_results`` maps job_id → Job or Exception (raised);
    ``wait_result`` is what ``wait_for_completion`` produces. A hook on
    wait lets tests observe repo state while the poll is "in flight".
    """

    def __init__(
        self,
        *,
        get_job_results: Optional[dict] = None,
        wait_result: Optional[_FakeJob] = None,
        wait_error: Optional[Exception] = None,
        new_job_id: str = "job-new",
    ):
        self.get_job_results = get_job_results or {}
        self.wait_result = wait_result
        self.wait_error = wait_error
        self.new_job_id = new_job_id
        self.transcribe_calls: List[dict] = []
        self.waited_job_ids: List[str] = []
        self.on_wait = None

    def transcribe(self, **kwargs):
        self.transcribe_calls.append(kwargs)
        return _FakeJob(self.new_job_id, JobStatus.PENDING)

    def get_job(self, job_id):
        result = self.get_job_results[str(job_id)]
        if isinstance(result, Exception):
            raise result
        return result

    def wait_for_completion(self, job_id, poll_interval=None, on_progress=None):
        self.waited_job_ids.append(str(job_id))
        if self.on_wait is not None:
            self.on_wait()
        if self.wait_error is not None:
            raise self.wait_error
        if self.wait_result is not None:
            return self.wait_result
        return _FakeJob(str(job_id), JobStatus.COMPLETED)


@pytest.fixture
def repo(tmp_path) -> SqlitePendingOperationsRepository:
    db_path = tmp_path / "podcasts.db"
    SqlitePodcastRepository(db_path=str(db_path))
    return SqlitePendingOperationsRepository(db_path=str(db_path))


def _transcriber(repo, client) -> DalstonTranscriber:
    t = DalstonTranscriber(base_url="http://dalston.test", pending_ops_repository=repo)
    t._client = client  # load_model() is a no-op once the client is set
    return t


def _options() -> TranscribeOptions:
    # URL mode skips the local-file existence check — the resume logic
    # under test is identical for both submission modes.
    return TranscribeOptions(language="en", episode_id=EPISODE_ID, audio_url=AUDIO_URL)


def _seed_pending_op(repo, job_id: str) -> None:
    repo.create(
        operation_id=job_id,
        provider="dalston",
        episode_id=EPISODE_ID,
        payload={"provider": "dalston", "job_id": job_id, "episode_id": EPISODE_ID},
    )


class TestFreshSubmit:
    def test_persists_pending_op_while_polling_and_clears_on_success(self, repo):
        client = _FakeDalstonClient(new_job_id="job-1")
        seen_during_poll = {}
        client.on_wait = lambda: seen_during_poll.update(op=repo.get("job-1"))

        result = _transcriber(repo, client).transcribe_audio("unused.wav", options=_options())

        assert result is not None
        assert len(client.transcribe_calls) == 1
        # The op existed while the poll was in flight (restart insurance)…
        assert seen_during_poll["op"] is not None
        assert seen_during_poll["op"].provider == "dalston"
        # …and is cleared once the job reached a terminal state.
        assert repo.get("job-1") is None

    def test_no_repository_still_transcribes(self):
        client = _FakeDalstonClient(new_job_id="job-1")
        t = DalstonTranscriber(base_url="http://dalston.test")
        t._client = client

        result = t.transcribe_audio("unused.wav", options=_options())

        assert result is not None
        assert len(client.transcribe_calls) == 1

    def test_no_episode_id_skips_persistence(self, repo):
        client = _FakeDalstonClient(new_job_id="job-1")
        options = TranscribeOptions(language="en", audio_url=AUDIO_URL)

        result = _transcriber(repo, client).transcribe_audio("unused.wav", options=options)

        assert result is not None
        assert repo.list_by_provider("dalston") == []


class TestRestartResume:
    def test_running_job_is_reattached_not_resubmitted(self, repo):
        _seed_pending_op(repo, "job-1")
        client = _FakeDalstonClient(get_job_results={"job-1": _FakeJob("job-1", JobStatus.RUNNING)})

        result = _transcriber(repo, client).transcribe_audio("unused.wav", options=_options())

        assert result is not None
        assert client.transcribe_calls == []  # the whole point: no duplicate job
        assert client.waited_job_ids == ["job-1"]
        assert repo.get("job-1") is None

    def test_completed_job_result_is_fetched_not_recomputed(self, repo):
        # The poll died after Dalston finished: the retry collects the
        # finished result instead of re-running the transcription.
        _seed_pending_op(repo, "job-1")
        client = _FakeDalstonClient(get_job_results={"job-1": _FakeJob("job-1", JobStatus.COMPLETED)})

        result = _transcriber(repo, client).transcribe_audio("unused.wav", options=_options())

        assert result is not None
        assert client.transcribe_calls == []
        assert repo.get("job-1") is None

    def test_failed_job_is_pruned_and_resubmitted(self, repo):
        _seed_pending_op(repo, "job-old")
        client = _FakeDalstonClient(
            get_job_results={"job-old": _FakeJob("job-old", JobStatus.FAILED)},
            new_job_id="job-new",
        )

        result = _transcriber(repo, client).transcribe_audio("unused.wav", options=_options())

        assert result is not None
        assert len(client.transcribe_calls) == 1
        assert repo.get("job-old") is None
        assert repo.get("job-new") is None  # new op cleared after success

    def test_unknown_job_is_pruned_and_resubmitted(self, repo):
        # Dalston restarted / purged its job store: the persisted id 404s.
        _seed_pending_op(repo, "job-gone")
        client = _FakeDalstonClient(
            get_job_results={"job-gone": NotFoundError("no such job")},
            new_job_id="job-new",
        )

        result = _transcriber(repo, client).transcribe_audio("unused.wav", options=_options())

        assert result is not None
        assert len(client.transcribe_calls) == 1
        assert repo.get("job-gone") is None

    def test_transient_error_keeps_pending_op_for_next_retry(self, repo):
        # Dalston unreachable while verifying the persisted job: the attempt
        # fails (task retry handles it) but the op survives, so the next
        # attempt can still reattach instead of double-submitting.
        _seed_pending_op(repo, "job-1")
        client = _FakeDalstonClient(get_job_results={"job-1": ConnectError("dalston down")})

        with pytest.raises(ConnectError):
            _transcriber(repo, client).transcribe_audio("unused.wav", options=_options())

        assert client.transcribe_calls == []
        assert repo.get("job-1") is not None

    def test_other_providers_ops_are_ignored(self, repo):
        repo.create(
            operation_id="el-1",
            provider="elevenlabs",
            episode_id=EPISODE_ID,
            payload={"provider": "elevenlabs"},
        )
        client = _FakeDalstonClient(new_job_id="job-new")

        result = _transcriber(repo, client).transcribe_audio("unused.wav", options=_options())

        assert result is not None
        assert len(client.transcribe_calls) == 1
        assert repo.get("el-1") is not None  # untouched


class TestProviderCheckMigration:
    def test_old_database_is_widened_and_rows_survive(self, tmp_path):
        """Databases created before 'dalston' was allowed get their provider
        CHECK widened by a table rebuild; existing rows carry over."""
        import sqlite3

        db_path = str(tmp_path / "podcasts.db")
        SqlitePodcastRepository(db_path=db_path)

        # Regress the table to its pre-dalston shape with a seeded row.
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            DROP TABLE pending_transcription_operations;
            CREATE TABLE pending_transcription_operations (
                operation_id    TEXT PRIMARY KEY NOT NULL,
                provider        TEXT NOT NULL,
                episode_id      TEXT NOT NULL,
                payload_json    TEXT NOT NULL,
                created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
                updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
                CHECK (provider IN ('google','elevenlabs'))
            );
            INSERT INTO pending_transcription_operations
                (operation_id, provider, episode_id, payload_json)
                VALUES ('el-1', 'elevenlabs', 'ep-9', '{}');
            """
        )
        conn.close()

        # Re-opening the repository re-runs migrations.
        SqlitePodcastRepository(db_path=db_path)

        repo = SqlitePendingOperationsRepository(db_path=db_path)
        assert repo.get("el-1") is not None  # survived the rebuild
        repo.create(operation_id="dl-1", provider="dalston", episode_id="ep-9", payload={})
        assert repo.get("dl-1") is not None  # widened CHECK accepts dalston
