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

"""The spec #104 periodic job loop."""

from thestill.core.periodic_runner import PeriodicJob, PeriodicRunner


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_runs_due_jobs_and_survives_failures():
    clock = _Clock()
    ran = []

    def boom():
        raise RuntimeError("boom")

    runner = PeriodicRunner(
        [
            PeriodicJob("fast", 10, lambda: ran.append("fast")),
            PeriodicJob("broken", 10, boom),
            PeriodicJob("slow", 100, lambda: ran.append("slow")),
        ],
        clock=clock,
    )
    assert runner.run_due() == ["fast", "broken", "slow"]
    clock.now = 5.0
    assert runner.run_due() == []
    clock.now = 11.0
    assert runner.run_due() == ["fast", "broken"]
    assert ran == ["fast", "slow", "fast"]


def test_start_and_stop():
    ran = []
    runner = PeriodicRunner([PeriodicJob("once", 3600, lambda: ran.append(1))], tick_seconds=0.01)
    runner.start()
    runner.stop()
    assert ran == [1]
