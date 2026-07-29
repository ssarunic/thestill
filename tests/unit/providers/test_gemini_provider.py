"""Unit tests for GeminiProvider structured-output failure handling.

Covers the two failure modes observed against the hosted Gemma endpoints
(2026-07-29): RECITATION blocks crashing the whole episode instead of
triggering the per-batch fallback, and MAX_TOKENS truncation surfacing as
a bare JSONDecodeError.
"""

import json
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
