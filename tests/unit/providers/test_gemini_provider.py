"""Unit tests for GeminiProvider structured-output failure handling.

Covers the two failure modes observed against the hosted Gemma endpoints
(2026-07-29): RECITATION blocks crashing the whole episode instead of
triggering the per-batch fallback, and MAX_TOKENS truncation surfacing as
a bare JSONDecodeError.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from thestill.core.llm_provider import GeminiProvider
from thestill.utils.exceptions import ProhibitedContentError


class _PatchModel(BaseModel):
    text: str


def _mock_response(finish_reason: str, text):
    """Build a genai-like response with one candidate."""
    response = MagicMock()
    candidate = MagicMock()
    candidate.finish_reason = finish_reason
    response.candidates = [candidate]
    if text is None:
        # response.text raises; candidate parts empty
        type(response).text = property(lambda self: (_ for _ in ()).throw(ValueError("no text")))
        candidate.content.parts = []
    else:
        response.text = text
    return response


@pytest.fixture
def provider():
    with patch("thestill.core.llm_provider.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        p = GeminiProvider(api_key="test-key", model="gemini-3-flash-preview")
        yield p, mock_client


class TestGeminiStructuredFailureModes:
    def _call(self, p):
        return p.generate_structured(
            messages=[{"role": "user", "content": "clean"}],
            response_model=_PatchModel,
            max_tokens=1024,
        )

    def test_recitation_without_text_raises_prohibited_content(self, provider):
        """RECITATION with no usable text must raise ProhibitedContentError so
        the segmented cleaner's per-batch raw-text fallback (spec #41 B)
        engages instead of the whole episode failing."""
        p, client = provider
        client.models.generate_content.return_value = _mock_response("RECITATION", None)

        with pytest.raises(ProhibitedContentError):
            self._call(p)

    def test_recitation_with_text_proceeds(self, provider):
        p, client = provider
        client.models.generate_content.return_value = _mock_response("RECITATION", json.dumps({"text": "ok"}))

        assert self._call(p) == _PatchModel(text="ok")

    def test_max_tokens_with_truncated_json_raises_actionable_error(self, provider):
        """Partial text that cannot parse must explain the truncation cause,
        not surface as a bare JSONDecodeError."""
        p, client = provider
        client.models.generate_content.return_value = _mock_response("MAX_TOKENS", '{"text": "truncated mid-str')

        with pytest.raises(RuntimeError, match="max_tokens"):
            self._call(p)

    def test_max_tokens_with_parseable_json_succeeds(self, provider):
        """The rare case of valid JSON exactly at the cap still returns."""
        p, client = provider
        client.models.generate_content.return_value = _mock_response("MAX_TOKENS", json.dumps({"text": "complete"}))

        assert self._call(p) == _PatchModel(text="complete")

    def test_invalid_json_without_max_tokens_still_raises_decode_error(self, provider):
        p, client = provider
        client.models.generate_content.return_value = _mock_response("STOP", "not json")

        with pytest.raises(json.JSONDecodeError):
            self._call(p)


class TestThinkingLevelModelMatching:
    """thinking_level must apply to dotted Gemini 3.x generations.

    The old plain-prefix list matched only gemini-3-pro/gemini-3-flash, so
    gemini-3.5-flash and gemini-3.6-flash silently ignored the configured
    level and ran at the default (full) thinking — slower and pricier.
    """

    def _provider(self, model, level="minimal"):
        with patch("thestill.core.llm_provider.genai.Client"):
            return GeminiProvider(api_key="test-key", model=model, thinking_level=level)

    @pytest.mark.parametrize(
        "model",
        [
            "gemini-3-flash-preview",
            "gemini-3.5-flash",
            "gemini-3.6-flash",
        ],
    )
    def test_minimal_thinking_applies_to_flash_generations(self, model):
        config = self._provider(model)._get_thinking_config()
        assert config is not None, f"thinking config dropped for {model}"

    def test_pro_models_match_with_pro_levels(self):
        config = self._provider("gemini-3.5-pro", level="low")._get_thinking_config()
        assert config is not None

    @pytest.mark.parametrize("model", ["gemini-2.5-flash", "gemini-2.0-flash"])
    def test_pre_gemini3_models_get_no_thinking_config(self, model):
        assert self._provider(model)._get_thinking_config() is None

    def test_flash_detection_for_dotted_ids(self):
        assert self._provider("gemini-3.6-flash")._is_gemini_3_flash() is True
        assert self._provider("gemini-3.5-pro", level="low")._is_gemini_3_flash() is False


class TestGeminiUsageLogging:
    """Every billed Gemini call logs one ``llm_usage`` event at INFO.

    Asserted on a patched module logger, not ``structlog.testing.capture_logs``,
    which misses loggers that earlier tests already cached.
    """

    @staticmethod
    def _usage(prompt=1200, candidates=300, thoughts=40, cached=800):
        return SimpleNamespace(
            prompt_token_count=prompt,
            candidates_token_count=candidates,
            thoughts_token_count=thoughts,
            cached_content_token_count=cached,
        )

    @staticmethod
    def _usage_calls(fake_logger):
        return [c for c in fake_logger.info.call_args_list if c.args and c.args[0] == "llm_usage"]

    def test_structured_call_logs_token_counts(self, provider, monkeypatch):
        p, client = provider
        fake_logger = MagicMock()
        monkeypatch.setattr("thestill.core.llm_provider.logger", fake_logger)
        response = _mock_response("STOP", json.dumps({"text": "ok"}))
        response.usage_metadata = self._usage()
        client.models.generate_content.return_value = response

        p.generate_structured(messages=[{"role": "user", "content": "x"}], response_model=_PatchModel)

        (call,) = self._usage_calls(fake_logger)
        assert call.kwargs == {
            "provider": "gemini",
            "model": "gemini-3-flash-preview",
            "call": "structured",
            "input_tokens": 1200,
            "output_tokens": 300,
            "thinking_tokens": 40,
            "cached_tokens": 800,
        }

    def test_refused_call_is_still_logged(self, provider, monkeypatch):
        """A refusal is billed, so usage is logged before the finish-reason raise."""
        p, client = provider
        fake_logger = MagicMock()
        monkeypatch.setattr("thestill.core.llm_provider.logger", fake_logger)
        response = _mock_response("RECITATION", None)
        response.usage_metadata = self._usage(candidates=0)
        client.models.generate_content.return_value = response

        with pytest.raises(ProhibitedContentError):
            p.generate_structured(messages=[{"role": "user", "content": "x"}], response_model=_PatchModel)

        assert len(self._usage_calls(fake_logger)) == 1

    def test_continuation_logs_each_attempt(self, provider, monkeypatch):
        p, client = provider
        fake_logger = MagicMock()
        monkeypatch.setattr("thestill.core.llm_provider.logger", fake_logger)
        first = _mock_response("MAX_TOKENS", "part one ")
        first.usage_metadata = self._usage(candidates=100)
        second = _mock_response("STOP", "part two")
        second.usage_metadata = self._usage(candidates=50)
        client.models.generate_content.side_effect = [first, second]

        text = p.chat_completion_with_continuation(messages=[{"role": "user", "content": "x"}])

        assert text == "part one part two"
        assert [c.kwargs["output_tokens"] for c in self._usage_calls(fake_logger)] == [100, 50]
        assert {c.kwargs["call"] for c in self._usage_calls(fake_logger)} == {"chat"}

    def test_stream_logs_usage_from_final_chunk(self, provider, monkeypatch):
        p, client = provider
        fake_logger = MagicMock()
        monkeypatch.setattr("thestill.core.llm_provider.logger", fake_logger)
        chunk = MagicMock()
        chunk.text = "hello"
        chunk.candidates = [MagicMock(finish_reason="STOP")]
        chunk.usage_metadata = self._usage(candidates=5)
        client.models.generate_content_stream.return_value = iter([chunk])

        assert p.chat_completion_streaming(messages=[{"role": "user", "content": "x"}]) == "hello"

        (call,) = self._usage_calls(fake_logger)
        assert call.kwargs["call"] == "stream"
        assert call.kwargs["output_tokens"] == 5

    def test_missing_counts_log_as_zero_and_absent_metadata_logs_nothing(self, provider, monkeypatch):
        p, client = provider
        fake_logger = MagicMock()
        monkeypatch.setattr("thestill.core.llm_provider.logger", fake_logger)
        with_none = _mock_response("STOP", "a")
        with_none.usage_metadata = self._usage(thoughts=None, cached=None)
        without = _mock_response("STOP", "b")
        without.usage_metadata = None
        client.models.generate_content.side_effect = [with_none, without]

        p.chat_completion(messages=[{"role": "user", "content": "x"}])
        p.chat_completion(messages=[{"role": "user", "content": "x"}])

        (call,) = self._usage_calls(fake_logger)
        assert call.kwargs["thinking_tokens"] == 0
        assert call.kwargs["cached_tokens"] == 0
