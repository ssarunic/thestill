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

"""Spec #104 §1.1/§1.3 — the checks at each costly boundary that runs
without a request: briefings, narration, email delivery, translation,
imports."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from thestill.models.briefing import Briefing
from thestill.models.user import UserStatus
from thestill.services.abuse.actor import Actor
from thestill.services.abuse.errors import AccountDisabledError, FeaturePausedError, ReadOnlyError
from thestill.services.briefing_delivery_service import BriefingDeliveryService, _PermanentDeliveryError
from thestill.services.briefing_service import BriefingService
from thestill.services.narration import NarrationRunner

from .conftest import NOW


def _suspend(world, user_id):
    world.repos.user.apply_status(user_id, UserStatus.SUSPENDED, reason="x", changed_by=None, changed_at=NOW)


def test_briefing_for_a_disabled_user_is_skipped(world):
    user = world.add_user("u@example.com")
    service = BriefingService(world.repos.briefing, world.repos.inbox, min_interval_seconds=0, access=world.abuse.guard)
    _suspend(world, user.id)
    assert service.generate_for_user(user.id, force=True, now=NOW) is None
    assert world.repos.briefing.latest_for_user(user.id) is None


class _NoLLM:
    """A generator that fails the test if anything reaches the paid call."""

    path_manager = None

    def generate(self, *_args, **_kwargs):
        raise AssertionError("narration made an LLM call")


def _runner(world):
    return NarrationRunner(
        generator=_NoLLM(),
        briefing_repository=world.repos.briefing,
        inbox_repository=world.repos.inbox,
        podcast_repository=world.repos.podcast,
        access=world.abuse.guard,
    )


def _briefing(world, user_id):
    return world.repos.briefing.insert(
        Briefing(user_id=user_id, cursor_from=NOW - timedelta(days=1), cursor_to=NOW, episode_count=1)
    )


def test_narration_refuses_a_disabled_owner_before_any_llm_call(world):
    user = world.add_user("u@example.com")
    briefing = _briefing(world, user.id)
    world.abuse.settings.set("narration_enabled", True, actor=Actor.cli())
    _suspend(world, user.id)
    with pytest.raises(AccountDisabledError):
        _runner(world).run(briefing_id=briefing.id)


@pytest.mark.parametrize("switch, error", [("narration_enabled", FeaturePausedError), ("read_only", ReadOnlyError)])
def test_narration_switches_stop_scheduled_and_manual_runs(world, switch, error):
    user = world.add_user("u@example.com")
    briefing = _briefing(world, user.id)
    world.abuse.settings.set("narration_enabled", True, actor=Actor.cli())
    world.abuse.settings.set(switch, switch == "read_only", actor=Actor.cli())
    with pytest.raises(error):
        _runner(world).run(briefing_id=briefing.id)


@pytest.mark.parametrize("attempts", [0, 1, 2], ids=["pending", "after-one-retry", "on-backoff"])
def test_delivery_to_a_disabled_user_is_parked(world, attempts):
    user = world.add_user("u@example.com")
    briefing = SimpleNamespace(id="b1", user_id=user.id, script_path="/x/script.md")
    sent = []
    service = BriefingDeliveryService(
        delivery_repository=None,
        briefing_repository=SimpleNamespace(get=lambda _id: briefing),
        schedule_repository=SimpleNamespace(get=lambda _uid: SimpleNamespace(enabled=True, email_enabled=True)),
        user_repository=world.repos.user,
        renderer=None,
        sender=SimpleNamespace(send=lambda *a, **k: sent.append(a)),
        path_manager=None,
        file_storage=None,
        access=world.abuse.guard,
    )
    _suspend(world, user.id)
    with pytest.raises(_PermanentDeliveryError, match="account disabled"):
        service._send_one(SimpleNamespace(briefing_id="b1", attempts=attempts))
    assert sent == []


def _translation_service(world, monkeypatch, *, cached):
    from thestill.services.podcast_service import PodcastService
    from thestill.utils.path_manager import PathManager

    service = PodcastService(
        world.config.storage_path,
        world.repos.podcast,
        PathManager(str(world.config.storage_path)),
        file_storage=world.config.file_storage,
        access=world.abuse.guard,
    )
    episode = SimpleNamespace(id="e1", summary_path="summary.md")
    monkeypatch.setattr(service, "_summary_path_for_language", lambda *a, **k: world.config.storage_path / "s.de.md")
    monkeypatch.setattr(
        service,
        "get_summary_for_episode",
        lambda ep, language=None, canonical_language=None: cached if language else "# Summary",
    )
    import thestill.core.summary_translation as translation

    monkeypatch.setattr(translation, "SummaryTranslator", lambda _p: pytest.fail("translation made an LLM call"))
    return service, episode


def test_paused_translations_serve_existing_ones_and_refuse_new(world, monkeypatch):
    world.abuse.settings.set("translations_enabled", False, actor=Actor.cli())

    service, episode = _translation_service(world, monkeypatch, cached="# Zusammenfassung")
    assert (
        service.get_or_create_summary_translation(episode, source_language="en", target_language="de", provider=None)
        == "# Zusammenfassung"
    )

    service, episode = _translation_service(world, monkeypatch, cached=None)
    with pytest.raises(FeaturePausedError):
        service.get_or_create_summary_translation(episode, source_language="en", target_language="de", provider=None)


def test_paused_imports_refuse_before_resolving(world):
    from thestill.services.import_service import ImportService

    resolved = []
    service = ImportService(
        repository=world.repos.podcast,
        inbox_repository=world.repos.inbox,
        queue_manager=world.repos.queue_manager,
        resolvers=[SimpleNamespace(matches=lambda url: resolved.append(url) or True)],
        access=world.abuse.guard,
    )
    world.abuse.settings.set("imports_enabled", False, actor=Actor.cli())
    with pytest.raises(FeaturePausedError):
        service.import_url(user_id="u", url="https://example.com/ep.mp3")
    assert resolved == []
