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

"""Spec #81 - configuration and the linker factory."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tests.unit.core.entity_linking.conftest import ScriptedProvider
from thestill.core.entity_linking import factory
from thestill.core.entity_linking.live_linker import LiveWikidataLinker
from thestill.core.entity_linking.rate_limiter import reset_shared_rate_limiter
from thestill.core.entity_resolver import EntityResolver
from thestill.utils.config import load_config


@pytest.fixture
def env(monkeypatch, tmp_path):
    empty_env = tmp_path / ".env"
    empty_env.touch()
    monkeypatch.setenv("THESTILL_ENV_FILE", str(empty_env))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 64)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for name in ("ENTITY_LINKER", "ENTITY_LINKING_PROVIDER", "ENTITY_LINKING_MODEL", "ENTITY_LINKING_MIN_CONFIDENCE"):
        monkeypatch.delenv(name, raising=False)
    reset_shared_rate_limiter()
    yield monkeypatch
    reset_shared_rate_limiter()


def test_defaults_keep_refined(env):
    config = load_config()
    assert config.entity_linker == "refined"
    assert (config.entity_linking_provider, config.entity_linking_model) == ("", "")
    assert config.entity_linking_min_confidence == "medium"
    assert config.entity_linking_none_ttl_days == 30
    assert config.wikidata_max_rps == 5.0


@pytest.mark.parametrize(
    "name, value, message",
    [
        ("ENTITY_LINKER", "refind", "ENTITY_LINKER must be"),
        ("ENTITY_LINKING_MIN_CONFIDENCE", "certain", "ENTITY_LINKING_MIN_CONFIDENCE must be"),
        ("ENTITY_LINKING_NONE_TTL_DAYS", "0", "ENTITY_LINKING_NONE_TTL_DAYS must be"),
        ("WIKIDATA_MAX_RPS", "0", "WIKIDATA_MAX_RPS must be"),
    ],
)
def test_bad_values_are_refused_at_boot(env, name, value, message):
    env.setenv(name, value)
    with pytest.raises(ValueError, match=message):
        load_config()


def _config(**overrides):
    base = dict(
        entity_linker="live",
        entity_linking_provider="",
        entity_linking_model="",
        entity_linking_min_confidence="medium",
        entity_linking_none_ttl_days=30,
        wikidata_max_rps=5.0,
        cleaning_provider="gemini",
        cleaning_model="gemini-flash-x",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _created_with(config):
    with (
        patch.object(
            factory, "provider_kwargs_from_config", return_value={"gemini_model": "global", "openai_model": "gpt"}
        ),
        patch.object(factory, "create_llm_provider") as create,
    ):
        factory.create_linking_provider(config)
    return create.call_args.kwargs


def test_the_provider_defaults_to_the_cleaning_pair():
    kwargs = _created_with(_config())
    assert kwargs["provider_type"] == "gemini" and kwargs["gemini_model"] == "gemini-flash-x"


def test_an_explicit_pair_wins():
    kwargs = _created_with(_config(entity_linking_provider="openai", entity_linking_model="gpt-mini"))
    assert kwargs["provider_type"] == "openai" and kwargs["openai_model"] == "gpt-mini"


def test_another_provider_without_a_model_does_not_inherit_the_cleaning_model():
    kwargs = _created_with(_config(entity_linking_provider="openai"))
    assert kwargs["provider_type"] == "openai" and kwargs["openai_model"] == "gpt"


def test_build_linker_follows_the_switch():
    repo = MagicMock()
    live = factory.build_linker(_config(), repo, provider=ScriptedProvider([]))
    assert isinstance(live, LiveWikidataLinker)
    assert isinstance(factory.build_linker(_config(entity_linker="refined"), repo), EntityResolver)


def test_the_live_linker_needs_no_install_and_refined_follows_its_extra():
    assert factory.linker_is_available(_config()) is True
    with patch.object(EntityResolver, "is_available", return_value=False):
        assert factory.linker_is_available(_config(entity_linker="refined")) is False
