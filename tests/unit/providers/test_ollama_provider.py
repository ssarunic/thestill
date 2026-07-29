"""Unit tests for OllamaProvider structured output (schema-constrained decoding)."""

import json
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from thestill.core.llm_provider import OllamaProvider


class _FactsModel(BaseModel):
    names: list[str]
    count: int


@pytest.fixture
def mock_provider():
    """OllamaProvider with a mocked ollama client."""
    with patch("thestill.core.llm_provider.ollama.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        provider = OllamaProvider(base_url="http://localhost:11434", model="test-model")
        yield provider, mock_client


class TestOllamaStructuredOutput:
    def test_generate_structured_passes_json_schema_as_format(self, mock_provider):
        """The Pydantic JSON schema must reach the client as ``format`` so
        Ollama constrains decoding to it (the fix for shape mismatches like
        a dict where the schema wants a list)."""
        provider, mock_client = mock_provider
        mock_client.generate.return_value = {
            "response": json.dumps({"names": ["a"], "count": 1}),
            "done": True,
        }

        result = provider.generate_structured(
            messages=[{"role": "user", "content": "extract"}],
            response_model=_FactsModel,
        )

        assert result == _FactsModel(names=["a"], count=1)
        call_kwargs = mock_client.generate.call_args[1]
        assert call_kwargs["format"] == _FactsModel.model_json_schema()

    def test_chat_completion_json_object_keeps_legacy_json_mode(self, mock_provider):
        """response_format json_object still maps to Ollama's plain 'json' mode."""
        provider, mock_client = mock_provider
        mock_client.generate.return_value = {"response": "{}", "done": True}

        provider.chat_completion(
            messages=[{"role": "user", "content": "hi"}],
            response_format={"type": "json_object"},
        )

        assert mock_client.generate.call_args[1]["format"] == "json"

    def test_chat_completion_without_response_format_passes_no_format(self, mock_provider):
        provider, mock_client = mock_provider
        mock_client.generate.return_value = {"response": "hello", "done": True}

        provider.chat_completion(messages=[{"role": "user", "content": "hi"}])

        assert mock_client.generate.call_args[1]["format"] is None

    def test_generate_structured_invalid_json_raises(self, mock_provider):
        """Unparseable output still surfaces as an error (Pydantic remains
        the second line of defence behind constrained decoding)."""
        provider, mock_client = mock_provider
        mock_client.generate.return_value = {"response": "not json", "done": True}

        with pytest.raises(json.JSONDecodeError):
            provider.generate_structured(
                messages=[{"role": "user", "content": "extract"}],
                response_model=_FactsModel,
            )

    def test_supports_structured_output(self, mock_provider):
        provider, _ = mock_provider
        assert provider.supports_structured_output() is True
