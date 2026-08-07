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

"""Resolution of the per-stage handler watchdog config."""

from __future__ import annotations

from thestill.core.queue_manager import TaskStage
from thestill.utils.config import get_stage_watchdog_seconds

ENV = "QUEUE_STAGE_WATCHDOG_SECONDS"


def test_unset_uses_per_stage_defaults(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    wd = get_stage_watchdog_seconds()
    # Raised from 1800 to 5400 on 2026-08-07. The watchdog frees a slot from a
    # handler that will NEVER finish; it is not a performance budget. Because
    # Python cannot kill the thread, abandoning work that would have completed
    # leaks an executor slot — at scale that wedges every stage (see the
    # outage note in ``get_stage_watchdog_seconds``). Budgets must sit above
    # the slowest legitimate run on the slowest deployment host.
    assert wd[TaskStage.CLEAN] == 5400.0
    # CPU-bound stages get more still: embedding and corpus-wide aggregation
    # on a 2-vCPU instance routinely exceed half an hour.
    assert wd[TaskStage.REINDEX] == 7200.0
    # Transcribe is intentionally unbounded (long local-Whisper jobs).
    assert wd[TaskStage.TRANSCRIBE] is None


def test_explicit_zero_disables_everywhere(monkeypatch):
    """Regression: an explicit 0 is the documented global kill switch and must
    NOT fall through to the per-stage defaults."""
    monkeypatch.setenv(ENV, "0")
    wd = get_stage_watchdog_seconds()
    assert set(wd.values()) == {None}


def test_positive_base_overrides_every_stage(monkeypatch):
    monkeypatch.setenv(ENV, "600")
    wd = get_stage_watchdog_seconds()
    assert all(v == 600.0 for v in wd.values())
