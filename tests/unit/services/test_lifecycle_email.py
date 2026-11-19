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

"""Spec #118 — the lifecycle planner, delivery pass and renderer."""

from __future__ import annotations

import re
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from urllib.parse import unquote

import pytest

from thestill.models.user_message import LifecycleFacts, MessageStatus, UserMessage
from thestill.services.lifecycle.messages import SEQUENCE
from thestill.services.lifecycle.renderer import LifecycleEmailRenderer
from thestill.services.lifecycle.service import LifecycleEmailService
from thestill.utils.unsubscribe_token import TIPS_PURPOSE, verify_unsubscribe_token

SECRET = "s" * 32
# 12:00 UTC is inside the 09–19 window for a UTC reader.
NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)


def facts(**overrides) -> LifecycleFacts:
    base = LifecycleFacts(
        user_id="u-1",
        email="reader@example.com",
        name="Alex Reader",
        created_at=NOW - timedelta(hours=30),
    )
    return replace(base, **overrides)


class FakeRepo:
    """In-memory ledger + candidate facts, with the repository's rules."""

    def __init__(self, candidates: List[LifecycleFacts]):
        self.facts: Dict[str, LifecycleFacts] = {f.user_id: f for f in candidates}
        self.rows: Dict[str, UserMessage] = {}
        self.pages = 0

    def candidates(self, *, created_from, created_to, channel, limit, user_id=None, after=None):
        self.pages += 1
        out = sorted(
            (f for f in self.facts.values() if created_from <= f.created_at <= created_to),
            key=lambda f: (f.created_at, f.user_id),
        )
        if user_id is not None:
            out = [f for f in out if f.user_id == user_id]
        if after is not None:
            out = [f for f in out if (f.created_at, f.user_id) > after]
        result = []
        for f in out:
            mine = [r for r in self.rows.values() if r.user_id == f.user_id]
            live = [r.sent_at or r.created_at for r in mine if r.status in ("pending", "sending", "sent")]
            result.append(
                replace(
                    f,
                    message_keys=frozenset(r.message_key for r in mine),
                    last_message_at=max(live) if live else None,
                )
            )
        return result[:limit]

    def ensure_pending(self, user_id, message_key, channel, *, now):
        if any(r.user_id == user_id and r.message_key == message_key for r in self.rows.values()):
            return False
        row = UserMessage(
            id=str(uuid.uuid4()), user_id=user_id, message_key=message_key, next_attempt_at=now, created_at=now
        )
        self.rows[row.id] = row
        return True

    def get(self, message_id):
        return self.rows.get(message_id)

    def due(self, now, *, limit):
        return [
            r.model_copy()
            for r in self.rows.values()
            if r.status in ("pending", "sending") and r.next_attempt_at and r.next_attempt_at <= now
        ][:limit]

    def claim(self, message_id, *, now, lease_seconds):
        row = self.rows[message_id]
        if row.status not in ("pending", "sending") or not row.next_attempt_at or row.next_attempt_at > now:
            return False
        row.status = MessageStatus.SENDING
        row.next_attempt_at = now + timedelta(seconds=lease_seconds)
        row.attempts += 1
        return True

    def mark_sent(self, message_id, *, sent_at):
        row = self.rows[message_id]
        row.status, row.sent_at, row.next_attempt_at = MessageStatus.SENT, sent_at, None

    def mark_retry(self, message_id, *, attempts, next_attempt_at, error):
        row = self.rows[message_id]
        row.status, row.attempts, row.next_attempt_at, row.last_error = (
            MessageStatus.PENDING,
            attempts,
            next_attempt_at,
            error,
        )

    def mark_failed(self, message_id, *, attempts, error):
        row = self.rows[message_id]
        row.status, row.attempts, row.next_attempt_at, row.last_error = MessageStatus.FAILED, attempts, None, error

    def mark_skipped(self, message_id, *, reason):
        row = self.rows[message_id]
        row.status, row.next_attempt_at, row.last_error = MessageStatus.SKIPPED, None, reason


class FakeSender:
    def __init__(self, fail: Optional[Exception] = None):
        self.sent: List[dict] = []
        self.fail = fail

    def send(self, **kwargs):
        if self.fail:
            raise self.fail
        self.sent.append(kwargs)


def make_service(repo, sender=None, **kwargs) -> LifecycleEmailService:
    renderer = LifecycleEmailRenderer(public_base_url="https://thestill.test", secret=SECRET)
    return LifecycleEmailService(repo, renderer, sender or FakeSender(), **kwargs)


# -- next_message (pure) -----------------------------------------------------


class TestNextMessage:
    def setup_method(self):
        self.service = make_service(FakeRepo([]))

    def pick(self, f: LifecycleFacts, now: datetime = NOW):
        message, reason = self.service.next_message(f, now)
        return (message.key if message else None), reason

    def test_nothing_before_the_first_day(self):
        assert self.pick(facts(created_at=NOW - timedelta(hours=5))) == (None, "nothing due yet")

    def test_welcome_on_day_one(self):
        assert self.pick(facts()) == ("welcome", "due")

    def test_expired_welcome_is_skipped_for_the_next_due_message(self):
        # Day 4, no welcome sent (an outage): the welcome expired at day 3,
        # the follow nudge is due.
        assert self.pick(facts(created_at=NOW - timedelta(days=4))) == ("follow_nudge", "due")

    def test_condition_that_no_longer_holds_is_passed_over(self):
        f = facts(created_at=NOW - timedelta(days=4), follow_count=1, message_keys=frozenset({"welcome"}))
        assert self.pick(f) == (None, "nothing due yet")  # nudge doesn't apply; connector not due

    def test_connector_tip_only_without_a_connector(self):
        base = dict(created_at=NOW - timedelta(days=8), message_keys=frozenset({"welcome", "follow_nudge"}))
        assert self.pick(facts(**base)) == ("claude_connector", "due")
        assert self.pick(facts(**base, connector_connected=True)) == (None, "nothing due yet")

    def test_search_tip_needs_a_follow(self):
        sent = frozenset({"welcome", "follow_nudge", "claude_connector"})
        assert self.pick(facts(created_at=NOW - timedelta(days=15), message_keys=sent)) == (None, "nothing applies")
        assert self.pick(facts(created_at=NOW - timedelta(days=15), message_keys=sent, follow_count=2)) == (
            "search_and_people",
            "due",
        )

    def test_frequency_cap(self):
        f = facts(created_at=NOW - timedelta(days=4), last_message_at=NOW - timedelta(days=1))
        assert self.pick(f) == (None, "frequency cap")

    def test_no_tip_soon_after_a_briefing_email(self):
        f = facts(last_briefing_email_at=NOW - timedelta(hours=4))
        assert self.pick(f) == (None, "briefing email sent recently")

    def test_send_window_uses_the_readers_zone(self):
        # 12:00 UTC is 21:00 in Tokyo: outside 09–19.
        assert self.pick(facts(timezone_name="Asia/Tokyo")) == (None, "outside send window")
        assert self.pick(facts(timezone_name="Europe/London")) == ("welcome", "due")
        # An unknown zone falls back to UTC rather than failing the pass.
        assert self.pick(facts(timezone_name="Not/AZone")) == ("welcome", "due")


# -- plan + deliver ----------------------------------------------------------


def test_tick_queues_and_sends_once():
    repo = FakeRepo([facts()])
    sender = FakeSender()
    service = make_service(repo, sender)

    service.tick(NOW)
    service.tick(NOW + timedelta(minutes=15))

    assert [s["subject"] for s in sender.sent] == ["Welcome to thestill"]
    assert sender.sent[0]["to"] == "reader@example.com"
    [row] = repo.rows.values()
    assert row.status == MessageStatus.SENT and row.message_key == "welcome"


def test_switch_off_does_nothing():
    repo = FakeRepo([facts()])
    sender = FakeSender()
    make_service(repo, sender, enabled=lambda: False).tick(NOW)
    assert repo.rows == {} and sender.sent == []


def test_unsubscribe_between_plan_and_send_skips():
    repo = FakeRepo([facts()])
    sender = FakeSender()
    service = make_service(repo, sender)
    service.plan(NOW)
    del repo.facts["u-1"]  # tips turned off: the facts query no longer returns them

    service.deliver_due(NOW)

    [row] = repo.rows.values()
    assert row.status == MessageStatus.SKIPPED and sender.sent == []


def test_condition_rechecked_at_send_time():
    repo = FakeRepo([facts(created_at=NOW - timedelta(days=4), message_keys=frozenset())])
    sender = FakeSender()
    service = make_service(repo, sender)
    service.plan(NOW)  # queues follow_nudge (the welcome expired)
    repo.facts["u-1"] = replace(repo.facts["u-1"], follow_count=1)

    service.deliver_due(NOW)

    [row] = repo.rows.values()
    assert row.message_key == "follow_nudge" and row.status == MessageStatus.SKIPPED
    assert sender.sent == []


def test_transport_failure_retries_then_parks():
    repo = FakeRepo([facts()])
    service = make_service(repo, FakeSender(fail=RuntimeError("smtp down")), max_attempts=2, backoff_seconds=60)
    service.plan(NOW)

    service.deliver_due(NOW)
    [row] = repo.rows.values()
    assert row.status == MessageStatus.PENDING and row.attempts == 1
    assert row.next_attempt_at == NOW + timedelta(seconds=60)

    service.deliver_due(NOW + timedelta(seconds=60))
    assert row.status == MessageStatus.FAILED and row.attempts == 2


def test_catch_up_sends_only_what_is_still_current():
    # Day 8 with nothing sent (the switch was off): the welcome and the
    # nudge have expired, so the reader gets the connector tip alone.
    repo = FakeRepo([facts(created_at=NOW - timedelta(days=8))])
    sender = FakeSender()
    service = make_service(repo, sender)

    service.tick(NOW)
    service.tick(NOW + timedelta(hours=1))

    assert [s["subject"] for s in sender.sent] == ["Ask Claude about your podcasts"]


def test_paging_reaches_every_candidate():
    # Codex P2: with more candidates than a page, later signups must not be
    # starved by earlier ones that have nothing left to receive.
    people = [facts(user_id=f"u-{i}", created_at=NOW - timedelta(hours=30 + i)) for i in range(5)]
    repo = FakeRepo(people)
    service = make_service(repo, page_size=2)

    assert service.plan(NOW) == 5
    assert repo.pages == 3  # 2 + 2 + 1


def test_retry_after_hours_waits_for_the_readers_morning():
    # Codex P2: a send that fails at 18:45 and retries at 19:00 must not go
    # out after hours.
    evening = datetime(2026, 11, 19, 18, 45, tzinfo=timezone.utc)
    repo = FakeRepo([facts(created_at=evening - timedelta(hours=30))])
    service = make_service(repo, FakeSender(fail=RuntimeError("smtp down")), backoff_seconds=900)
    service.tick(evening)
    [row] = repo.rows.values()
    assert row.attempts == 1 and row.next_attempt_at == evening + timedelta(minutes=15)

    sender = FakeSender()
    service = make_service(repo, sender, backoff_seconds=900)
    service.deliver_due(evening + timedelta(minutes=15))

    assert sender.sent == []
    assert row.status == MessageStatus.PENDING and row.attempts == 1  # the deferral spent no attempt
    assert row.next_attempt_at == datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)
    service.deliver_due(datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc))
    assert [s["subject"] for s in sender.sent] == ["Welcome to thestill"]


def test_briefing_email_since_planning_defers_the_send():
    repo = FakeRepo([facts()])
    sender = FakeSender()
    service = make_service(repo, sender)
    service.plan(NOW)
    repo.facts["u-1"] = replace(repo.facts["u-1"], last_briefing_email_at=NOW - timedelta(hours=1))

    service.deliver_due(NOW)

    [row] = repo.rows.values()
    assert sender.sent == [] and row.status == MessageStatus.PENDING
    assert row.next_attempt_at == NOW + timedelta(hours=11)


def test_message_expired_while_waiting_is_skipped():
    repo = FakeRepo([facts(created_at=NOW - timedelta(days=2, hours=23))])
    service = make_service(repo, FakeSender())
    service.plan(NOW)  # welcome, one hour before it expires
    [row] = repo.rows.values()
    row.next_attempt_at = NOW + timedelta(hours=2)  # e.g. a backoff

    service.deliver_due(NOW + timedelta(hours=2))

    assert row.status == MessageStatus.SKIPPED and row.last_error == "expired"


def test_sequence_is_ordered_and_windows_are_sane():
    assert [m.key for m in SEQUENCE] == ["welcome", "follow_nudge", "claude_connector", "search_and_people"]
    for message in SEQUENCE:
        assert message.send_after < message.expires_after
    assert len({m.key for m in SEQUENCE}) == len(SEQUENCE)


# -- renderer ----------------------------------------------------------------


class TestRenderer:
    renderer = LifecycleEmailRenderer(public_base_url="https://thestill.test/", secret=SECRET)

    @pytest.mark.parametrize("message", SEQUENCE, ids=lambda m: m.key)
    def test_every_message_renders_with_absolute_links_and_unsubscribe(self, message):
        email = self.renderer.render(message, facts(follow_count=2))

        assert email.subject
        assert email.text.startswith("Hi Alex,")
        hrefs = re.findall(r'href="([^"]+)"', email.html)
        assert hrefs and all(h.startswith("https://thestill.test/") for h in hrefs)
        assert "](" not in email.text and "**" not in email.text  # plain text, no markdown
        assert email.headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"

    def test_unsubscribe_link_is_a_tips_token(self):
        email = self.renderer.render(SEQUENCE[0], facts())
        url = email.headers["List-Unsubscribe"].strip("<>")
        assert url.startswith("https://thestill.test/unsubscribe/tips?token=")
        token = unquote(url.split("token=", 1)[1])
        assert verify_unsubscribe_token(token, SECRET, purpose=TIPS_PURPOSE) == "u-1"
        assert verify_unsubscribe_token(token, SECRET) is None  # not a briefing token

    def test_welcome_speaks_to_where_the_reader_is(self):
        none = self.renderer.render(SEQUENCE[0], facts(follow_count=0)).text
        some = self.renderer.render(
            SEQUENCE[0], facts(follow_count=3, briefing_enabled=True, briefing_hour_local=7)
        ).text
        assert "first step is to follow a show" in none
        assert "You follow 3 shows" in some and "07:00 your time" in some
        assert "Turn on briefing emails" in some

    def test_name_is_escaped_in_html(self):
        email = self.renderer.render(SEQUENCE[0], facts(name="<b>Eve</b> X"))
        assert "<b>Eve</b>" not in email.html and "&lt;b&gt;Eve&lt;/b&gt;" in email.html

    def test_no_name_greets_plainly(self):
        assert self.renderer.render(SEQUENCE[0], facts(name=None)).text.startswith("Hi,")
