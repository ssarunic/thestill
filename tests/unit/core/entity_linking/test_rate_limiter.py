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

"""Spec #81 — outbound Wikidata pacing."""

import pytest

from thestill.core.entity_linking.rate_limiter import (
    WikidataRateLimiter,
    get_shared_rate_limiter,
    reset_shared_rate_limiter,
)
from thestill.core.entity_linking.types import surface_key


class FakeTime:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(round(seconds, 6))
        self.now += seconds


def _limiter(max_rps, fake):
    return WikidataRateLimiter(max_rps, clock=fake.clock, sleep=fake.sleep)


def test_first_request_is_not_delayed():
    fake = FakeTime()
    _limiter(5, fake).acquire()
    assert fake.sleeps == []


def test_requests_are_spaced_at_the_configured_rate():
    fake = FakeTime()
    limiter = _limiter(5, fake)
    for _ in range(3):
        limiter.acquire()
    assert fake.sleeps == [0.2, 0.2]


def test_idle_time_is_not_banked_into_a_burst():
    fake = FakeTime()
    limiter = _limiter(5, fake)
    limiter.acquire()
    fake.now += 60
    limiter.acquire()
    limiter.acquire()
    assert fake.sleeps == [0.2]


def test_hold_off_delays_every_later_caller():
    fake = FakeTime()
    limiter = _limiter(5, fake)
    limiter.hold_off(30)
    limiter.acquire()
    limiter.acquire()
    assert fake.sleeps == [30, 0.2]


def test_hold_off_never_shortens_an_existing_wait():
    fake = FakeTime()
    limiter = _limiter(5, fake)
    limiter.hold_off(30)
    limiter.hold_off(1)
    limiter.acquire()
    assert fake.sleeps == [30]


def test_a_caller_already_waiting_honours_a_hold_off_that_arrives_meanwhile():
    """Another worker gets a 429 while this one sleeps on its reserved slot."""
    fake = FakeTime()
    limiter = _limiter(5, fake)
    limiter.acquire()  # takes the slot at 100.0; the next is 100.2

    def sleep_and_get_a_429(seconds):
        fake.sleeps.append(round(seconds, 6))
        fake.now += seconds
        if len(fake.sleeps) == 1:
            limiter.hold_off(30)  # arrives at 100.2, so nobody sends before 130.2

    limiter._sleep = sleep_and_get_a_429
    limiter.acquire()
    assert fake.now >= 130.2
    assert fake.sleeps[0] == 0.2 and len(fake.sleeps) == 2


def test_rejects_a_non_positive_rate():
    with pytest.raises(ValueError):
        WikidataRateLimiter(0)


def test_shared_limiter_is_one_object_per_process():
    reset_shared_rate_limiter()
    try:
        assert get_shared_rate_limiter(5) is get_shared_rate_limiter(50)
    finally:
        reset_shared_rate_limiter()


@pytest.mark.parametrize(
    "spoken, key",
    [("OpenAI", "openai"), ("  Dario   Amodei ", "dario amodei"), ("ŠKODA", "škoda"), ("Straße", "strasse")],
)
def test_surface_key_folds_case_and_whitespace_in_python(spoken, key):
    assert surface_key(spoken) == key
