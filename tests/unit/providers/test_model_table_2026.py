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

"""Model table refresh, 2026-09-22: the 2026 generation of every provider."""

from unittest.mock import patch

import pytest

from thestill.core.llm_provider import _PROMPT_CACHING_MODELS, MODEL_CONFIGS, GeminiProvider, OpenAIProvider


@pytest.mark.parametrize(
    "model_id",
    [
        "gpt-6-astra",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "claude-opus-5",
        "claude-sonnet-5",
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.1-pro-preview",
        "gemini-3.1-flash-lite",
        "gemma4:e2b",
        "gemma4:12b",
        "gemma4:26b",
        "gemma4:31b",
    ],
)
def test_the_2026_generation_is_in_the_table(model_id):
    assert model_id in MODEL_CONFIGS


def test_retired_gemini_3_pro_preview_is_gone():
    assert "gemini-3-pro-preview" not in MODEL_CONFIGS
    assert "gemini-3-pro-preview" not in _PROMPT_CACHING_MODELS


def test_the_gemini_defaults_name_a_model_that_is_in_the_table():
    """The retired entry is gone from the table, so nothing may still default to it."""
    import inspect

    from thestill.core.llm_provider import create_llm_provider

    factory_default = inspect.signature(create_llm_provider).parameters["gemini_model"].default
    provider_default = inspect.signature(GeminiProvider.__init__).parameters["model"].default
    assert factory_default == provider_default
    assert factory_default in MODEL_CONFIGS


def test_gemini_3_flash_preview_is_still_served():
    assert "gemini-3-flash-preview" in MODEL_CONFIGS


@pytest.mark.parametrize("model_id", ["gpt-6-astra", "gpt-5.6-terra", "gemini-3.8-flash"])
def test_new_models_are_cache_eligible(model_id):
    assert model_id in _PROMPT_CACHING_MODELS


def _openai(model, effort=None):
    with patch("thestill.core.llm_provider.OpenAI"):
        return OpenAIProvider(api_key="test-key", model=model, reasoning_effort=effort)


@pytest.mark.parametrize("model", ["gpt-6-astra", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"])
def test_the_2026_openai_models_are_reasoning_models_that_take_reasoning_effort(model):
    provider = _openai(model)
    assert provider._is_reasoning_model() is True
    assert provider._is_gpt5x_model() is True
    assert provider.supports_temperature() is False
    assert provider._supports_top_efforts() is True


def test_gpt_5_2_does_not_get_max_and_gpt_5_1_does_not_get_xhigh():
    assert _openai("gpt-5.2")._supports_top_efforts() is True
    assert _openai("gpt-5.1")._supports_top_efforts() is False
    assert "max" in OpenAIProvider.VALID_REASONING_EFFORTS


def _gemini(model, level):
    with patch("thestill.core.llm_provider.genai"):
        return GeminiProvider(api_key="test-key", model=model, thinking_level=level)


def test_gemini_3_8_flash_rejects_minimal_and_falls_back_to_the_api_default():
    assert _gemini("gemini-3.8-flash", "minimal")._get_thinking_config() is None
    assert _gemini("gemini-3.8-flash", "low")._get_thinking_config() is not None


def test_earlier_flash_generations_keep_minimal():
    assert _gemini("gemini-3.6-flash", "minimal")._get_thinking_config() is not None
    assert _gemini("gemini-3-flash-preview", "minimal")._get_thinking_config() is not None


@pytest.mark.parametrize(
    "model, generation", [("gemini-3-flash-preview", 3.0), ("gemini-3.8-flash", 3.8), ("gemini-2.5-flash", 0.0)]
)
def test_gemini_generation_parsing(model, generation):
    assert _gemini(model, "low")._gemini_generation() == generation
