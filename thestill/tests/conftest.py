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
Pytest fixtures for thestill tests.

Provides MockLLMProvider to avoid calling real LLM APIs during tests,
making tests faster, more reliable, and free from API costs.
"""

from typing import Dict, List, Optional

import pytest

from thestill.core.llm_provider import LLMProvider


class MockLLMProvider(LLMProvider):
    """
    Mock LLM provider for testing.

    Returns pre-configured responses based on message content patterns.
    Tracks call count and last messages for assertions.

    Usage:
        provider = MockLLMProvider()
        provider.add_response("entity", '{"entities": []}')
        result = provider.chat_completion([{"role": "user", "content": "extract entities"}])
    """

    def __init__(self, responses: Optional[Dict[str, str]] = None, model_name: str = "mock-model"):
        """
        Initialize mock provider.

        Args:
            responses: Dict mapping content patterns to response strings.
                       If a message contains the pattern (case-insensitive), return the response.
            model_name: Model name to return from get_model_name()
        """
        self.responses = responses or {}
        self._model_name = model_name
        self.call_count = 0
        self.last_messages: Optional[List[Dict[str, str]]] = None
        self.last_temperature: Optional[float] = None
        self.last_max_tokens: Optional[int] = None

    def add_response(self, pattern: str, response: str) -> None:
        """
        Add a response pattern.

        Args:
            pattern: Case-insensitive pattern to match in message content
            response: Response string to return when pattern matches
        """
        self.responses[pattern.lower()] = response

    def set_default_responses(self) -> None:
        """Set sensible default responses for transcript cleaning."""
        # Phase 1: Corrections response
        self.add_response(
            "corrections",
            '{"corrections": [{"type": "spelling", "original": "teh", "corrected": "the"}]}',
        )

        # Phase 2: Speaker identification response
        self.add_response(
            "speaker",
            '{"speaker_mapping": {"SPEAKER_00": "Host", "SPEAKER_01": "Guest"}}',
        )

        # Phase 3: Cleaned transcript response
        self.add_response(
            "clean",
            "**Host:** Welcome to the podcast.\n\n**Guest:** Thanks for having me.",
        )

        # Entity extraction response
        self.add_response(
            "entity",
            '{"entities": [{"term": "OpenAI", "type": "company", "context": "AI research lab"}]}',
        )

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        Return a mock response based on message content patterns.

        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature (ignored, stored for assertions)
            max_tokens: Maximum tokens (ignored, stored for assertions)
            response_format: Response format (ignored)

        Returns:
            Matching response or default JSON response
        """
        self.call_count += 1
        self.last_messages = messages
        self.last_temperature = temperature
        self.last_max_tokens = max_tokens

        # Get last user message content
        content = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                break

        content_lower = content.lower()

        # Check for pattern matches
        for pattern, response in self.responses.items():
            if pattern in content_lower:
                return response

        # Default response (valid JSON for most cases)
        return '{"result": "mock response"}'

    def supports_temperature(self) -> bool:
        """Mock always supports temperature."""
        return True

    def health_check(self) -> bool:
        """Mock is always healthy."""
        return True

    def get_model_name(self) -> str:
        """Return configured model name."""
        return self._model_name

    def get_model_display_name(self) -> str:
        """Return human-readable model name."""
        return f"Mock ({self._model_name})"


@pytest.fixture
def mock_llm_provider() -> MockLLMProvider:
    """
    Fixture providing a basic mock LLM provider.

    Usage:
        def test_something(mock_llm_provider):
            mock_llm_provider.add_response("pattern", "response")
            result = my_function(mock_llm_provider)
            assert mock_llm_provider.call_count == 1
    """
    return MockLLMProvider()


@pytest.fixture
def mock_llm_provider_with_defaults() -> MockLLMProvider:
    """
    Fixture providing a mock LLM provider with default transcript cleaning responses.

    Pre-configured with responses for:
    - Entity extraction
    - Corrections identification
    - Speaker mapping
    - Cleaned transcript generation

    Usage:
        def test_transcript_cleaning(mock_llm_provider_with_defaults):
            processor = TranscriptCleaningProcessor(mock_llm_provider_with_defaults)
            result = processor.clean_transcript(...)
    """
    provider = MockLLMProvider()
    provider.set_default_responses()
    return provider


@pytest.fixture
def mock_anthropic_provider() -> MockLLMProvider:
    """
    Fixture providing a mock Anthropic-like provider.

    Pre-configured with responses suitable for Anthropic Claude models.
    """
    provider = MockLLMProvider(model_name="claude-3-5-sonnet-20241022")
    provider.set_default_responses()
    return provider
