"""Unit tests for MistralProvider."""

import json
from unittest.mock import MagicMock, patch

import pytest

from thestill.core.llm_provider import MODEL_CONFIGS, MistralProvider, create_llm_provider


class TestMistralProviderInit:
    """Test MistralProvider initialization."""

    def test_init_with_default_model(self):
        """Test initialization with default model."""
        with patch("thestill.core.llm_provider.Mistral") as mock_mistral:
            provider = MistralProvider(api_key="test-key")
            assert provider.model == "mistral-large-latest"
            mock_mistral.assert_called_once_with(api_key="test-key")

    def test_init_with_custom_model(self):
        """Test initialization with custom model."""
        with patch("thestill.core.llm_provider.Mistral") as mock_mistral:
            provider = MistralProvider(api_key="test-key", model="mistral-small-latest")
            assert provider.model == "mistral-small-latest"
            mock_mistral.assert_called_once_with(api_key="test-key")


class TestMistralProviderChatCompletion:
    """Test MistralProvider chat completion methods."""

    @pytest.fixture
    def mock_provider(self):
        """Create a MistralProvider with mocked client."""
        with patch("thestill.core.llm_provider.Mistral") as mock_mistral:
            mock_client = MagicMock()
            mock_mistral.return_value = mock_client
            provider = MistralProvider(api_key="test-key")
            yield provider, mock_client

    def test_chat_completion_basic(self, mock_provider):
        """Test basic chat completion."""
        provider, mock_client = mock_provider

        # Mock response
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello, world!"
        mock_response.choices[0].finish_reason = "stop"
        mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
        mock_client.chat.complete.return_value = mock_response

        messages = [{"role": "user", "content": "Hello"}]
        result = provider.chat_completion(messages)

        assert result == "Hello, world!"
        mock_client.chat.complete.assert_called_once()
        call_kwargs = mock_client.chat.complete.call_args[1]
        assert call_kwargs["model"] == "mistral-large-latest"
        assert call_kwargs["messages"] == messages

    def test_chat_completion_with_temperature(self, mock_provider):
        """Test chat completion with temperature parameter."""
        provider, mock_client = mock_provider

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Response"
        mock_response.choices[0].finish_reason = "stop"
        mock_response.usage = None
        mock_client.chat.complete.return_value = mock_response

        messages = [{"role": "user", "content": "Hello"}]
        provider.chat_completion(messages, temperature=0.7)

        call_kwargs = mock_client.chat.complete.call_args[1]
        assert call_kwargs["temperature"] == 0.7

    def test_chat_completion_with_max_tokens(self, mock_provider):
        """Test chat completion with max_tokens parameter."""
        provider, mock_client = mock_provider

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Response"
        mock_response.choices[0].finish_reason = "stop"
        mock_response.usage = None
        mock_client.chat.complete.return_value = mock_response

        messages = [{"role": "user", "content": "Hello"}]
        provider.chat_completion(messages, max_tokens=1000)

        call_kwargs = mock_client.chat.complete.call_args[1]
        assert call_kwargs["max_tokens"] == 1000

    def test_chat_completion_with_json_format(self, mock_provider):
        """Test chat completion with JSON response format."""
        provider, mock_client = mock_provider

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"key": "value"}'
        mock_response.choices[0].finish_reason = "stop"
        mock_response.usage = None
        mock_client.chat.complete.return_value = mock_response

        messages = [{"role": "user", "content": "Return JSON"}]
        result = provider.chat_completion(messages, response_format={"type": "json_object"})

        assert result == '{"key": "value"}'
        call_kwargs = mock_client.chat.complete.call_args[1]
        assert call_kwargs["response_format"] == {"type": "json_object"}

    def test_chat_completion_empty_response(self, mock_provider):
        """Test chat completion with empty response content."""
        provider, mock_client = mock_provider

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = None
        mock_response.choices[0].finish_reason = "stop"
        mock_response.usage = None
        mock_client.chat.complete.return_value = mock_response

        messages = [{"role": "user", "content": "Hello"}]
        result = provider.chat_completion(messages)

        assert result == ""


class TestMistralProviderStreaming:
    """Test MistralProvider streaming methods."""

    @pytest.fixture
    def mock_provider(self):
        """Create a MistralProvider with mocked client."""
        with patch("thestill.core.llm_provider.Mistral") as mock_mistral:
            mock_client = MagicMock()
            mock_mistral.return_value = mock_client
            provider = MistralProvider(api_key="test-key")
            yield provider, mock_client

    def test_streaming_basic(self, mock_provider):
        """Test basic streaming chat completion."""
        provider, mock_client = mock_provider

        # Mock streaming response
        mock_events = []
        for chunk in ["Hello", ", ", "world", "!"]:
            event = MagicMock()
            event.data.choices = [MagicMock()]
            event.data.choices[0].delta = MagicMock()
            event.data.choices[0].delta.content = chunk
            mock_events.append(event)

        mock_client.chat.stream.return_value = iter(mock_events)

        messages = [{"role": "user", "content": "Hello"}]
        result = provider.chat_completion_streaming(messages)

        assert result == "Hello, world!"

    def test_streaming_with_callback(self, mock_provider):
        """Test streaming with on_chunk callback."""
        provider, mock_client = mock_provider

        mock_events = []
        for chunk in ["Hello", ", ", "world"]:
            event = MagicMock()
            event.data.choices = [MagicMock()]
            event.data.choices[0].delta = MagicMock()
            event.data.choices[0].delta.content = chunk
            mock_events.append(event)

        mock_client.chat.stream.return_value = iter(mock_events)

        chunks_received = []

        def on_chunk(chunk):
            chunks_received.append(chunk)

        messages = [{"role": "user", "content": "Hello"}]
        result = provider.chat_completion_streaming(messages, on_chunk=on_chunk)

        assert result == "Hello, world"
        assert chunks_received == ["Hello", ", ", "world"]

    def test_streaming_with_empty_chunks(self, mock_provider):
        """Test streaming handles empty chunks gracefully."""
        provider, mock_client = mock_provider

        mock_events = []
        # First event with content
        event1 = MagicMock()
        event1.data.choices = [MagicMock()]
        event1.data.choices[0].delta = MagicMock()
        event1.data.choices[0].delta.content = "Hello"
        mock_events.append(event1)

        # Event with no choices
        event2 = MagicMock()
        event2.data.choices = []
        mock_events.append(event2)

        # Event with None delta content
        event3 = MagicMock()
        event3.data.choices = [MagicMock()]
        event3.data.choices[0].delta = MagicMock()
        event3.data.choices[0].delta.content = None
        mock_events.append(event3)

        # Final event with content
        event4 = MagicMock()
        event4.data.choices = [MagicMock()]
        event4.data.choices[0].delta = MagicMock()
        event4.data.choices[0].delta.content = " world"
        mock_events.append(event4)

        mock_client.chat.stream.return_value = iter(mock_events)

        messages = [{"role": "user", "content": "Hello"}]
        result = provider.chat_completion_streaming(messages)

        assert result == "Hello world"


class TestMistralProviderStructuredOutput:
    """Test MistralProvider structured output methods."""

    @pytest.fixture
    def mock_provider(self):
        """Create a MistralProvider with mocked client."""
        with patch("thestill.core.llm_provider.Mistral") as mock_mistral:
            mock_client = MagicMock()
            mock_mistral.return_value = mock_client
            provider = MistralProvider(api_key="test-key")
            yield provider, mock_client

    def test_generate_structured_basic(self, mock_provider):
        """Test basic structured output generation."""
        from pydantic import BaseModel

        class TestModel(BaseModel):
            name: str
            value: int

        provider, mock_client = mock_provider

        # Mock parsed response
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.parsed = TestModel(name="test", value=42)
        mock_client.chat.parse.return_value = mock_response

        messages = [{"role": "user", "content": "Generate data"}]
        result = provider.generate_structured(messages, TestModel)

        assert isinstance(result, TestModel)
        assert result.name == "test"
        assert result.value == 42

    def test_generate_structured_with_temperature(self, mock_provider):
        """Test structured output with temperature."""
        from pydantic import BaseModel

        class TestModel(BaseModel):
            data: str

        provider, mock_client = mock_provider

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.parsed = TestModel(data="result")
        mock_client.chat.parse.return_value = mock_response

        messages = [{"role": "user", "content": "Generate"}]
        provider.generate_structured(messages, TestModel, temperature=0.5)

        call_kwargs = mock_client.chat.parse.call_args[1]
        assert call_kwargs["temperature"] == 0.5

    def test_generate_structured_fallback_to_json(self, mock_provider):
        """Test structured output falls back to JSON mode on parse failure."""
        from pydantic import BaseModel

        class TestModel(BaseModel):
            data: str

        provider, mock_client = mock_provider

        # First call (parse) fails with schema error
        mock_client.chat.parse.side_effect = Exception("parse schema error")

        # Second call (complete with JSON) succeeds
        mock_json_response = MagicMock()
        mock_json_response.choices = [MagicMock()]
        mock_json_response.choices[0].message.content = '{"data": "fallback"}'
        mock_json_response.choices[0].finish_reason = "stop"
        mock_json_response.usage = None
        mock_client.chat.complete.return_value = mock_json_response

        messages = [{"role": "user", "content": "Generate"}]
        result = provider.generate_structured(messages, TestModel)

        assert isinstance(result, TestModel)
        assert result.data == "fallback"


class TestMistralProviderCapabilities:
    """Test MistralProvider capability methods."""

    def test_supports_temperature(self):
        """Test supports_temperature returns True."""
        with patch("thestill.core.llm_provider.Mistral"):
            provider = MistralProvider(api_key="test-key")
            assert provider.supports_temperature() is True

    def test_supports_structured_output_known_model(self):
        """Test supports_structured_output for known models."""
        with patch("thestill.core.llm_provider.Mistral"):
            provider = MistralProvider(api_key="test-key", model="mistral-large-latest")
            assert provider.supports_structured_output() is True

    def test_supports_structured_output_unknown_model(self):
        """Test supports_structured_output defaults to True for unknown models."""
        with patch("thestill.core.llm_provider.Mistral"):
            provider = MistralProvider(api_key="test-key", model="unknown-model")
            assert provider.supports_structured_output() is True

    def test_get_model_name(self):
        """Test get_model_name returns current model."""
        with patch("thestill.core.llm_provider.Mistral"):
            provider = MistralProvider(api_key="test-key", model="mistral-small-latest")
            assert provider.get_model_name() == "mistral-small-latest"

    def test_get_model_display_name(self):
        """Test get_model_display_name returns formatted name."""
        with patch("thestill.core.llm_provider.Mistral"):
            provider = MistralProvider(api_key="test-key", model="mistral-large-latest")
            assert provider.get_model_display_name() == "Mistral mistral-large-latest"


class TestMistralProviderHealthCheck:
    """Test MistralProvider health check."""

    def test_health_check_success(self):
        """Test health check returns True on success."""
        with patch("thestill.core.llm_provider.Mistral") as mock_mistral:
            mock_client = MagicMock()
            mock_mistral.return_value = mock_client

            mock_response = MagicMock()
            mock_client.chat.complete.return_value = mock_response

            provider = MistralProvider(api_key="test-key")
            assert provider.health_check() is True

    def test_health_check_failure(self):
        """Test health check returns False on error."""
        with patch("thestill.core.llm_provider.Mistral") as mock_mistral:
            mock_client = MagicMock()
            mock_mistral.return_value = mock_client
            mock_client.chat.complete.side_effect = Exception("API error")

            provider = MistralProvider(api_key="test-key")
            assert provider.health_check() is False


class TestMistralProviderRetryLogic:
    """Test MistralProvider retry logic for rate limits and server errors."""

    @pytest.fixture
    def mock_provider(self):
        """Create a MistralProvider with mocked client."""
        with patch("thestill.core.llm_provider.Mistral") as mock_mistral:
            mock_client = MagicMock()
            mock_mistral.return_value = mock_client
            provider = MistralProvider(api_key="test-key")
            yield provider, mock_client

    @patch("thestill.core.llm_provider.time.sleep")
    def test_retry_on_rate_limit(self, mock_sleep, mock_provider):
        """Test retry logic on rate limit error."""
        provider, mock_client = mock_provider

        # First call fails with rate limit
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Success"
        mock_response.choices[0].finish_reason = "stop"
        mock_response.usage = None

        mock_client.chat.complete.side_effect = [
            Exception("Rate limit exceeded (429)"),
            mock_response,
        ]

        messages = [{"role": "user", "content": "Hello"}]
        result = provider.chat_completion(messages)

        assert result == "Success"
        assert mock_client.chat.complete.call_count == 2
        mock_sleep.assert_called()

    @patch("thestill.core.llm_provider.time.sleep")
    def test_retry_on_server_error(self, mock_sleep, mock_provider):
        """Test retry logic on server error."""
        provider, mock_client = mock_provider

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Success"
        mock_response.choices[0].finish_reason = "stop"
        mock_response.usage = None

        mock_client.chat.complete.side_effect = [
            Exception("Server error 500"),
            mock_response,
        ]

        messages = [{"role": "user", "content": "Hello"}]
        result = provider.chat_completion(messages)

        assert result == "Success"
        assert mock_client.chat.complete.call_count == 2


class TestMistralModelConfigs:
    """Test Mistral models are properly configured."""

    def test_mistral_large_in_configs(self):
        """Test mistral-large-latest is in MODEL_CONFIGS."""
        assert "mistral-large-latest" in MODEL_CONFIGS
        config = MODEL_CONFIGS["mistral-large-latest"]
        assert config.context_window == 256000
        assert config.max_output_tokens == 32768
        assert config.supports_structured_output is True

    def test_mistral_small_in_configs(self):
        """Test mistral-small-latest is in MODEL_CONFIGS."""
        assert "mistral-small-latest" in MODEL_CONFIGS
        config = MODEL_CONFIGS["mistral-small-latest"]
        assert config.context_window == 128000
        assert config.supports_structured_output is True

    def test_mistral_medium_in_configs(self):
        """Test mistral-medium-latest is in MODEL_CONFIGS."""
        assert "mistral-medium-latest" in MODEL_CONFIGS
        config = MODEL_CONFIGS["mistral-medium-latest"]
        assert config.context_window == 128000

    def test_ministral_8b_in_configs(self):
        """Test ministral-8b-latest is in MODEL_CONFIGS."""
        assert "ministral-8b-latest" in MODEL_CONFIGS
        config = MODEL_CONFIGS["ministral-8b-latest"]
        assert config.context_window == 262000

    def test_codestral_in_configs(self):
        """Test codestral-latest is in MODEL_CONFIGS."""
        assert "codestral-latest" in MODEL_CONFIGS
        config = MODEL_CONFIGS["codestral-latest"]
        assert config.context_window == 256000


class TestCreateLLMProviderMistral:
    """Test create_llm_provider factory function with Mistral."""

    def test_create_mistral_provider(self):
        """Test creating Mistral provider via factory."""
        with patch("thestill.core.llm_provider.Mistral"):
            provider = create_llm_provider(
                provider_type="mistral",
                mistral_api_key="test-key",
                mistral_model="mistral-small-latest",
            )
            assert isinstance(provider, MistralProvider)
            assert provider.model == "mistral-small-latest"

    def test_create_mistral_provider_default_model(self):
        """Test creating Mistral provider with default model."""
        with patch("thestill.core.llm_provider.Mistral"):
            provider = create_llm_provider(
                provider_type="mistral",
                mistral_api_key="test-key",
            )
            assert isinstance(provider, MistralProvider)
            assert provider.model == "mistral-large-latest"

    def test_create_mistral_provider_missing_key(self):
        """Test creating Mistral provider without API key raises error."""
        with pytest.raises(ValueError) as exc_info:
            create_llm_provider(provider_type="mistral", mistral_api_key="")
        assert "Mistral API key is required" in str(exc_info.value)

    def test_create_mistral_provider_case_insensitive(self):
        """Test provider_type is case insensitive."""
        with patch("thestill.core.llm_provider.Mistral"):
            provider = create_llm_provider(
                provider_type="MISTRAL",
                mistral_api_key="test-key",
            )
            assert isinstance(provider, MistralProvider)

    def test_invalid_provider_includes_mistral_in_error(self):
        """Test invalid provider error message includes mistral."""
        with pytest.raises(ValueError) as exc_info:
            create_llm_provider(provider_type="invalid")
        assert "mistral" in str(exc_info.value).lower()
