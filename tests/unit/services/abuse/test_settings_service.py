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

"""Spec #104 §1.3 — runtime settings: env defaults, overrides, cache, switches."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from thestill.services.abuse.actor import Actor
from thestill.services.abuse.errors import FeaturePausedError, ReadOnlyError
from thestill.services.abuse.settings_registry import SETTINGS, get_spec
from thestill.services.abuse.settings_service import SettingsService

from .conftest import NOW, make_world


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_env_defaults_apply_with_no_row(tmp_path):
    world = make_world(tmp_path, signup_mode="allowlist", signup_allowlist=["@Example.com"], narration_enabled=True)
    settings = world.abuse.settings
    assert settings.get("signup_mode") == "allowlist"
    assert settings.get("signup_allowlist") == ["@example.com"]
    assert settings.get("narration_enabled") is True
    assert settings.get("read_only") is False
    assert settings.effective("signup_mode").source == "env"
    assert settings.non_default() == {}


def test_runtime_row_overrides_env_and_is_audited(world):
    settings = world.abuse.settings
    result = settings.set("signup_mode", "closed", actor=Actor.cli())
    assert result.value == "closed" and result.source == "runtime"
    assert settings.non_default() == {"signup_mode": "closed"}

    [entry] = world.repos.audit_log.list()
    assert entry.action == "setting.set" and entry.actor == "cli"
    assert entry.details == {"key": "signup_mode", "old": "open", "new": "closed"}

    reverted = settings.reset("signup_mode", actor=Actor.cli())
    assert reverted.value == "open" and reverted.source == "env"
    assert world.repos.audit_log.count() == 2


def test_validation_rejects_bad_values_and_unknown_keys(world):
    settings = world.abuse.settings
    with pytest.raises(ValueError):
        settings.set("signup_mode", "sometimes", actor=Actor.cli())
    with pytest.raises(ValueError):
        settings.set("read_only", "yes", actor=Actor.cli())  # JSON bool only; the CLI parses text
    with pytest.raises(ValueError):
        settings.set("signup_allowlist", ["not-an-email"], actor=Actor.cli())
    with pytest.raises(KeyError):
        settings.set("free_beer", True, actor=Actor.cli())
    assert world.repos.audit_log.count() == 0


def test_cli_parsers():
    assert get_spec("read_only").parse("on") is True
    assert get_spec("read_only").parse("False") is False
    with pytest.raises(ValueError):
        get_spec("read_only").parse("maybe")
    allowlist = get_spec("signup_allowlist")
    assert allowlist.validate(allowlist.parse("Alice@Example.com, @corp.io")) == ["alice@example.com", "@corp.io"]
    assert allowlist.validate(allowlist.parse('["@a.io"]')) == ["@a.io"]
    assert set(SETTINGS) >= {"signup_mode", "new_feeds_enabled", "read_only"}


def test_cache_expires_after_ttl(world):
    clock = _Clock()
    reader = SettingsService(world.repos.settings, world.config, ttl_seconds=15, clock=clock)
    assert reader.get("imports_enabled") is True
    # Another worker writes; this one keeps its snapshot until the TTL.
    world.abuse.settings.set("imports_enabled", False, actor=Actor.cli())
    clock.now += 14
    assert reader.get("imports_enabled") is True
    clock.now += 2
    assert reader.get("imports_enabled") is False


def test_feature_switches_and_read_only_implication(world):
    settings = world.abuse.settings
    assert all(settings.allowed(f) for f in ("new_feeds", "imports", "translations"))
    # Narration's default is NARRATION_ENABLED, off unless set.
    assert not settings.allowed("narration")

    settings.set("translations_enabled", False, actor=Actor.cli())
    assert not settings.allowed("translations") and settings.allowed("imports")
    with pytest.raises(FeaturePausedError) as exc:
        settings.require("translations")
    assert exc.value.detail() == {
        "error": "feature_paused",
        "feature": "translations",
        "message": "Summary translations are paused right now.",
    }

    settings.set("read_only", True, actor=Actor.cli())
    for feature in ("new_feeds", "imports", "narration"):
        with pytest.raises(ReadOnlyError):
            settings.require(feature)


def test_invalid_stored_row_falls_back_to_default(world):
    world.repos.settings.upsert("signup_mode", "bogus", updated_by=None, updated_at=world.abuse.audit._clock())
    assert world.abuse.settings.get("signup_mode") == "open"


def test_read_failure_keeps_last_good_snapshot(world):
    clock = _Clock()
    world.abuse.settings.set("read_only", True, actor=Actor.cli())
    reader = SettingsService(world.repos.settings, world.config, ttl_seconds=15, clock=clock)
    assert reader.get("read_only") is True

    def boom():
        raise RuntimeError("database blinked")

    reader._repo = SimpleNamespace(get_all=boom)
    clock.now += 60
    # A blip must not silently lift panic mode.
    assert reader.get("read_only") is True


def test_a_bad_env_default_stops_the_boot(tmp_path, monkeypatch):
    from thestill.utils.config import load_config

    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("THESTILL_ENV_FILE", str(tmp_path / ".no-such-env"))
    monkeypatch.setenv("SIGNUP_MODE", "Closd")
    with pytest.raises(ValueError, match="SIGNUP_MODE"):
        load_config()
    monkeypatch.setenv("SIGNUP_MODE", "allowlist")
    monkeypatch.setenv("SIGNUP_ALLOWLIST", "bob")
    with pytest.raises(ValueError, match="SIGNUP_ALLOWLIST"):
        load_config()
    monkeypatch.setenv("SIGNUP_ALLOWLIST", "Bob@Example.com, @corp.io")
    assert load_config().signup_allowlist == ["bob@example.com", "@corp.io"]


def test_an_invalid_env_default_fails_closed_without_breaking_reads(tmp_path):
    # Hand-built configs skip load_config's check: the switches still work.
    world = make_world(tmp_path, signup_mode="Closd", signup_allowlist=["bob"])
    settings = world.abuse.settings
    assert settings.get("signup_mode") == "closed"
    assert settings.get("signup_allowlist") == []
    assert settings.allowed("imports")


def test_a_read_racing_a_write_does_not_cache_stale_rows(world, monkeypatch):
    settings, repo = world.abuse.settings, world.repos.settings
    real_get_all = repo.get_all
    stale = real_get_all()  # snapshot taken before the write below
    repo.upsert("imports_enabled", False, updated_by=None, updated_at=NOW)

    def racing_read():
        monkeypatch.setattr(repo, "get_all", real_get_all)
        settings.invalidate()  # the write's invalidation lands mid-read
        return stale

    monkeypatch.setattr(repo, "get_all", racing_read)
    assert settings.get("imports_enabled") is True  # this read answers from its snapshot...
    assert settings.get("imports_enabled") is False  # ...but did not cache it
