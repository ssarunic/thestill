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

It polls the episode's pending operation and raw transcript instead of an
in-process tracker, so it finishes even when another process (an already
running ``thestill server``) receives the callback.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from thestill.cli import _wait_for_webhook_transcripts
from thestill.models.pending_operation import PendingOperation

_NOW = datetime(2026, 11, 19, tzinfo=timezone.utc)


class _World:
    """Pending operations and episodes that change as (fake) time passes."""

    def __init__(self, events):
        # events: {tick: [(episode_id, raw_path_or_None)]} — at that tick the
        # episode's operation is settled and its raw transcript set.
        self.tick = 0
        self.events = events
        self.pending = {"ep-a": "elevenlabs", "ep-b": "elevenlabs"}
        self.raw = {"ep-a": None, "ep-b": None}

    def sleep(self, _seconds):
        self.tick += 1
        for episode_id, raw_path in self.events.get(self.tick, []):
            self.pending.pop(episode_id, None)
            if raw_path:
                self.raw[episode_id] = raw_path

    def list_by_episode(self, episode_id):
        provider = self.pending.get(episode_id)
        return (
            [
                PendingOperation(
                    operation_id=f"op-{episode_id}",
                    provider=provider,
                    episode_id=episode_id,
                    payload={},
                    created_at=_NOW,
                    updated_at=_NOW,
                )
            ]
            if provider
            else []
        )

    def get_episode(self, episode_id):
        return (SimpleNamespace(id="pod"), SimpleNamespace(id=episode_id, raw_transcript_path=self.raw[episode_id]))


def _run(world, submitted):
    lines = []
    result = _wait_for_webhook_transcripts(
        repository=SimpleNamespace(get_episode=world.get_episode),
        pending_ops_repository=SimpleNamespace(list_by_episode=world.list_by_episode),
        submitted=submitted,
        echo=lines.append,
        sleep=world.sleep,
        poll_seconds=0,
    )
    return result, lines


def test_waits_until_every_episode_is_settled_and_counts_outcomes():
    world = _World({2: [("ep-a", "pod/a.json")], 5: [("ep-b", None)]})  # b's callback had no text
    (delivered, failed), _ = _run(world, {"ep-a": None, "ep-b": None})
    assert (delivered, failed) == (["ep-a"], ["ep-b"])
    assert world.tick == 5


def test_a_changed_raw_transcript_counts_even_if_the_operation_lingers():
    # A callback that beat the pending-operation write leaves the row behind;
    # the new raw transcript still proves the episode was delivered.
    world = _World({1: []})
    world.pending = {"ep-a": "elevenlabs"}
    world.raw = {"ep-a": "pod/old.json"}

    def sleep(_s):
        world.tick += 1
        world.raw["ep-a"] = "pod/new.json"

    delivered, failed = _wait_for_webhook_transcripts(
        repository=SimpleNamespace(get_episode=world.get_episode),
        pending_ops_repository=SimpleNamespace(list_by_episode=world.list_by_episode),
        submitted={"ep-a": "pod/old.json"},
        echo=lambda _l: None,
        sleep=sleep,
        poll_seconds=0,
    )
    assert (delivered, failed) == (["ep-a"], [])


def test_nothing_submitted_returns_at_once():
    world = _World({})
    (delivered, failed), lines = _run(world, {})
    assert (delivered, failed) == ([], [])
    assert world.tick == 0


def test_other_providers_operations_do_not_hold_the_wait():
    world = _World({})
    world.pending = {"ep-a": "google"}
    world.raw = {"ep-a": "pod/a.json"}
    (delivered, failed), _ = _run(world, {"ep-a": None})
    assert (delivered, failed) == (["ep-a"], [])
