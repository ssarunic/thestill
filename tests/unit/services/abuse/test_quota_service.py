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

"""Spec #104 Phase 2 — quotas through the services that spend."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from thestill.core.periodic_runner import PeriodicRunner
from thestill.models.briefing import Briefing
from thestill.services.abuse.actor import Actor
from thestill.services.abuse.errors import FeaturePausedError, QuotaExceededError
from thestill.services.abuse.housekeeping import usage_housekeeping_jobs
from thestill.services.abuse.quota_service import reserve_quota
from thestill.services.narration import NarrationRunner

from .conftest import NOW, make_world

DAY_AGO = NOW - timedelta(hours=24)


class _Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


def _world(tmp_path, **limits):
    world = make_world(tmp_path, clock=_Clock(), **limits)
    world.quota = world.abuse.quota
    return world


def _count(world, user_id, kind):
    return world.repos.usage.window(user_id, kind, world.clock() - timedelta(hours=24))[0]


def test_reserve_refuses_at_limit_plus_one_with_retry_after(tmp_path):
    world = _world(tmp_path, quota_translations_per_day=2)
    user = world.add_user("u@example.com")
    for minutes in (0, 60):
        world.clock.now = NOW + timedelta(minutes=minutes)
        with world.quota.reserve(user.id, "translations_per_day", subject_id="e") as reservation:
            reservation.commit()

    world.clock.now = NOW + timedelta(hours=2)
    with pytest.raises(QuotaExceededError) as exc:
        world.quota.reserve(user.id, "translations_per_day")
    assert exc.value.limit == 2
    assert exc.value.retry_after_seconds == 22 * 3600  # the first one leaves the window then
    assert exc.value.headers() == {"Retry-After": str(22 * 3600)}
    assert "You've translated 2 summaries today. You can translate more in 22 hours." == str(exc.value)
    assert world.repos.usage.counts_by_kind(user.id, DAY_AGO)["quota_exceeded"] == 1


def test_failure_before_spend_releases_and_after_spend_commits(tmp_path):
    world = _world(tmp_path, quota_narrations_per_day=1)
    user = world.add_user("u@example.com")

    with pytest.raises(RuntimeError):
        with world.quota.reserve(user.id, "narrations_per_day"):
            raise RuntimeError("provider rejected the request")  # nothing charged
    assert _count(world, user.id, "narration") == 0

    with pytest.raises(OSError):
        with world.quota.reserve(user.id, "narrations_per_day") as reservation:
            reservation.commit()  # the provider charged ...
            raise OSError("... but storing the result failed")
    assert _count(world, user.id, "narration") == 1
    with pytest.raises(QuotaExceededError):
        world.quota.reserve(user.id, "narrations_per_day")


def test_a_pending_reservation_counts(tmp_path):
    world = _world(tmp_path, quota_translations_per_day=1)
    user = world.add_user("u@example.com")
    with world.quota.reserve(user.id, "translations_per_day") as reservation:
        with pytest.raises(QuotaExceededError):
            world.quota.reserve(user.id, "translations_per_day")  # a slow first one is still in flight
        reservation.commit()


def test_admins_are_exempt_but_counted(tmp_path):
    world = _world(tmp_path, quota_translations_per_day=1)
    admin = world.add_user("op@example.com", is_admin=True)
    for _ in range(3):
        with world.quota.reserve(admin.id, "translations_per_day") as reservation:
            reservation.commit()
    assert _count(world, admin.id, "translation") == 3


def test_overrides_raise_and_lower_limits(tmp_path):
    world = _world(tmp_path, quota_imports_per_day=1)
    trusted, suspect = world.add_user("t@example.com"), world.add_user("s@example.com")
    admin = world.abuse.account_admin
    assert admin.set_quota_override(trusted.id, "imports_per_day", 3, actor=Actor.cli()) == {"imports_per_day": 3}
    admin.set_quota_override(suspect.id, "translations_per_day", 0, actor=Actor.cli())  # 0 = unlimited
    assert world.quota.limit_for(trusted.id, "imports_per_day") == 3
    assert world.quota.limit_for(suspect.id, "imports_per_day") == 1
    assert world.quota.limit_for(suspect.id, "translations_per_day") == 0

    entry = world.repos.audit_log.list(action="user.quota_override", target_user_id=trusted.id)[0]
    assert entry.details == {"quota": "imports_per_day", "old": None, "new": 3}
    assert world.repos.audit_log.count(action="setting.set") == 0  # no whole-map entry

    admin.set_quota_override(trusted.id, "imports_per_day", None, actor=Actor.cli())
    assert world.quota.limit_for(trusted.id, "imports_per_day") == 1


def test_unenforced_quotas_record_without_refusing(tmp_path):
    world = _world(tmp_path, quota_translations_per_day=1, quota_enforce=False)
    user = world.add_user("u@example.com")
    for _ in range(3):
        with world.quota.reserve(user.id, "translations_per_day") as reservation:
            reservation.commit()
    assert _count(world, user.id, "translation") == 3


def test_quotas_do_not_apply_in_single_user_mode(tmp_path):
    world = make_world(tmp_path, multi_user=False, quota_translations_per_day=1)
    local = world.add_user("local@thestill.me")
    for _ in range(3):
        with world.abuse.quota.reserve(local.id, "translations_per_day") as reservation:
            reservation.commit()
    assert world.repos.usage.counts_by_kind(local.id, DAY_AGO) == {}


# ----- follows -----


def test_feed_introduced_quota_spares_feeds_others_follow(tmp_path):
    world = _world(tmp_path, quota_feeds_introduced_per_day=1)
    user, other = world.add_user("u@example.com"), world.add_user("o@example.com")
    first, _ = world.add_podcast("first")
    second, _ = world.add_podcast("second")
    popular, _ = world.add_podcast("popular")
    world.follow(other.id, popular)

    world.follower_service.follow(user.id, first)
    world.follower_service.follow(user.id, popular)  # someone already follows it: cheap, allowed
    with pytest.raises(QuotaExceededError) as exc:
        world.follower_service.follow(user.id, second)
    assert exc.value.quota == "feeds_introduced_per_day"
    assert str(exc.value) == "You've added 1 new podcasts today. You can add more in 24 hours."
    assert sorted(world.repos.follower.get_followed_podcast_ids(user.id)) == sorted([first, popular])
    assert world.repos.usage.counts_by_kind(user.id, DAY_AGO) == {
        "follow": 2,
        "feed_introduced": 1,
        "quota_exceeded": 1,
    }


def test_follow_quotas_total_and_per_day(tmp_path):
    world = _world(tmp_path, quota_follows_per_day=2, quota_follows_total=3)
    user, other = world.add_user("u@example.com"), world.add_user("o@example.com")
    feeds = [world.add_podcast(f"p{i}")[0] for i in range(4)]
    for pid in feeds:
        world.follow(other.id, pid)

    world.follower_service.follow(user.id, feeds[0])
    world.follower_service.follow(user.id, feeds[1])
    with pytest.raises(QuotaExceededError) as exc:
        world.follower_service.follow(user.id, feeds[2])
    assert exc.value.quota == "follows_per_day"

    world.clock.now = NOW + timedelta(days=1, minutes=1)  # a new day
    world.follower_service.follow(user.id, feeds[2])
    with pytest.raises(QuotaExceededError) as exc:
        world.follower_service.follow(user.id, feeds[3])
    assert exc.value.quota == "follows_total" and exc.value.retry_after_seconds is None
    assert exc.value.headers() == {}


# ----- call sites -----


def test_narration_quota_stops_before_the_llm(tmp_path):
    world = _world(tmp_path, quota_narrations_per_day=1)
    user = world.add_user("u@example.com")
    world.abuse.settings.set("narration_enabled", True, actor=Actor.cli())
    briefing = world.repos.briefing.insert(
        Briefing(user_id=user.id, cursor_from=NOW - timedelta(days=1), cursor_to=NOW, episode_count=1)
    )
    with world.quota.reserve(user.id, "narrations_per_day") as reservation:
        reservation.commit()

    runner = NarrationRunner(
        generator=SimpleNamespace(generate=lambda *_: pytest.fail("narration made an LLM call")),
        briefing_repository=world.repos.briefing,
        inbox_repository=world.repos.inbox,
        podcast_repository=world.repos.podcast,
        access=world.abuse.guard,
        quota=world.quota,
    )
    runner._resolve_episodes = lambda _briefing: ["episode"]
    with pytest.raises(QuotaExceededError):
        runner.run(briefing_id=briefing.id)


def test_translation_quota_charges_only_generated_translations(tmp_path, monkeypatch):
    from thestill.services.podcast_service import PodcastService
    from thestill.utils.path_manager import PathManager

    world = _world(tmp_path, quota_translations_per_day=1)
    user = world.add_user("u@example.com")
    service = PodcastService(
        world.config.storage_path,
        world.repos.podcast,
        PathManager(str(world.config.storage_path)),
        file_storage=world.config.file_storage,
        access=world.abuse.guard,
        quota=world.quota,
    )
    cache = {}
    monkeypatch.setattr(service, "_summary_path_for_language", lambda *a, **k: world.config.storage_path / "s.md")
    monkeypatch.setattr(
        service,
        "get_summary_for_episode",
        lambda ep, language=None, canonical_language=None: cache.get((ep.id, language)) if language else "# Summary",
    )
    import thestill.core.summary_citations as citations
    import thestill.core.summary_translation as translation

    monkeypatch.setattr(
        translation, "SummaryTranslator", lambda _p: SimpleNamespace(translate=lambda *a, **k: "# Prijevod")
    )
    monkeypatch.setattr(
        citations, "resolve_and_persist_summary_citations", lambda **k: SimpleNamespace(markdown=k["summary_markdown"])
    )
    monkeypatch.setattr("thestill.services.podcast_service.write_translation_metadata", lambda *a, **k: None)

    first = SimpleNamespace(id="e1", summary_path="s.md")
    assert (
        service.get_or_create_summary_translation(
            first, source_language="en", target_language="hr", provider=None, user_id=user.id
        )
        == "# Prijevod"
    )
    cache[("e1", "hr")] = "# Prijevod"
    # Serving the cached one is free.
    service.get_or_create_summary_translation(
        first, source_language="en", target_language="hr", provider=None, user_id=user.id
    )
    with pytest.raises(QuotaExceededError):
        service.get_or_create_summary_translation(
            SimpleNamespace(id="e2", summary_path="s.md"),
            source_language="en",
            target_language="hr",
            provider=None,
            user_id=user.id,
        )
    assert _count(world, user.id, "translation") == 1


# ----- housekeeping -----


def test_housekeeping_releases_abandoned_reservations(tmp_path):
    world = _world(tmp_path, quota_translations_per_day=1)
    user = world.add_user("u@example.com")
    world.quota.reserve(user.id, "translations_per_day")  # the process "dies": never settled
    with pytest.raises(QuotaExceededError):
        world.quota.reserve(user.id, "translations_per_day")  # fails closed meanwhile

    jobs = usage_housekeeping_jobs(
        world.repos.usage, reservation_ttl_seconds=900, clock=lambda: NOW + timedelta(minutes=10)
    )
    PeriodicRunner(jobs).run_due()
    with pytest.raises(QuotaExceededError):
        world.quota.reserve(user.id, "translations_per_day")  # a fresher one keeps counting

    jobs = usage_housekeeping_jobs(
        world.repos.usage, reservation_ttl_seconds=900, clock=lambda: NOW + timedelta(minutes=16)
    )
    PeriodicRunner(jobs).run_due()
    with world.quota.reserve(user.id, "translations_per_day"):
        pass


def test_follow_quotas_are_not_reserved(tmp_path):
    world = _world(tmp_path)
    user = world.add_user("u@example.com")
    with pytest.raises(ValueError):
        world.quota.reserve(user.id, "follows_per_day")


def test_a_commit_after_the_reservation_expired_still_counts(tmp_path):
    world = _world(tmp_path, quota_translations_per_day=5)
    user = world.add_user("u@example.com")
    with world.quota.reserve(user.id, "translations_per_day") as reservation:
        # The paid call outlives the TTL; housekeeping releases the row.
        world.repos.usage.release_stale(NOW + timedelta(seconds=1), now=NOW)
        reservation.commit(subject_id="e1")
    assert _count(world, user.id, "translation") == 1


def test_reserve_quota_without_a_service_is_a_no_op():
    with reserve_quota(None, "anyone", "imports_per_day") as reservation:
        reservation.commit()


def test_add_path_precheck_refuses_over_quota_without_recording(tmp_path):
    world = _world(tmp_path, quota_feeds_introduced_per_day=1)
    user, other = world.add_user("u@example.com"), world.add_user("o@example.com")
    mine, _ = world.add_podcast("mine")
    popular, _ = world.add_podcast("popular")
    world.follow(other.id, popular)
    world.follower_service.follow(user.id, mine)
    check = world.abuse.new_feed_precheck.check
    podcasts = world.repos.podcast

    with pytest.raises(QuotaExceededError) as exc:
        check(None, will_follow=True, user_id=user.id)  # a brand-new feed
    assert exc.value.quota == "feeds_introduced_per_day"
    assert exc.value.retry_after_seconds == 24 * 3600
    check(podcasts.get_by_id(popular), will_follow=True, user_id=user.id)  # others follow it: fine
    check(podcasts.get_by_id(mine), will_follow=True, user_id=user.id)  # already followed: nothing added
    check(None, will_follow=False, user_id=user.id)  # browsing never counts
    assert "quota_exceeded" not in world.repos.usage.counts_by_kind(user.id, DAY_AGO)


def test_re_importing_an_existing_episode_is_free_at_the_limit(tmp_path):
    from thestill.services.import_service import ImportService

    world = _world(tmp_path, quota_imports_per_day=1)
    user = world.add_user("u@example.com")
    with world.quota.reserve(user.id, "imports_per_day") as reservation:
        reservation.commit()  # the day's one import

    service = ImportService(
        repository=SimpleNamespace(find_episode_id_by_canonical_id=lambda _cid: "ep-1"),
        inbox_repository=SimpleNamespace(find_or_create=lambda **_: (SimpleNamespace(), False)),
        queue_manager=None,
        quota=world.quota,
    )
    service._resolve = lambda url: SimpleNamespace(canonical_id="c-1", kind="audio", title="t", source_handle=None)
    service._find_or_create_episode = lambda canonical: ("ep-1", False, None)
    service._pending_for_existing = lambda episode_id: []  # processed or in flight
    service._describe_episode = lambda user_id, episode_id: (None, None, None, False)

    result = service.import_url(user_id=user.id, url="https://example.com/ep.mp3")
    assert result.episode_id == "ep-1" and not result.episode_created
    assert _count(world, user.id, "import") == 1


def _orphan_import_service(world, enqueued):
    from thestill.services.import_service import ImportService

    service = ImportService(
        repository=SimpleNamespace(find_episode_id_by_canonical_id=lambda _cid: "ep-1"),
        inbox_repository=SimpleNamespace(find_or_create=lambda **_: (SimpleNamespace(), False)),
        queue_manager=None,
        quota=world.quota,
    )
    service._resolve = lambda url: SimpleNamespace(canonical_id="c-1", kind="audio", title="t", source_handle=None)
    service._find_or_create_episode = lambda canonical: ("ep-1", False, None)
    service._pending_for_existing = lambda episode_id: [(episode_id, "https://example.com/ep.mp3")]
    service._enqueue_existing = lambda pending: enqueued.extend(pending) or len(pending)
    service._describe_episode = lambda user_id, episode_id: (None, None, None, False)
    return service


def test_reimport_that_starts_an_orphan_pipeline_is_charged(tmp_path):
    """An existing but never-processed episode costs a pipeline run when an
    import starts it, so it counts — and is refused at the limit."""
    world = _world(tmp_path, quota_imports_per_day=1)
    user = world.add_user("u@example.com")
    enqueued: list = []
    service = _orphan_import_service(world, enqueued)

    service.import_url(user_id=user.id, url="https://example.com/ep.mp3")
    assert _count(world, user.id, "import") == 1 and len(enqueued) == 1

    with pytest.raises(QuotaExceededError):
        service.import_url(user_id=user.id, url="https://example.com/ep.mp3")
    assert len(enqueued) == 1


def _inbox_service(world):
    from thestill.services.inbox_service import InboxService

    return InboxService(
        world.repos.inbox,
        world.repos.follower,
        queue_manager=world.repos.queue_manager,
        podcast_repository=world.repos.podcast,
        transcription_provider="dalston",
        access=world.abuse.guard,
        quota=world.quota,
    )


def test_send_to_inbox_that_starts_a_pipeline_counts_as_an_import(tmp_path):
    world = _world(tmp_path, quota_imports_per_day=1)
    user = world.add_user("u@example.com")
    _, (first, second) = world.add_podcast("p", episodes=2)
    service = _inbox_service(world)

    service.deliver_to_user(user.id, first)
    assert _count(world, user.id, "import") == 1
    assert world.repos.queue_manager.get_tasks_for_episode(first)

    with pytest.raises(QuotaExceededError):
        service.deliver_to_user(user.id, second)
    assert world.repos.inbox.get(user.id, second) is None
    assert world.repos.queue_manager.get_tasks_for_episode(second) == []


def test_send_to_inbox_of_an_in_flight_episode_is_free_at_the_limit(tmp_path):
    world = _world(tmp_path, quota_imports_per_day=1)
    alice = world.add_user("a@example.com")
    bob = world.add_user("b@example.com")
    _, (episode,) = world.add_podcast("p", episodes=1)
    service = _inbox_service(world)
    with world.quota.reserve(bob.id, "imports_per_day") as reservation:
        reservation.commit()  # bob's one import today

    service.deliver_to_user(alice.id, episode)  # starts the pipeline
    _, created = service.deliver_to_user(bob.id, episode)

    assert created is True
    assert _count(world, bob.id, "import") == 1


def test_send_to_inbox_respects_the_imports_switch(tmp_path):
    world = _world(tmp_path)
    user = world.add_user("u@example.com")
    _, (episode,) = world.add_podcast("p", episodes=1)
    world.abuse.settings.set("imports_enabled", False, actor=Actor.cli())

    with pytest.raises(FeaturePausedError):
        _inbox_service(world).deliver_to_user(user.id, episode)
    assert world.repos.inbox.get(user.id, episode) is None
