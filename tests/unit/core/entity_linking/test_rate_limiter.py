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
