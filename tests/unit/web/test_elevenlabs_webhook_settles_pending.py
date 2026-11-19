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

"""An ElevenLabs callback settles the episode's pending operation.

The CLI waits for these rows to disappear, so the signal works whichever
process receives the callback (its own background server, or a ``thestill
server`` that was already running). Before, completion went to an
in-process tracker, and a CLI that reused an existing server waited forever.
"""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from thestill.models.pending_operation import PendingOperation
from thestill.web.routes import webhooks as webhook_route

EPISODE_ID = "ep-1"


_NOW = datetime(2026, 11, 19, tzinfo=timezone.utc)


class _PendingOps:
    def __init__(self, ops):
        self.ops = {op.operation_id: op for op in ops}
        self.deleted = []

    def list_by_episode(self, episode_id):
        return [op for op in self.ops.values() if op.episode_id == episode_id]

    def delete(self, operation_id):
        self.deleted.append(operation_id)
        self.ops.pop(operation_id, None)


def _op(op_id, provider="elevenlabs", episode_id=EPISODE_ID):
    return PendingOperation(
        operation_id=op_id, provider=provider, episode_id=episode_id, payload={}, created_at=_NOW, updated_at=_NOW
    )


def _state(tmp_path, pending_ops):
    state = MagicMock()
    state.pending_ops_repository = pending_ops
    state.repository.get_episode.return_value = (
        SimpleNamespace(id="pod-1"),
        SimpleNamespace(id=EPISODE_ID, raw_transcript_path="pod/ep.json", title="Ep"),
    )
    return state


@pytest.fixture(autouse=True)
def _unsigned_dev_webhooks(monkeypatch, tmp_path):
    monkeypatch.setenv(webhook_route._DEV_ALLOW_UNSIGNED_ENV, "1")
    monkeypatch.setattr(webhook_route, "_get_webhook_secret", lambda state: None)
    monkeypatch.setattr(webhook_route, "_get_require_metadata", lambda state: False)
    monkeypatch.setattr(webhook_route, "_get_webhook_data_dir", lambda state: tmp_path)


def _body(text="hello world"):
    return json.dumps(
        {
            "request_id": "req-9",
            "transcription": {"text": text, "words": []},
            "webhook_metadata": {"episode_id": EPISODE_ID, "podcast_slug": "pod", "episode_slug": "ep"},
        }
    ).encode()


def test_successful_callback_deletes_the_episodes_elevenlabs_operation(tmp_path, monkeypatch):
    pending = _PendingOps([_op("tx-1"), _op("g-1", provider="google")])
    processor = MagicMock()
    processor.return_value.process_elevenlabs_webhook.return_value = object()
    monkeypatch.setattr(webhook_route, "WebhookTranscriptProcessor", processor)

    webhook_route._process_elevenlabs_webhook(_body(), None, _state(tmp_path, pending))

    assert pending.deleted == ["tx-1"]  # the Google operation is left alone


def test_failed_callback_also_settles_the_operation(tmp_path):
    # No transcript text: nothing is saved, but the job is over, so the
    # waiting CLI must stop waiting (and report it as failed).
    pending = _PendingOps([_op("tx-1")])
    webhook_route._process_elevenlabs_webhook(_body(text=""), None, _state(tmp_path, pending))
    assert pending.deleted == ["tx-1"]


def test_no_pending_repository_is_tolerated(tmp_path):
    state = _state(tmp_path, None)
    response = webhook_route._process_elevenlabs_webhook(_body(text=""), None, state)
    assert response["episode_id"] == EPISODE_ID
