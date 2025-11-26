# Copyright 2025 thestill.ai
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

"""
Tests for MockLLMProvider fixture.
"""

import json

from thestill.tests.conftest import MockLLMProvider


class TestMockLLMProvider:
    """Tests for the MockLLMProvider class."""

    def test_basic_response(self, mock_llm_provider):
        """Test that MockLLMProvider returns default response."""
        result = mock_llm_provider.chat_completion(messages=[{"role": "user", "content": "Hello"}])
        assert result == '{"result": "mock response"}'
        assert mock_llm_provider.call_count == 1

    def test_pattern_matching(self, mock_llm_provider):
        """Test that MockLLMProvider matches patterns case-insensitively."""
        mock_llm_provider.add_response("hello", "Hi there!")

        result = mock_llm_provider.chat_completion(messages=[{"role": "user", "content": "HELLO world"}])
        assert result == "Hi there!"

    def test_multiple_patterns(self, mock_llm_provider):
        """Test multiple pattern matching."""
        mock_llm_provider.add_response("foo", "response_foo")
        mock_llm_provider.add_response("bar", "response_bar")

        result1 = mock_llm_provider.chat_completion(messages=[{"role": "user", "content": "foo test"}])
        result2 = mock_llm_provider.chat_completion(messages=[{"role": "user", "content": "bar test"}])

        assert result1 == "response_foo"
        assert result2 == "response_bar"
        assert mock_llm_provider.call_count == 2

    def test_tracks_last_messages(self, mock_llm_provider):
        """Test that MockLLMProvider tracks last messages."""
        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Test message"},
        ]
        mock_llm_provider.chat_completion(messages=messages)

        assert mock_llm_provider.last_messages == messages

    def test_tracks_parameters(self, mock_llm_provider):
        """Test that MockLLMProvider tracks temperature and max_tokens."""
        mock_llm_provider.chat_completion(
            messages=[{"role": "user", "content": "Test"}],
            temperature=0.7,
            max_tokens=1000,
        )

        assert mock_llm_provider.last_temperature == 0.7
        assert mock_llm_provider.last_max_tokens == 1000

    def test_health_check(self, mock_llm_provider):
        """Test health_check always returns True."""
        assert mock_llm_provider.health_check() is True

    def test_supports_temperature(self, mock_llm_provider):
        """Test supports_temperature returns True."""
        assert mock_llm_provider.supports_temperature() is True

    def test_model_name(self, mock_llm_provider):
        """Test model name methods."""
        assert mock_llm_provider.get_model_name() == "mock-model"
        assert mock_llm_provider.get_model_display_name() == "Mock (mock-model)"

    def test_custom_model_name(self):
        """Test creating provider with custom model name."""
        provider = MockLLMProvider(model_name="custom-model")
        assert provider.get_model_name() == "custom-model"


class TestMockLLMProviderWithDefaults:
    """Tests for the mock_llm_provider_with_defaults fixture."""

    def test_entity_extraction_response(self, mock_llm_provider_with_defaults):
        """Test default entity extraction response."""
        result = mock_llm_provider_with_defaults.chat_completion(
            messages=[{"role": "user", "content": "Extract entity names from this text"}]
        )

        parsed = json.loads(result)
        assert "entities" in parsed
        assert len(parsed["entities"]) > 0
        assert parsed["entities"][0]["term"] == "OpenAI"

    def test_corrections_response(self, mock_llm_provider_with_defaults):
        """Test default corrections response."""
        result = mock_llm_provider_with_defaults.chat_completion(
            messages=[{"role": "user", "content": "Find corrections in this text"}]
        )

        parsed = json.loads(result)
        assert "corrections" in parsed

    def test_speaker_response(self, mock_llm_provider_with_defaults):
        """Test default speaker identification response."""
        result = mock_llm_provider_with_defaults.chat_completion(
            messages=[{"role": "user", "content": "Identify speaker names"}]
        )

        parsed = json.loads(result)
        assert "speaker_mapping" in parsed
        assert "SPEAKER_00" in parsed["speaker_mapping"]

    def test_clean_response(self, mock_llm_provider_with_defaults):
        """Test default cleaning response."""
        result = mock_llm_provider_with_defaults.chat_completion(
            messages=[{"role": "user", "content": "Clean this transcript"}]
        )

        assert "**Host:**" in result
        assert "**Guest:**" in result


class TestMockAnthropicProvider:
    """Tests for the mock_anthropic_provider fixture."""

    def test_anthropic_model_name(self, mock_anthropic_provider):
        """Test Anthropic provider has correct model name."""
        assert "claude" in mock_anthropic_provider.get_model_name().lower()

    def test_has_default_responses(self, mock_anthropic_provider):
        """Test Anthropic provider has default responses."""
        result = mock_anthropic_provider.chat_completion(messages=[{"role": "user", "content": "corrections"}])
        parsed = json.loads(result)
        assert "corrections" in parsed
