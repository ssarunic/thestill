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

"""Spec #104 Phase 3 — operator alerts and the daily digest."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from thestill.services.abuse.actor import Actor
from thestill.services.abuse.alert_channels import AlertMessage, EmailChannel, WebhookChannel, deliver
from thestill.services.abuse.alerts import AlertService, AlertSignals

from .conftest import NOW, make_world


class _Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


class _Signals:
    """Canned readings; tests set what a rule should see."""

    def __init__(self):
        self.rate = {}
        self.signups = []
        self.by_kind = {}
        self.pending = 0
        self.audio_minutes = 0

    def rate_limit_hits_by_user(self):
        return self.rate

    def counts_by_user(self, kind, since):
        return self.by_kind.get(kind, {})

    def signups_since(self, since):
        return self.signups

    def pending_tasks(self):
        return self.pending

    def queued_audio_minutes(self, since):
        return self.audio_minutes


class _Inbox:
    name = "inbox"

    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)


class _Broken:
    name = "broken"

    def send(self, message):
        raise ConnectionError("smtp down")


@pytest.fixture
def setup(tmp_path):
    clock = _Clock()
    world = make_world(tmp_path, clock=clock, public_base_url="https://thestill.test")
    signals, inbox = _Signals(), _Inbox()
    service = AlertService(
        signals=signals,
        channels=[inbox, _Broken()],
        claims=world.repos.settings,
        settings=world.abuse.settings,
        users=world.repos.user,
        config=world.config,
        clock=clock,
    )
    return SimpleNamespace(world=world, clock=clock, signals=signals, inbox=inbox, service=service)


def test_each_alert_fires_once_and_holds_for_an_hour(setup):
    user = setup.world.add_user("spammer@example.com")
    setup.signals.by_kind = {"quota_exceeded": {user.id: 7}}
    setup.signals.pending = 501

    assert sorted(setup.service.tick()) == sorted([f"quota_pressure.{user.id}", "queue_backlog"])
    quota_alert = next(m for m in setup.inbox.sent if m.kind == "quota_pressure")
    assert quota_alert.title == "spammer@example.com hit a quota 7 times in the last hour"
    assert f"https://thestill.test/admin/users/{user.id}" in quota_alert.text
    assert quota_alert.details == {"user_id": user.id, "count": 7}  # no email in machine details

    setup.clock.now = NOW + timedelta(minutes=55)
    assert setup.service.tick() == []
    setup.clock.now = NOW + timedelta(hours=1, minutes=1)
    assert len(setup.service.tick()) == 2


def test_per_user_alerts_are_keyed_by_user(setup):
    a, b = setup.world.add_user("a@example.com"), setup.world.add_user("b@example.com")
    setup.signals.rate = {a.id: 60}
    assert setup.service.tick() == [f"rate_limit_pressure.{a.id}"]
    setup.signals.rate = {a.id: 60, b.id: 51}
    assert setup.service.tick() == [f"rate_limit_pressure.{b.id}"]


def test_thresholds_come_from_env_with_runtime_overrides(setup):
    setup.signals.signups = [object()] * 10
    setup.signals.by_kind = {"feed_introduced": {"u1": 20, "u2": 10}}
    assert sorted(setup.service.tick()) == ["feed_spike", "signup_spike"]

    setup.world.abuse.settings.set("alerts", {"signup_spike": 50, "feed_spike": 0}, actor=Actor.cli())
    setup.clock.now = NOW + timedelta(hours=2)
    assert setup.service.tick() == []  # raised past, and 0 turns it off


def test_spend_breaker_pauses_new_feeds_only_when_configured(setup):
    settings = setup.world.abuse.settings
    setup.signals.audio_minutes = 900
    settings.set("alerts", {"spend_breaker_minutes": 600}, actor=Actor.cli())
    assert setup.service.tick() == ["spend_breaker"]
    assert settings.get("new_feeds_enabled") is True
    assert "Pause new feeds" in setup.inbox.sent[-1].text

    settings.set("alerts", {"spend_breaker_minutes": 600, "spend_breaker_action": "pause_new_feeds"}, actor=Actor.cli())
    setup.clock.now = NOW + timedelta(hours=1, minutes=1)
    assert setup.service.tick() == ["spend_breaker"]
    assert settings.get("new_feeds_enabled") is False
    assert "New feeds are now paused" in setup.inbox.sent[-1].text
    entry = setup.world.repos.audit_log.list(action="setting.set")[0]
    assert entry.actor == "system" and entry.details["actor_name"] == "spend_breaker"


def test_a_broken_channel_never_stops_the_others(setup):
    results = setup.service.send_test()
    assert results == {"inbox": True, "broken": False}
    assert setup.inbox.sent[0].kind == "test"


def test_webhook_payload_and_timeout():
    calls = []
    channel = WebhookChannel(
        "https://ntfy.example/thestill",
        post=lambda url, **kw: calls.append((url, kw)) or SimpleNamespace(status_code=200),
    )
    assert deliver([channel], AlertMessage("feed_spike", "t", "body", {"feeds": 31})) == {"webhook": True}
    url, kwargs = calls[0]
    assert url == "https://ntfy.example/thestill"
    assert kwargs["json"] == {
        "text": "body",  # Slack
        "content": "body",  # Discord
        "title": "t",
        "alert": "feed_spike",
        "details": {"feeds": 31},
    }
    assert kwargs["timeout"] == 5.0


def test_digest_is_sent_once_at_the_hour_and_skips_quiet_days(setup):
    config = setup.world.config
    # The digest hour is server-local; derive it from the test clock.
    local_hour = NOW.astimezone().hour
    config.admin_digest_hour = (local_hour + 1) % 24
    assert setup.service.maybe_send_digest() is False  # not the hour yet

    config.admin_digest_hour = local_hour
    assert setup.service.maybe_send_digest() is False  # quiet day: skipped, slot used
    config.admin_digest_always = True
    assert setup.service.maybe_send_digest() is False  # already claimed today

    setup.clock.now = NOW + timedelta(days=1)
    user = setup.world.add_user("new@example.com")
    setup.signals.signups = [user]
    setup.signals.by_kind = {"quota_exceeded": {user.id: 2}, "feed_introduced": {user.id: 3}}
    assert setup.service.maybe_send_digest() is True
    digest = next(m for m in setup.inbox.sent if m.kind == "digest")
    assert digest.title == "Daily digest: 1 new accounts, 2 quota hits"
    assert "new@example.com: 3" in digest.text
    assert "Signup mode: open" in digest.text


def test_digest_lists_alerts_sent_and_switches_off_default(setup):
    setup.signals.pending = 10_000
    setup.service.tick()
    setup.world.abuse.settings.set("read_only", True, actor=Actor.cli())
    digest = setup.service.build_digest()
    assert "10000 pipeline tasks are pending" in digest.text
    assert "read_only=True" in digest.text


def test_digest_always_sends_on_a_quiet_day(setup):
    setup.world.config.admin_digest_hour = NOW.astimezone().hour
    setup.world.config.admin_digest_always = True
    assert setup.service.maybe_send_digest() is True
    assert setup.inbox.sent[-1].kind == "digest"


def test_breaker_says_when_new_feeds_are_already_paused(setup):
    settings = setup.world.abuse.settings
    settings.set("new_feeds_enabled", False, actor=Actor.cli())
    settings.set("alerts", {"spend_breaker_minutes": 10, "spend_breaker_action": "pause_new_feeds"}, actor=Actor.cli())
    setup.signals.audio_minutes = 11
    setup.service.tick()
    assert "New feeds are already paused" in setup.inbox.sent[-1].text


def test_threshold_trips_at_the_reading(setup):
    setup.signals.pending = 500  # == ALERT_QUEUE_BACKLOG
    assert setup.service.tick() == ["queue_backlog"]


def test_one_failing_rule_does_not_stop_the_others(setup):
    def broken(since):
        raise RuntimeError("database blinked")

    setup.signals.signups_since = broken
    setup.signals.pending = 9999
    assert setup.service.tick() == ["queue_backlog"]


def test_junk_durations_count_as_zero(tmp_path):
    queue = SimpleNamespace(queued_audio_durations=lambda since: ["inf", "1:00:00", None, "nonsense", "120"])
    signals = AlertSignals(usage=None, directory=None, queue=queue, rate_limit_hits=dict)
    assert signals.queued_audio_minutes(NOW) == 62


def test_webhook_errors_never_carry_the_url():
    secret = "https://hooks.slack.com/services/T0/B0/very-secret"

    def refuse(url, **_):
        raise httpx.ConnectError(f"cannot reach {url}")

    for post in (refuse, lambda url, **_: SimpleNamespace(status_code=404)):
        channel = WebhookChannel(secret, post=post)
        with pytest.raises(Exception) as exc:
            channel.send(AlertMessage("test", "t", "x"))
        assert "very-secret" not in str(exc.value)


def test_email_tries_every_recipient():
    sent = []

    def send(*, to, **_):
        if to == "bad@example.com":
            raise ConnectionError("rejected")
        sent.append(to)

    channel = EmailChannel(SimpleNamespace(send=send), ["bad@example.com", "ops@example.com"])
    assert deliver([channel], AlertMessage("test", "t", "x")) == {"email": False}
    assert sent == ["ops@example.com"]
