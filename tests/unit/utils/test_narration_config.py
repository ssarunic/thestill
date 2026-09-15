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

"""Spec #77 §7 — narration voice + writer tuning keys."""

import pytest

from thestill.utils.config import load_config


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """Isolate load_config from the developer's real .env."""
    empty_env = tmp_path / ".env"
    empty_env.touch()
    monkeypatch.setenv("THESTILL_ENV_FILE", str(empty_env))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    for key in (
        "DATABASE_URL",
        "NARRATION_ANCHOR_PROMPT",
        "NARRATION_STATED_TARGET_RATIO",
        "NARRATION_MATERIAL_MAX_WORDS",
    ):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_defaults_are_conversational_voice_and_spec_values(clean_env) -> None:
    config = load_config()
    assert config.narration_anchor_prompt == "conversational_anchor"
    assert config.narration_stated_target_ratio == 0.8
    assert config.narration_material_max_words == 400


def test_env_overrides_are_read(clean_env) -> None:
    clean_env.setenv("NARRATION_ANCHOR_PROMPT", " newsroom_anchor ")
    clean_env.setenv("NARRATION_STATED_TARGET_RATIO", "1.0")
    clean_env.setenv("NARRATION_MATERIAL_MAX_WORDS", "250")
    config = load_config()
    assert config.narration_anchor_prompt == "newsroom_anchor"
    assert config.narration_stated_target_ratio == 1.0
    assert config.narration_material_max_words == 250


@pytest.mark.parametrize("ratio", ["0.49", "1.01", "0"])
def test_ratio_outside_bounds_is_rejected(clean_env, ratio: str) -> None:
    clean_env.setenv("NARRATION_STATED_TARGET_RATIO", ratio)
    with pytest.raises(ValueError, match="NARRATION_STATED_TARGET_RATIO"):
        load_config()


def test_non_positive_material_cap_is_rejected(clean_env) -> None:
    clean_env.setenv("NARRATION_MATERIAL_MAX_WORDS", "0")
    with pytest.raises(ValueError, match="NARRATION_MATERIAL_MAX_WORDS"):
        load_config()


def test_empty_prompt_name_is_rejected(clean_env) -> None:
    clean_env.setenv("NARRATION_ANCHOR_PROMPT", "   ")
    with pytest.raises(ValueError, match="NARRATION_ANCHOR_PROMPT"):
        load_config()
