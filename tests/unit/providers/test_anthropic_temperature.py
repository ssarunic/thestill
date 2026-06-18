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

"""AnthropicProvider must honor MODEL_CONFIGS[model].supports_temperature.

Reasoning models (Claude Opus 4.8) reject ``temperature`` with a 400, so the
provider must drop the parameter even when a caller passes it. Models that
accept temperature (Sonnet 4.6) must still receive it.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from thestill.core.llm_provider import AnthropicProvider


def _provider(model: str) -> AnthropicProvider:
    p = AnthropicProvider(api_key="test-key", model=model)
    return p


def _fake_response(text: str = "ok"):
    return SimpleNamespace(
        content=[SimpleNamespace(text=text)],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        stop_reason="end_turn",
    )


# --- supports_temperature() decision (exact + date-suffixed + prefix) --------
@pytest.mark.parametrize(
    "model,expected",
    [
        ("claude-opus-4-8", False),
        ("claude-opus-4-8-20260101", False),  # dated id resolves to base config
        ("claude-sonnet-4-6", True),
    ],
)
def test_supports_temperature_lookup(model: str, expected: bool) -> None:
    assert _provider(model).supports_temperature() is expected


# --- request builders drop / keep temperature accordingly --------------------
def _captured_create_kwargs(model: str) -> dict:
    p = _provider(model)
    create = MagicMock(return_value=_fake_response())
    p.client.messages.create = create
    p.chat_completion([{"role": "user", "content": "hi"}], temperature=0.5)
    return create.call_args.kwargs


def test_opus_48_omits_temperature_even_when_passed() -> None:
    kwargs = _captured_create_kwargs("claude-opus-4-8")
    assert "temperature" not in kwargs


def test_temperature_capable_model_keeps_temperature() -> None:
    kwargs = _captured_create_kwargs("claude-sonnet-4-6")
    assert kwargs.get("temperature") == 0.5
