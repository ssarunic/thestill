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

"""``thestill transcribe`` (ElevenLabs webhooks) waits on stored state.

It polls the episode's pending operation and whether a raw transcript was
written after submission, instead of an in-process tracker, so it finishes
even when another process (an already running ``thestill server``) receives
the callback.
"""

import os
from datetime import datetime, timezone
from types import SimpleNamespace

from thestill.cli import _raw_transcript_written_since, _wait_for_webhook_transcripts
from thestill.models.pending_operation import PendingOperation

_NOW = datetime(2026, 11, 19, tzinfo=timezone.utc)


def _op(episode_id, provider="elevenlabs"):
    return PendingOperation(
        operation_id=f"op-{episode_id}",
        provider=provider,
        episode_id=episode_id,
        payload={},
        created_at=_NOW,
        updated_at=_NOW,
    )


class _World:
    """Pending operations and written transcripts that change as (fake) time passes."""

    def __init__(self, events, pending=("ep-a", "ep-b")):
        # events: {tick: [(episode_id, wrote_transcript)]} — at that tick the
        # episode's callback arrives: its operation is settled and, when
        # ``wrote_transcript``, a new raw transcript is written.
        self.tick = 0
        self.events = events
        self.pending = {eid: "elevenlabs" for eid in pending}
        self.written = set()

    def sleep(self, _seconds):
        self.tick += 1
        for episode_id, wrote in self.events.get(self.tick, []):
            self.pending.pop(episode_id, None)
            if wrote:
                self.written.add(episode_id)

    def list_by_episode(self, episode_id):
        provider = self.pending.get(episode_id)
        return [_op(episode_id, provider)] if provider else []

    def transcript_written(self, episode_id, _since):
        return episode_id in self.written


def _run(world, submitted):
    lines = []
    result = _wait_for_webhook_transcripts(
        pending_ops_repository=SimpleNamespace(list_by_episode=world.list_by_episode),
        submitted=submitted,
        transcript_written=world.transcript_written,
        echo=lines.append,
        sleep=world.sleep,
        poll_seconds=0,
    )
    return result, lines


def test_waits_until_every_episode_is_settled_and_counts_outcomes():
    world = _World({2: [("ep-a", True)], 5: [("ep-b", False)]})  # b's callback had no text
    (delivered, failed), _ = _run(world, {"ep-a": 0.0, "ep-b": 0.0})
    assert (delivered, failed) == (["ep-a"], ["ep-b"])
    assert world.tick == 5


def test_a_written_transcript_settles_even_if_the_operation_lingers():
    # A callback that beat the pending-operation write leaves the row behind;
    # the freshly written transcript still proves delivery.
    world = _World({1: []}, pending=("ep-a",))

    def sleep(_s):
        world.tick += 1
        world.written.add("ep-a")

    world.sleep = sleep
    (delivered, failed), _ = _run(world, {"ep-a": 0.0})
    assert (delivered, failed) == (["ep-a"], [])


def test_nothing_submitted_returns_at_once():
    world = _World({})
    (delivered, failed), _ = _run(world, {})
    assert (delivered, failed) == ([], [])
    assert world.tick == 0


def test_other_providers_operations_do_not_hold_the_wait():
    world = _World({}, pending=())
    world.pending = {"ep-a": "google"}
    world.written = {"ep-a"}
    (delivered, failed), _ = _run(world, {"ep-a": 0.0})
    assert (delivered, failed) == (["ep-a"], [])


class TestRawTranscriptWrittenSince:
    """Delivery is "a raw transcript written after submission", not "the path
    changed": a re-transcription writes the same deterministic path."""

    def _setup(self, tmp_path, raw_path="pod/ep.json"):
        raw_dir = tmp_path / "raw"
        path_manager = SimpleNamespace(raw_transcript_file=lambda rel: raw_dir / rel)
        repository = SimpleNamespace(
            get_episode=lambda eid: (SimpleNamespace(id="pod"), SimpleNamespace(id=eid, raw_transcript_path=raw_path))
        )
        return raw_dir, repository, path_manager

    def test_same_path_rewritten_after_submission_counts(self, tmp_path):
        raw_dir, repository, path_manager = self._setup(tmp_path)
        (raw_dir / "pod").mkdir(parents=True)
        f = raw_dir / "pod" / "ep.json"
        f.write_text("{}")
        os.utime(f, (2_000, 2_000))
        assert _raw_transcript_written_since(repository, path_manager, "ep", 1_000) is True

    def test_older_file_or_missing_file_does_not_count(self, tmp_path):
        raw_dir, repository, path_manager = self._setup(tmp_path)
        assert _raw_transcript_written_since(repository, path_manager, "ep", 1_000) is False  # missing
        (raw_dir / "pod").mkdir(parents=True)
        f = raw_dir / "pod" / "ep.json"
        f.write_text("{}")
        os.utime(f, (500, 500))
        assert _raw_transcript_written_since(repository, path_manager, "ep", 1_000) is False  # stale

    def test_no_raw_path_does_not_count(self, tmp_path):
        _, repository, path_manager = self._setup(tmp_path, raw_path=None)
        assert _raw_transcript_written_since(repository, path_manager, "ep", 0) is False
