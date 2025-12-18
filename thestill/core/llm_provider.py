# Copyright 2025 thestill.me
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
Abstract LLM provider interface and implementations for OpenAI, Ollama, Gemini, and Anthropic.
"""

import json
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, NamedTuple, Optional, Type, TypeVar

import ollama
from anthropic import Anthropic, APIStatusError, BadRequestError, RateLimitError
from google import genai
from google.genai import types as genai_types
from openai import OpenAI
from pydantic import BaseModel

# TypeVar for generic structured output return type
T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger(__name__)

# Default max output tokens for unknown models
DEFAULT_MAX_OUTPUT_TOKENS = 8192


class ModelLimits(NamedTuple):
    """Rate limits and constraints for LLM models"""

    tpm: int  # Tokens per minute
    rpm: int  # Requests per minute
    tpd: int  # Tokens per day
    context_window: int  # Maximum context window size (input)
    max_output_tokens: int = 4096  # Maximum output tokens the model can generate
    supports_temperature: bool = True  # Whether model supports custom temperature
    supports_structured_output: bool = False  # Whether model supports schema-validated JSON output
    structured_output_beta: Optional[str] = None  # Beta header required for structured output (e.g., Anthropic)


# Model rate limits, context windows, output token limits, and structured output support
# Sources:
# - OpenAI: https://platform.openai.com/docs/models
# - OpenAI Structured Outputs: https://platform.openai.com/docs/guides/structured-outputs
# - Anthropic: https://docs.anthropic.com/en/docs/about-claude/models
# - Anthropic Structured Outputs: https://docs.anthropic.com/en/docs/build-with-claude/structured-outputs
# - Google: https://ai.google.dev/gemini-api/docs/models
# - Google Structured Outputs: https://ai.google.dev/gemini-api/docs/structured-output
#
# Structured output notes:
# - OpenAI: Supported via response_format with JSON schema (gpt-4o+), NOT supported for reasoning models (o1, o3, gpt-5)
# - Anthropic: Supported via beta header "structured-outputs-2025-11-13" (Claude 4.5+)
# - Gemini: Supported via response_schema parameter (all models)
# - Ollama: No native schema validation, falls back to JSON mode + Pydantic validation
MODEL_CONFIGS: Dict[str, ModelLimits] = {
    # OpenAI GPT-5.1 series (November 2025) - adaptive reasoning models
    # Uses reasoning_effort parameter ('none', 'low', 'medium', 'high')
    # Reasoning models do NOT support structured outputs
    "gpt-5.1": ModelLimits(
        tpm=500000,
        rpm=500,
        tpd=5000000,
        context_window=128000,
        max_output_tokens=16384,
        supports_temperature=False,
        supports_structured_output=False,
    ),
    "gpt-5.1-codex": ModelLimits(
        tpm=500000,
        rpm=500,
        tpd=5000000,
        context_window=128000,
        max_output_tokens=16384,
        supports_temperature=False,
        supports_structured_output=False,
    ),
    "gpt-5.1-codex-mini": ModelLimits(
        tpm=500000,
        rpm=500,
        tpd=5000000,
        context_window=128000,
        max_output_tokens=16384,
        supports_temperature=False,
        supports_structured_output=False,
    ),
    "gpt-5.1-codex-max": ModelLimits(
        tpm=500000,
        rpm=500,
        tpd=5000000,
        context_window=1000000,
        max_output_tokens=32768,
        supports_temperature=False,
        supports_structured_output=False,
    ),
    # OpenAI GPT-5.0 series - reasoning models, no structured output
    "gpt-5": ModelLimits(
        tpm=500000,
        rpm=500,
        tpd=1500000,
        context_window=128000,
        max_output_tokens=16384,
        supports_temperature=False,
        supports_structured_output=False,
    ),
    "gpt-5-mini": ModelLimits(
        tpm=500000,
        rpm=500,
        tpd=5000000,
        context_window=128000,
        max_output_tokens=16384,
        supports_temperature=False,
        supports_structured_output=False,
    ),
    "gpt-5-nano": ModelLimits(
        tpm=200000,
        rpm=500,
        tpd=2000000,
        context_window=128000,
        max_output_tokens=16384,
        supports_temperature=False,
        supports_structured_output=False,
    ),
    # OpenAI GPT-4.1 series - supports structured output
    "gpt-4.1": ModelLimits(
        tpm=30000,
        rpm=500,
        tpd=900000,
        context_window=128000,
        max_output_tokens=16384,
        supports_temperature=True,
        supports_structured_output=True,
    ),
    "gpt-4.1-mini": ModelLimits(
        tpm=200000,
        rpm=500,
        tpd=2000000,
        context_window=128000,
        max_output_tokens=16384,
        supports_temperature=True,
        supports_structured_output=True,
    ),
    "gpt-4.1-nano": ModelLimits(
        tpm=200000,
        rpm=500,
        tpd=2000000,
        context_window=128000,
        max_output_tokens=16384,
        supports_temperature=True,
        supports_structured_output=True,
    ),
    # OpenAI o-series reasoning models - no structured output
    "o3": ModelLimits(
        tpm=30000,
        rpm=500,
        tpd=90000,
        context_window=128000,
        max_output_tokens=16384,
        supports_temperature=False,
        supports_structured_output=False,
    ),
    "o4-mini": ModelLimits(
        tpm=200000,
        rpm=500,
        tpd=2000000,
        context_window=128000,
        max_output_tokens=16384,
        supports_temperature=False,
        supports_structured_output=False,
    ),
    # OpenAI GPT-4o series - supports structured output (Aug 2024+)
    "gpt-4o": ModelLimits(
        tpm=30000,
        rpm=500,
        tpd=90000,
        context_window=128000,
        max_output_tokens=16384,
        supports_temperature=True,
        supports_structured_output=True,
    ),
    "gpt-4o-mini": ModelLimits(
        tpm=200000,
        rpm=500,
        tpd=2000000,
        context_window=128000,
        max_output_tokens=16384,
        supports_temperature=True,
        supports_structured_output=True,
    ),
    "gpt-4-turbo": ModelLimits(
        tpm=30000,
        rpm=500,
        tpd=90000,
        context_window=128000,
        max_output_tokens=4096,
        supports_temperature=True,
        supports_structured_output=True,
    ),
    "gpt-4-turbo-preview": ModelLimits(
        tpm=30000,
        rpm=500,
        tpd=90000,
        context_window=128000,
        max_output_tokens=4096,
        supports_temperature=True,
        supports_structured_output=True,
    ),
    # Anthropic Claude Opus 4.5 (November 2025) - supports structured output (beta)
    # 200K input, 64K output tokens, uses effort parameter (low/medium/high)
    "claude-opus-4-5-20251101": ModelLimits(
        tpm=400000,
        rpm=1000,
        tpd=5000000,
        context_window=200000,
        max_output_tokens=64000,
        supports_temperature=True,
        supports_structured_output=True,
        structured_output_beta="structured-outputs-2025-11-13",
    ),
    # Anthropic Claude 4.5 series - supports structured output (beta)
    "claude-sonnet-4-5-20250929": ModelLimits(
        tpm=400000,
        rpm=1000,
        tpd=5000000,
        context_window=200000,
        max_output_tokens=64000,
        supports_temperature=True,
        supports_structured_output=True,
        structured_output_beta="structured-outputs-2025-11-13",
    ),
    "claude-haiku-4-5-20251015": ModelLimits(
        tpm=400000,
        rpm=1000,
        tpd=5000000,
        context_window=200000,
        max_output_tokens=8192,
        supports_temperature=True,
        supports_structured_output=True,
        structured_output_beta="structured-outputs-2025-11-13",
    ),
    # Anthropic Claude 4.x models - no structured output (pre-beta)
    "claude-sonnet-4-20250514": ModelLimits(
        tpm=400000,
        rpm=1000,
        tpd=5000000,
        context_window=200000,
        max_output_tokens=8192,
        supports_temperature=True,
        supports_structured_output=False,
    ),
    "claude-opus-4-20250514": ModelLimits(
        tpm=400000,
        rpm=1000,
        tpd=5000000,
        context_window=200000,
        max_output_tokens=8192,
        supports_temperature=True,
        supports_structured_output=False,
    ),
    "claude-opus-4-1-20250925": ModelLimits(
        tpm=400000,
        rpm=1000,
        tpd=5000000,
        context_window=200000,
        max_output_tokens=8192,
        supports_temperature=True,
        supports_structured_output=True,
        structured_output_beta="structured-outputs-2025-11-13",
    ),
    # Legacy Claude 3.x models - no structured output
    "claude-3-5-sonnet-20241022": ModelLimits(
        tpm=400000,
        rpm=1000,
        tpd=5000000,
        context_window=200000,
        max_output_tokens=8192,
        supports_temperature=True,
        supports_structured_output=False,
    ),
    "claude-3-5-haiku-20241022": ModelLimits(
        tpm=400000,
        rpm=1000,
        tpd=5000000,
        context_window=200000,
        max_output_tokens=8192,
        supports_temperature=True,
        supports_structured_output=False,
    ),
    "claude-3-opus-20240229": ModelLimits(
        tpm=400000,
        rpm=1000,
        tpd=5000000,
        context_window=200000,
        max_output_tokens=4096,
        supports_temperature=True,
        supports_structured_output=False,
    ),
    "claude-3-sonnet-20240229": ModelLimits(
        tpm=400000,
        rpm=1000,
        tpd=5000000,
        context_window=200000,
        max_output_tokens=4096,
        supports_temperature=True,
        supports_structured_output=False,
    ),
    "claude-3-haiku-20240307": ModelLimits(
        tpm=400000,
        rpm=1000,
        tpd=5000000,
        context_window=200000,
        max_output_tokens=4096,
        supports_temperature=True,
        supports_structured_output=False,
    ),
    # Google Gemini 3 (November/December 2025) - supports structured output
    # Uses thinking_level parameter for reasoning depth
    # Pro supports: "low", "high" (default)
    # Flash supports: "minimal", "low", "medium", "high" (default)
    # Pricing: Pro: $2/$12 per 1M tokens (<200k), $4/$18 (>200k)
    #          Flash: $0.50/$3.00 per 1M tokens
    "gemini-3-pro-preview": ModelLimits(
        tpm=500000,
        rpm=1000,
        tpd=10000000,
        context_window=1048576,  # 1M input
        max_output_tokens=65536,  # 64K output
        supports_temperature=True,
        supports_structured_output=True,
    ),
    "gemini-3-flash-preview": ModelLimits(
        tpm=500000,
        rpm=1000,
        tpd=10000000,
        context_window=1048576,  # 1M input
        max_output_tokens=65536,  # 64K output
        supports_temperature=True,
        supports_structured_output=True,
    ),
    # Note: gemini-3-pro-image-preview has different limits (65k input / 32k output)
    # Google Gemini 2.5 series - supports structured output
    "gemini-2.5-pro": ModelLimits(
        tpm=500000,
        rpm=1000,
        tpd=10000000,
        context_window=1048576,
        max_output_tokens=65536,
        supports_temperature=True,
        supports_structured_output=True,
    ),
    "gemini-2.5-flash": ModelLimits(
        tpm=500000,
        rpm=1000,
        tpd=10000000,
        context_window=1048576,
        max_output_tokens=65536,
        supports_temperature=True,
        supports_structured_output=True,
    ),
    "gemini-2.5-flash-lite": ModelLimits(
        tpm=500000,
        rpm=1000,
        tpd=10000000,
        context_window=1048576,
        max_output_tokens=65536,
        supports_temperature=True,
        supports_structured_output=True,
    ),
    # Google Gemini 2.0 series - supports structured output
    "gemini-2.0-flash-exp": ModelLimits(
        tpm=500000,
        rpm=1000,
        tpd=10000000,
        context_window=1048576,
        max_output_tokens=8192,
        supports_temperature=True,
        supports_structured_output=True,
    ),
    "gemini-2.0-flash": ModelLimits(
        tpm=500000,
        rpm=1000,
        tpd=10000000,
        context_window=1048576,
        max_output_tokens=8192,
        supports_temperature=True,
        supports_structured_output=True,
    ),
    # Ollama/Gemma 3 models - no native structured output (uses JSON mode + Pydantic fallback)
    # Using very high tpm/rpm/tpd since there are no actual limits
    "gemma3:270m": ModelLimits(
        tpm=1000000,
        rpm=10000,
        tpd=100000000,
        context_window=32000,
        max_output_tokens=8192,
        supports_temperature=True,
        supports_structured_output=False,
    ),
    "gemma3:1b": ModelLimits(
        tpm=1000000,
        rpm=10000,
        tpd=100000000,
        context_window=32000,
        max_output_tokens=8192,
        supports_temperature=True,
        supports_structured_output=False,
    ),
    "gemma3:4b": ModelLimits(
        tpm=1000000,
        rpm=10000,
        tpd=100000000,
        context_window=128000,
        max_output_tokens=8192,
        supports_temperature=True,
        supports_structured_output=False,
    ),
    "gemma3:12b": ModelLimits(
        tpm=1000000,
        rpm=10000,
        tpd=100000000,
        context_window=128000,
        max_output_tokens=8192,
        supports_temperature=True,
        supports_structured_output=False,
    ),
    "gemma3:27b": ModelLimits(
        tpm=1000000,
        rpm=10000,
        tpd=100000000,
        context_window=128000,
        max_output_tokens=8192,
        supports_temperature=True,
        supports_structured_output=False,
    ),
}


def get_max_output_tokens(model_name: str) -> int:
    """
    Get the maximum output tokens for a model from MODEL_CONFIGS.

    This is a module-level helper function. For provider instances,
    prefer using provider.get_max_output_tokens() method instead.

    Args:
        model_name: The model name (e.g., "claude-sonnet-4-5-20250929")

    Returns:
        The max_output_tokens for the model, or DEFAULT_MAX_OUTPUT_TOKENS if unknown
    """
    # Check for exact match first
    if model_name in MODEL_CONFIGS:
        return MODEL_CONFIGS[model_name].max_output_tokens

    # Check for partial match (model names often have date suffixes)
    for config_name, limits in MODEL_CONFIGS.items():
        # Match by prefix (e.g., "claude-sonnet-4-5" matches "claude-sonnet-4-5-20250929")
        if model_name.startswith(config_name.rsplit("-", 1)[0]):
            return limits.max_output_tokens
        if config_name.startswith(model_name.rsplit("-", 1)[0]):
            return limits.max_output_tokens

    # Fallback for common provider patterns
    model_lower = model_name.lower()
    if "gemini" in model_lower:
        return 65536  # Gemini 2.x default
    elif "gpt-4" in model_lower:
        return 16384  # GPT-4o default
    elif "claude" in model_lower:
        return 64000  # Claude 4.x default

    logger.warning(f"Model '{model_name}' not found in MODEL_CONFIGS, " f"using default {DEFAULT_MAX_OUTPUT_TOKENS}")
    return DEFAULT_MAX_OUTPUT_TOKENS


class LLMProvider(ABC):
    """Abstract base class for LLM providers"""

    @abstractmethod
    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        Generate a chat completion.

        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature (0-1)
            max_tokens: Maximum tokens to generate
            response_format: Response format specification

        Returns:
            The generated text response
        """
        pass

    @abstractmethod
    def supports_temperature(self) -> bool:
        """Check if this provider/model supports custom temperature"""
        pass

    @abstractmethod
    def health_check(self) -> bool:
        """Check if the provider is available and healthy"""
        pass

    @abstractmethod
    def get_model_name(self) -> str:
        """Get the current model name"""
        pass

    @abstractmethod
    def get_model_display_name(self) -> str:
        """Get human-readable model name for display"""
        pass

    @abstractmethod
    def supports_structured_output(self) -> bool:
        """Check if this provider/model supports schema-validated structured output"""
        pass

    def get_max_output_tokens(self) -> int:
        """
        Get the maximum output tokens for the current model.

        Uses MODEL_CONFIGS as the source of truth.
        Falls back to DEFAULT_MAX_OUTPUT_TOKENS for unknown models.

        Returns:
            Maximum output tokens for this provider's model
        """
        return get_max_output_tokens(self.get_model_name())

    @abstractmethod
    def generate_structured(
        self,
        messages: List[Dict[str, str]],
        response_model: Type[T],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> T:
        """
        Generate a structured response matching the Pydantic model schema.

        This method guarantees the response will be valid JSON matching the schema
        defined by response_model. For providers that support native structured output,
        the schema is enforced by the API. For providers without native support,
        this falls back to JSON mode with Pydantic validation.

        Args:
            messages: List of message dicts with 'role' and 'content'
            response_model: Pydantic model class defining the expected response schema
            temperature: Sampling temperature (0-1)
            max_tokens: Maximum tokens to generate

        Returns:
            Parsed and validated Pydantic model instance

        Raises:
            ValueError: If the model refuses to generate or response is invalid
            json.JSONDecodeError: If response cannot be parsed as JSON (fallback mode)
        """
        pass


class OpenAIProvider(LLMProvider):
    """OpenAI API provider"""

    # Models that don't support custom temperature and response_format
    # GPT-5.x series uses adaptive reasoning - set reasoning_effort='none' for non-reasoning behavior
    REASONING_MODELS = [
        "o1",
        "o1-preview",
        "o1-mini",
        "o3",
        "o4-mini",  # o-series reasoning models
        "gpt-5",
        "gpt-5-mini",
        "gpt-5-turbo",
        "gpt-5-nano",  # GPT-5.0 series
        "gpt-5.1",
        "gpt-5.1-codex",
        "gpt-5.1-codex-mini",
        "gpt-5.1-codex-max",  # GPT-5.1 series (Nov 2025)
    ]

    def __init__(self, api_key: str, model: str = "gpt-5.1"):
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, str]] = None,
    ) -> str:
        """Generate a chat completion using OpenAI API"""
        return self._create_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            enable_continuation=False,
        )

    def chat_completion_with_continuation(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, str]] = None,
        max_attempts: int = 3,
    ) -> str:
        """
        Generate a complete chat completion with automatic continuation on truncation.

        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature (0-1)
            max_tokens: Maximum tokens to generate per attempt
            response_format: Response format specification
            max_attempts: Maximum continuation attempts (default: 3)

        Returns:
            The complete generated text response
        """
        return self._create_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            enable_continuation=True,
            max_attempts=max_attempts,
        )

    def _create_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, str]] = None,
        enable_continuation: bool = False,
        max_attempts: int = 3,
    ) -> str:
        """Internal method to create completions with optional continuation"""
        full_response = ""
        current_messages = messages.copy()

        for attempt in range(max_attempts):
            params: Dict[str, Any] = {
                "model": self.model,
                "messages": current_messages,
            }

            is_reasoning_model = self._is_reasoning_model()

            # Only add temperature if supported (reasoning models don't support it)
            if temperature is not None and not is_reasoning_model:
                params["temperature"] = temperature

            # Add max_tokens if specified
            if max_tokens is not None:
                params["max_completion_tokens"] = max_tokens

            # Only add response_format for non-reasoning models
            # Reasoning models (o1, gpt-5) don't support structured output via response_format
            if response_format is not None and not is_reasoning_model:
                params["response_format"] = response_format

            response = self.client.chat.completions.create(**params)

            # Extract content
            current_text = response.choices[0].message.content or ""
            full_response += current_text

            # Log token usage if available
            if hasattr(response, "usage") and response.usage:
                logger.debug(
                    f"Token usage - Input: {response.usage.prompt_tokens}, Output: {response.usage.completion_tokens}"
                )

            # Check finish_reason
            finish_reason = response.choices[0].finish_reason

            if finish_reason == "length":  # Equivalent to max_tokens
                if enable_continuation and attempt < max_attempts - 1:
                    logger.info(f"Response truncated, continuing (attempt {attempt + 2}/{max_attempts})...")
                    # Continue from where we left off
                    current_messages = messages + [
                        {"role": "assistant", "content": full_response},
                        {"role": "user", "content": "Please continue from where you left off."},
                    ]
                    continue
                else:
                    logger.warning(
                        f"OpenAI response truncated due to max_tokens limit (limit: {max_tokens or 'default'})"
                    )
                    if not enable_continuation:
                        logger.info("Consider using smaller chunks or chat_completion_with_continuation()")
                    break
            elif finish_reason == "stop":
                # Normal completion
                break
            elif finish_reason == "content_filter":
                logger.warning("OpenAI content filter triggered")
                break
            else:
                # Other finish reasons (function_call, tool_calls, etc.)
                break

        return full_response

    def _is_reasoning_model(self) -> bool:
        """Check if the current model is a reasoning model (o1, gpt-5 series)"""
        for reasoning_model in self.REASONING_MODELS:
            if self.model.startswith(reasoning_model):
                return True
        return False

    def supports_temperature(self) -> bool:
        """Check if the current model supports custom temperature"""
        return not self._is_reasoning_model()

    def health_check(self) -> bool:
        """Check if OpenAI API is accessible"""
        try:
            # Simple test with minimal tokens
            self.client.chat.completions.create(
                model=self.model, messages=[{"role": "user", "content": "test"}], max_completion_tokens=1
            )
            return True
        except Exception as e:
            logger.error(f"OpenAI health check failed: {e}")
            return False

    def get_model_name(self) -> str:
        """Get the current model name"""
        return self.model

    def get_model_display_name(self) -> str:
        """Get human-readable model name for display"""
        return f"OpenAI {self.model}"

    def supports_structured_output(self) -> bool:
        """Check if this model supports structured output (reasoning models don't)"""
        return not self._is_reasoning_model()

    def generate_structured(
        self,
        messages: List[Dict[str, str]],
        response_model: Type[T],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> T:
        """
        Generate a structured response using OpenAI's native structured output.

        Uses client.beta.chat.completions.parse() for schema-validated JSON output.
        Falls back to JSON mode + Pydantic validation for reasoning models.
        """
        if self._is_reasoning_model():
            # Reasoning models don't support structured output, use fallback
            logger.warning(f"Model {self.model} doesn't support structured output, using JSON mode fallback")
            response = self.chat_completion(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
            data = json.loads(response)
            return response_model(**data)

        # Use native structured output
        params: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "response_format": response_model,
        }

        if temperature is not None:
            params["temperature"] = temperature
        if max_tokens is not None:
            params["max_completion_tokens"] = max_tokens

        completion = self.client.beta.chat.completions.parse(**params)

        # Check for model refusal
        if completion.choices[0].message.refusal:
            raise ValueError(f"Model refused to generate: {completion.choices[0].message.refusal}")

        parsed = completion.choices[0].message.parsed
        if parsed is None:
            raise ValueError("Model returned empty parsed response")

        return parsed

    def chat_completion_streaming(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, str]] = None,
        on_chunk: Optional[callable] = None,
    ) -> str:
        """
        Generate a chat completion with streaming support.

        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature (0-1)
            max_tokens: Maximum tokens to generate
            response_format: Response format specification
            on_chunk: Optional callback function(chunk_text) called for each chunk

        Returns:
            The complete generated text response
        """
        params: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }

        is_reasoning_model = self._is_reasoning_model()

        # Only add temperature if supported (reasoning models don't support it)
        if temperature is not None and not is_reasoning_model:
            params["temperature"] = temperature

        # Add max_tokens if specified
        if max_tokens is not None:
            params["max_completion_tokens"] = max_tokens

        # Only add response_format for non-reasoning models
        if response_format is not None and not is_reasoning_model:
            params["response_format"] = response_format

        full_response = ""
        stream = self.client.chat.completions.create(**params)

        for chunk in stream:
            if chunk.choices and len(chunk.choices) > 0:
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    full_response += delta.content
                    if on_chunk:
                        on_chunk(delta.content)

        return full_response


class OllamaProvider(LLMProvider):
    """Ollama local LLM provider using official SDK"""

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "gemma3:4b"):
        self.model = model
        # Create Ollama client with custom host if provided
        self.client = ollama.Client(host=base_url)

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, str]] = None,
    ) -> str:
        """Generate a chat completion using Ollama SDK"""
        # Convert chat messages to a single prompt for generate API
        # (Ollama's chat API might not be available in all versions)
        prompt = self._messages_to_prompt(messages)

        # Build options dict
        options = {}
        if temperature is not None:
            options["temperature"] = temperature
        if max_tokens is not None:
            options["num_predict"] = max_tokens

        # Increase context window to prevent truncation
        # num_ctx controls the context window size
        if "num_ctx" not in options:
            options["num_ctx"] = 32768  # Increase context window for longer responses

        try:
            # Use generate API for better compatibility
            response = self.client.generate(
                model=self.model,
                prompt=prompt,
                stream=False,
                format="json" if response_format and response_format.get("type") == "json_object" else None,
                options=options if options else None,
            )

            # Check for truncation/completion status
            # Ollama returns 'done' field and may include 'done_reason'
            response_text = response.get("response", "")
            done = response.get("done", True)

            # Log token counts if available
            if "prompt_eval_count" in response and "eval_count" in response:
                logger.debug(
                    f"Token usage - Input: {response.get('prompt_eval_count', 0)}, Output: {response.get('eval_count', 0)}"
                )

            # Check if response was truncated (hit max_tokens limit)
            if max_tokens and "eval_count" in response:
                if response["eval_count"] >= max_tokens:
                    logger.warning(f"Ollama response may be truncated (reached max_tokens: {max_tokens})")
                    logger.info("Consider increasing max_tokens or using smaller chunks")

            return response_text
        except Exception as e:
            raise RuntimeError(f"Ollama API request failed: {e}")

    def _messages_to_prompt(self, messages: List[Dict[str, str]]) -> str:
        """Convert OpenAI-style messages to a single prompt string"""
        prompt_parts = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "system":
                prompt_parts.append(f"System: {content}\n")
            elif role == "user":
                prompt_parts.append(f"User: {content}\n")
            elif role == "assistant":
                prompt_parts.append(f"Assistant: {content}\n")

        return "\n".join(prompt_parts)

    def supports_temperature(self) -> bool:
        """Ollama always supports temperature"""
        return True

    def health_check(self) -> bool:
        """Check if Ollama is running and the model is available"""
        try:
            # List available models using SDK
            models_response = self.client.list()
            # SDK returns a Pydantic model with .models attribute
            model_names = [m.model for m in models_response.models]

            # Check for exact match or model with tag
            model_available = any(self.model == name or self.model == name.split(":")[0] for name in model_names)

            if not model_available:
                logger.warning(f"Model '{self.model}' not found in Ollama.")
                logger.info(f"Available models: {', '.join(model_names)}")
                logger.info(f"Run: ollama pull {self.model}")
                return False

            return True
        except Exception as e:
            logger.error(f"Cannot connect to Ollama: {e}")
            logger.info("Make sure Ollama is running: ollama serve")
            return False

    def get_model_name(self) -> str:
        """Get the current model name"""
        return self.model

    def get_model_display_name(self) -> str:
        """Get human-readable model name for display"""
        return f"Ollama {self.model}"

    def supports_structured_output(self) -> bool:
        """Ollama doesn't support native structured output"""
        return False

    def generate_structured(
        self,
        messages: List[Dict[str, str]],
        response_model: Type[T],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> T:
        """
        Generate a structured response using JSON mode fallback.

        Ollama doesn't support native schema-validated output, so we use
        JSON mode and validate with Pydantic after parsing.
        """
        logger.debug(f"Ollama using JSON mode fallback for structured output")

        response = self.chat_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )

        # Parse JSON and validate with Pydantic
        data = json.loads(response)
        return response_model(**data)

    def chat_completion_streaming(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, str]] = None,
        on_chunk: Optional[callable] = None,
    ) -> str:
        """
        Generate a chat completion with streaming support.

        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature (0-1)
            max_tokens: Maximum tokens to generate
            response_format: Response format specification
            on_chunk: Optional callback function(chunk_text) called for each chunk

        Returns:
            The complete generated text response
        """
        # Convert chat messages to a single prompt for generate API
        prompt = self._messages_to_prompt(messages)

        # Build options dict
        options = {}
        if temperature is not None:
            options["temperature"] = temperature
        if max_tokens is not None:
            options["num_predict"] = max_tokens

        # Increase context window to prevent truncation
        if "num_ctx" not in options:
            options["num_ctx"] = 32768

        try:
            full_response = ""
            # Use streaming generate API
            stream = self.client.generate(
                model=self.model,
                prompt=prompt,
                stream=True,
                format="json" if response_format and response_format.get("type") == "json_object" else None,
                options=options if options else None,
            )

            for chunk in stream:
                chunk_text = chunk.get("response", "")
                if chunk_text:
                    full_response += chunk_text
                    if on_chunk:
                        on_chunk(chunk_text)

            return full_response
        except Exception as e:
            raise RuntimeError(f"Ollama streaming API request failed: {e}")


class AnthropicProvider(LLMProvider):
    """Anthropic Claude API provider"""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-5-20250929"):
        # Set a timeout to prevent infinite hangs (5 minutes for large responses)
        # Set max_retries=0 to disable SDK's automatic retry - we handle retries manually
        # This prevents the SDK from retrying rate limits immediately without waiting
        self.client = Anthropic(api_key=api_key, timeout=300.0, max_retries=0)
        self.model = model

    def _validate_max_tokens(self, requested_max_tokens: int) -> int:
        """
        Validate and potentially cap max_tokens to model limit.

        Args:
            requested_max_tokens: The requested max_tokens value

        Returns:
            The validated max_tokens value (may be capped)
        """
        model_limit = self.get_max_output_tokens()

        if requested_max_tokens > model_limit:
            logger.warning(
                f"Requested max_tokens ({requested_max_tokens}) exceeds model limit "
                f"({model_limit}) for {self.model}. Capping to {model_limit}."
            )
            return model_limit

        return requested_max_tokens

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, str]] = None,
    ) -> str:
        """Generate a chat completion using Anthropic API"""
        return self._create_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            enable_continuation=False,
        )

    def chat_completion_with_continuation(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, str]] = None,
        max_attempts: int = 3,
    ) -> str:
        """
        Generate a complete chat completion with automatic continuation on truncation.

        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature (0-1)
            max_tokens: Maximum tokens to generate per attempt
            response_format: Response format specification
            max_attempts: Maximum continuation attempts (default: 3)

        Returns:
            The complete generated text response
        """
        return self._create_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            enable_continuation=True,
            max_attempts=max_attempts,
        )

    def _create_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, str]] = None,
        enable_continuation: bool = False,
        max_attempts: int = 3,
    ) -> str:
        """Internal method to create completions with optional continuation"""
        full_response = ""
        current_messages = messages.copy()
        total_retry_count = 0  # Track total retries across all continuation attempts
        max_total_retries = 10  # Global limit to prevent infinite rate limit loops

        for attempt in range(max_attempts):
            # Separate system messages from conversation messages
            system_message = None
            conversation_messages = []

            for msg in current_messages:
                role = msg.get("role", "")
                content = msg.get("content", "")

                if role == "system":
                    # Anthropic uses a separate system parameter
                    system_message = content
                elif role in ["user", "assistant"]:
                    conversation_messages.append({"role": role, "content": content})

            # Validate and cap max_tokens to model limit
            effective_max_tokens = self._validate_max_tokens(max_tokens or 4096)

            # Build request parameters
            params: Dict[str, Any] = {
                "model": self.model,
                "messages": conversation_messages,
                "max_tokens": effective_max_tokens,
                "timeout": 600.0,  # 10 minute timeout for long requests
            }

            # Add system message if present
            if system_message:
                params["system"] = system_message

            # Add temperature if specified
            if temperature is not None:
                params["temperature"] = temperature

            # Handle JSON response format
            # Note: Anthropic doesn't have a native JSON mode like OpenAI
            # We'll add an instruction to the system message if needed
            if response_format and response_format.get("type") == "json_object":
                json_instruction = "\n\nIMPORTANT: You must respond with valid JSON only. Do not include any text before or after the JSON object."
                if system_message:
                    params["system"] = system_message + json_instruction
                else:
                    params["system"] = json_instruction.strip()

            # Make API request with rate limit retry logic
            max_retries = 3
            for retry_attempt in range(max_retries):
                try:
                    response = self.client.messages.create(**params)
                    break  # Success - exit retry loop
                except RateLimitError as e:
                    total_retry_count += 1

                    # Check global retry limit to prevent infinite loops
                    if total_retry_count > max_total_retries:
                        logger.error(
                            f"Rate limit retry failed after {total_retry_count} total retries across all attempts"
                        )
                        raise

                    if retry_attempt < max_retries - 1:
                        # Parse retry-after header (in seconds)
                        retry_after = 60  # Default to 60 seconds if header missing
                        if hasattr(e, "response") and e.response and hasattr(e.response, "headers"):
                            try:
                                retry_after = int(e.response.headers.get("retry-after", 60))
                            except (ValueError, TypeError):
                                # If parsing fails, use default
                                pass

                        # Add extra buffer time to ensure rate limit window has reset
                        buffer_time = 10  # Extra 10 seconds buffer
                        total_wait = retry_after + buffer_time

                        logger.warning(
                            f"Rate limit exceeded. Waiting {total_wait}s ({retry_after}s + {buffer_time}s buffer) "
                            f"before retry (total retry {total_retry_count}/{max_total_retries}, "
                            f"current attempt {retry_attempt + 2}/{max_retries})..."
                        )

                        # Show progress during long waits (>10 seconds)
                        if total_wait > 10:
                            elapsed = 0
                            while elapsed < total_wait:
                                time.sleep(10)
                                elapsed += 10
                                remaining = max(0, total_wait - elapsed)
                                if remaining > 0:
                                    logger.debug(f"...{remaining}s remaining...")
                        else:
                            time.sleep(total_wait)

                        logger.info("Retrying now...")
                        continue  # Explicitly continue to next retry attempt
                    else:
                        # Max retries for this attempt exceeded, re-raise the error
                        logger.error(
                            f"Rate limit retry failed after {max_retries} attempts (total retries: {total_retry_count})"
                        )
                        raise
                except BadRequestError as e:
                    # Permanent error - do NOT retry - fail fast with clear error message
                    error_str = str(e)
                    logger.error(f"BadRequest error (not retrying): {e}")

                    # Check for billing/credit balance errors
                    if "credit balance" in error_str.lower() or "billing" in error_str.lower():
                        raise ValueError(
                            f"Anthropic API billing error: {e}. "
                            f"Please check your account balance at https://console.anthropic.com/settings/billing"
                        ) from e

                    # Other bad request errors (invalid parameters, etc.)
                    raise ValueError(
                        f"Invalid Anthropic API request: {e}. "
                        f"This may be due to invalid parameters for model {self.model}."
                    ) from e

                except APIStatusError as e:
                    # Server errors (500, 529 overloaded) are transient - retry
                    # Client errors (400, 401, 403, 404) should not be retried
                    if e.status_code in (500, 529):
                        total_retry_count += 1
                        logger.warning(f"Server error {e.status_code}, will retry: {e}")
                        if retry_attempt < max_retries - 1:
                            retry_after = 30
                            logger.info(
                                f"Waiting {retry_after}s before retry (attempt {retry_attempt + 2}/{max_retries})"
                            )
                            time.sleep(retry_after)
                            continue
                        logger.error(f"Server error retry failed after {max_retries} attempts")
                        raise
                    # Other API status errors (4xx) - fail fast
                    logger.error(f"API error (status {e.status_code}): {e}")
                    raise

                except Exception as e:
                    # Connection errors, timeouts - these are transient, retry
                    total_retry_count += 1
                    error_type = type(e).__name__
                    logger.warning(f"Connection error ({error_type}): {e}")
                    if retry_attempt < max_retries - 1:
                        retry_after = 30  # Wait 30 seconds for connection errors
                        logger.info(f"Waiting {retry_after}s before retry (attempt {retry_attempt + 2}/{max_retries})")
                        time.sleep(retry_after)
                        continue
                    logger.error(f"Connection error retry failed after {max_retries} attempts")
                    raise

            # Extract text from current response
            current_text = ""
            if response.content and len(response.content) > 0:
                current_text = response.content[0].text

            full_response += current_text

            # Log token usage if available
            if hasattr(response, "usage"):
                logger.debug(
                    f"Token usage - Input: {response.usage.input_tokens}, Output: {response.usage.output_tokens}"
                )

            # Check stop_reason for handling different completion scenarios
            stop_reason = getattr(response, "stop_reason", None)

            if stop_reason == "max_tokens":
                if enable_continuation and attempt < max_attempts - 1:
                    logger.info(f"Response truncated, continuing (attempt {attempt + 2}/{max_attempts})...")
                    # Continue from where we left off
                    current_messages = messages + [
                        {"role": "assistant", "content": full_response},
                        {"role": "user", "content": "Please continue from where you left off."},
                    ]
                    continue
                else:
                    logger.warning(
                        f"Anthropic response truncated due to max_tokens limit (limit: {max_tokens or 4096})"
                    )
                    if not enable_continuation:
                        logger.info("Consider using smaller chunks or chat_completion_with_continuation()")
                    break
            elif stop_reason == "end_turn":
                # Normal completion
                break
            else:
                # Other stop reasons or no stop reason
                break

        return full_response

    def supports_temperature(self) -> bool:
        """Anthropic always supports temperature"""
        return True

    def health_check(self) -> bool:
        """Check if Anthropic API is accessible"""
        try:
            # Simple test with minimal tokens
            self.client.messages.create(model=self.model, messages=[{"role": "user", "content": "test"}], max_tokens=1)
            return True
        except Exception as e:
            logger.error(f"Anthropic health check failed: {e}")
            return False

    def get_model_name(self) -> str:
        """Get the current model name"""
        return self.model

    def get_model_display_name(self) -> str:
        """Get human-readable model name for display"""
        return f"Anthropic {self.model}"

    def supports_structured_output(self) -> bool:
        """Check if this model supports structured output (Claude 4.5+ with beta)"""
        # Check MODEL_CONFIGS for structured output support
        if self.model in MODEL_CONFIGS:
            return MODEL_CONFIGS[self.model].supports_structured_output

        # Check for partial match
        for model_name, limits in MODEL_CONFIGS.items():
            if self.model.startswith(model_name.rsplit("-", 1)[0]):
                return limits.supports_structured_output

        return False

    def _get_structured_output_beta(self) -> Optional[str]:
        """Get the beta header required for structured output, if any"""
        if self.model in MODEL_CONFIGS:
            return MODEL_CONFIGS[self.model].structured_output_beta

        for model_name, limits in MODEL_CONFIGS.items():
            if self.model.startswith(model_name.rsplit("-", 1)[0]):
                return limits.structured_output_beta

        return None

    def generate_structured(
        self,
        messages: List[Dict[str, str]],
        response_model: Type[T],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> T:
        """
        Generate a structured response using Anthropic's structured output.

        Uses client.beta.messages.create() with output_format for schema-validated JSON.
        Falls back to JSON mode + Pydantic validation for unsupported models.
        """
        beta_header = self._get_structured_output_beta()

        if not self.supports_structured_output() or not beta_header:
            # Fallback for models without structured output support
            logger.warning(f"Model {self.model} doesn't support structured output, using JSON mode fallback")
            response = self.chat_completion(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
            data = json.loads(response)
            return response_model(**data)

        # Separate system messages from conversation messages
        system_message = None
        conversation_messages = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "system":
                system_message = content
            elif role in ["user", "assistant"]:
                conversation_messages.append({"role": role, "content": content})

        # Validate and cap max_tokens to model limit
        effective_max_tokens = self._validate_max_tokens(max_tokens or 4096)

        # Build request parameters for beta structured output
        # Note: Anthropic uses a different API method for structured output
        params: Dict[str, Any] = {
            "model": self.model,
            "messages": conversation_messages,
            "max_tokens": effective_max_tokens,
        }

        if system_message:
            params["system"] = system_message
        if temperature is not None:
            params["temperature"] = temperature

        # Use beta API with structured output
        # Convert Pydantic model to JSON schema for Anthropic
        json_schema = response_model.model_json_schema()

        try:
            response = self.client.beta.messages.create(
                **params,
                betas=[beta_header],
                extra_headers={"anthropic-beta": beta_header},
            )

            # Extract and parse the response
            if response.content and len(response.content) > 0:
                response_text = response.content[0].text
                data = json.loads(response_text)
                return response_model(**data)
            else:
                raise ValueError("Anthropic returned empty response")

        except Exception as e:
            # If beta API fails, fall back to JSON mode
            logger.warning(f"Anthropic structured output failed, using fallback: {e}")
            response = self.chat_completion(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
            data = json.loads(response)
            return response_model(**data)

    def chat_completion_streaming(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, str]] = None,
        on_chunk: Optional[callable] = None,
    ) -> str:
        """
        Generate a chat completion with streaming support.

        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature (0-1)
            max_tokens: Maximum tokens to generate
            response_format: Response format specification
            on_chunk: Optional callback function(chunk_text) called for each chunk

        Returns:
            The complete generated text response
        """
        # Separate system messages from conversation messages
        system_message = None
        conversation_messages = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "system":
                system_message = content
            elif role in ["user", "assistant"]:
                conversation_messages.append({"role": role, "content": content})

        # Validate and cap max_tokens to model limit
        effective_max_tokens = self._validate_max_tokens(max_tokens or 4096)

        # Build request parameters (stream() is a method, not a parameter)
        params: Dict[str, Any] = {
            "model": self.model,
            "messages": conversation_messages,
            "max_tokens": effective_max_tokens,
        }

        # Add system message if present
        if system_message:
            params["system"] = system_message

        # Add temperature if specified
        if temperature is not None:
            params["temperature"] = temperature

        # Handle JSON response format
        if response_format and response_format.get("type") == "json_object":
            json_instruction = "\n\nIMPORTANT: You must respond with valid JSON only. Do not include any text before or after the JSON object."
            if system_message:
                params["system"] = system_message + json_instruction
            else:
                params["system"] = json_instruction.strip()

        # Make streaming API request with rate limit retry logic
        max_retries = 3
        for retry_attempt in range(max_retries):
            try:
                full_response = ""
                logger.debug(f"Opening stream (attempt {retry_attempt + 1}/{max_retries})...")
                # Use the stream() context manager method (not a parameter!)
                with self.client.messages.stream(**params) as stream:
                    logger.debug("Stream opened, waiting for data...")
                    chunk_count = 0
                    for text in stream.text_stream:
                        chunk_count += 1
                        if chunk_count == 1:
                            logger.debug("Receiving data...")
                        full_response += text
                        if on_chunk:
                            on_chunk(text)

                return full_response

            except RateLimitError as e:
                if retry_attempt < max_retries - 1:
                    # Parse retry-after header (in seconds)
                    retry_after = 60  # Default to 60 seconds if header missing
                    if hasattr(e, "response") and e.response and hasattr(e.response, "headers"):
                        try:
                            retry_after = int(e.response.headers.get("retry-after", 60))
                        except (ValueError, TypeError):
                            pass

                    # Add extra buffer time to ensure rate limit window has reset
                    # Anthropic rate limits use token buckets that may need longer than retry-after
                    buffer_time = 10  # Extra 10 seconds buffer
                    total_wait = retry_after + buffer_time

                    logger.warning(
                        f"Rate limit exceeded. Waiting {total_wait}s ({retry_after}s + {buffer_time}s buffer) "
                        f"before retry (attempt {retry_attempt + 2}/{max_retries})..."
                    )

                    # Show progress during long waits (>10 seconds)
                    if total_wait > 10:
                        elapsed = 0
                        while elapsed < total_wait:
                            time.sleep(10)
                            elapsed += 10
                            remaining = max(0, total_wait - elapsed)
                            if remaining > 0:
                                logger.debug(f"...{remaining}s remaining...")
                    else:
                        time.sleep(total_wait)

                    logger.info("Retrying now...")
                    continue  # Explicitly continue to next retry attempt
                else:
                    logger.error(f"Rate limit retry failed after {max_retries} attempts")
                    raise

            except BadRequestError as e:
                # Permanent error - invalid request parameters
                # Do NOT retry - fail fast with clear error message
                logger.error(f"BadRequest error in streaming (not retrying): {e}")
                raise ValueError(
                    f"Invalid Anthropic API request: {e}. "
                    f"This may be due to invalid parameters for model {self.model}."
                ) from e

            except APIStatusError as e:
                # Server errors (500, 529 overloaded) are transient - retry
                if e.status_code in (500, 529):
                    logger.warning(f"Server error {e.status_code} in streaming, will retry: {e}")
                    if retry_attempt < max_retries - 1:
                        retry_after = 30
                        logger.info(f"Waiting {retry_after}s before retry (attempt {retry_attempt + 2}/{max_retries})")
                        time.sleep(retry_after)
                        continue
                    logger.error(f"Server error retry failed after {max_retries} attempts")
                    raise
                # Other API status errors (4xx) - fail fast
                logger.error(f"API error in streaming (status {e.status_code}): {e}")
                raise

            except Exception as e:
                # Connection errors, timeouts - these are transient, retry
                error_type = type(e).__name__
                logger.warning(f"Connection error in streaming ({error_type}): {e}")
                if retry_attempt < max_retries - 1:
                    retry_after = 30  # Wait 30 seconds for connection errors
                    logger.info(f"Waiting {retry_after}s before retry (attempt {retry_attempt + 2}/{max_retries})")
                    time.sleep(retry_after)
                    continue
                logger.error(f"Connection error retry failed after {max_retries} attempts")
                raise

        # Should never reach here, but return empty string as fallback
        return ""


class GeminiProvider(LLMProvider):
    """Google Gemini API provider using the new google.genai SDK"""

    # Gemini 3 models that support thinking_level parameter
    GEMINI_3_MODELS = [
        "gemini-3-pro",
        "gemini-3-flash",
    ]

    # Valid thinking levels per model type
    # Pro: "low", "high" (default)
    # Flash: "minimal", "low", "medium", "high" (default)
    THINKING_LEVELS_PRO = ["low", "high"]
    THINKING_LEVELS_FLASH = ["minimal", "low", "medium", "high"]

    # Map string thinking levels to SDK enum values
    THINKING_LEVEL_MAP = {
        "minimal": genai_types.ThinkingLevel.MINIMAL,
        "low": genai_types.ThinkingLevel.LOW,
        "medium": genai_types.ThinkingLevel.MEDIUM,
        "high": genai_types.ThinkingLevel.HIGH,
    }

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3-pro-preview",
        thinking_level: Optional[str] = None,
    ):
        """
        Initialize Gemini provider.

        Args:
            api_key: Google Gemini API key
            model: Model name (e.g., "gemini-3-pro-preview", "gemini-3-flash-preview")
            thinking_level: Reasoning depth for Gemini 3 models.
                           Pro: "low", "high" (default)
                           Flash: "minimal", "low", "medium", "high" (default)
                           None uses API default (high).
        """
        self.model = model
        self.thinking_level = thinking_level
        self.client = genai.Client(api_key=api_key)

    def _is_gemini_3_model(self) -> bool:
        """Check if the current model is a Gemini 3 model that supports thinking_level"""
        for model_prefix in self.GEMINI_3_MODELS:
            if self.model.startswith(model_prefix):
                return True
        return False

    def _is_gemini_3_flash(self) -> bool:
        """Check if the current model is Gemini 3 Flash"""
        return self.model.startswith("gemini-3-flash")

    def _get_thinking_config(self) -> Optional[genai_types.ThinkingConfig]:
        """
        Get thinking configuration for Gemini 3 models.

        Returns:
            ThinkingConfig object for API, or None if not applicable
        """
        if not self._is_gemini_3_model() or not self.thinking_level:
            return None

        # Validate thinking level for the model type
        if self._is_gemini_3_flash():
            valid_levels = self.THINKING_LEVELS_FLASH
        else:
            valid_levels = self.THINKING_LEVELS_PRO

        if self.thinking_level not in valid_levels:
            logger.warning(
                f"Invalid thinking_level '{self.thinking_level}' for {self.model}. "
                f"Valid levels: {valid_levels}. Using API default."
            )
            return None

        # Convert string to SDK enum
        thinking_enum = self.THINKING_LEVEL_MAP.get(self.thinking_level)
        if thinking_enum is None:
            return None

        return genai_types.ThinkingConfig(thinking_level=thinking_enum)

    def _build_config(
        self,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, str]] = None,
        response_schema: Optional[Type] = None,
    ) -> genai_types.GenerateContentConfig:
        """Build GenerateContentConfig for API calls"""
        config_kwargs = {}

        if temperature is not None:
            config_kwargs["temperature"] = temperature
        if max_tokens is not None:
            config_kwargs["max_output_tokens"] = max_tokens

        # Handle JSON response format
        if response_format and response_format.get("type") == "json_object":
            config_kwargs["response_mime_type"] = "application/json"

        # Handle structured output with schema
        if response_schema is not None:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"] = response_schema

        # Add thinking_level for Gemini 3 models
        thinking_config = self._get_thinking_config()
        if thinking_config:
            config_kwargs["thinking_config"] = thinking_config

        # Configure safety settings to be less restrictive for transcript processing
        config_kwargs["safety_settings"] = [
            genai_types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="OFF"),
            genai_types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"),
            genai_types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
            genai_types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
        ]

        return genai_types.GenerateContentConfig(**config_kwargs)

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, str]] = None,
    ) -> str:
        """Generate a chat completion using Gemini API"""
        return self._create_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            enable_continuation=False,
            max_attempts=1,
        )

    def chat_completion_with_continuation(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, str]] = None,
        max_attempts: int = 3,
    ) -> str:
        """
        Generate a complete chat completion with automatic continuation on truncation.

        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature (0-1)
            max_tokens: Maximum tokens to generate per attempt
            response_format: Response format specification
            max_attempts: Maximum continuation attempts (default: 3)

        Returns:
            The complete generated text response
        """
        return self._create_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            enable_continuation=True,
            max_attempts=max_attempts,
        )

    def _create_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, str]] = None,
        enable_continuation: bool = False,
        max_attempts: int = 3,
    ) -> str:
        """Internal method to create completions with optional continuation"""
        full_response = ""
        current_messages = messages.copy()

        for attempt in range(max_attempts):
            # Convert OpenAI-style messages to Gemini format
            contents = self._convert_messages(current_messages)

            # Build config
            config = self._build_config(
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )

            # Generate response using new SDK
            response = self.client.models.generate_content(
                model=self.model,
                contents=contents,
                config=config,
            )

            # Handle different finish reasons
            if not response.candidates:
                raise RuntimeError("Gemini returned no candidates in response")

            candidate = response.candidates[0]
            finish_reason = candidate.finish_reason

            # Try to get text first, regardless of finish reason
            response_text = None
            try:
                response_text = response.text
            except Exception:
                # If response.text fails, try getting from candidate
                if candidate.content and candidate.content.parts and len(candidate.content.parts) > 0:
                    try:
                        response_text = candidate.content.parts[0].text
                    except Exception:
                        pass

            # Accumulate response text
            if response_text:
                full_response += response_text

            # Check finish reason (new SDK uses string values)
            # STOP, MAX_TOKENS, SAFETY, RECITATION, OTHER, etc.
            finish_reason_str = str(finish_reason).upper() if finish_reason else ""

            if "STOP" in finish_reason_str:
                break
            elif "MAX_TOKENS" in finish_reason_str:
                if enable_continuation and attempt < max_attempts - 1:
                    logger.info(f"Response truncated, continuing (attempt {attempt + 2}/{max_attempts})...")
                    current_messages = messages + [
                        {"role": "assistant", "content": full_response},
                        {"role": "user", "content": "Please continue from where you left off."},
                    ]
                    continue
                else:
                    char_count = len(full_response) if full_response else 0
                    estimated_tokens = char_count // 4
                    logger.warning(
                        f"Gemini response truncated due to max_tokens limit. "
                        f"Limit: {max_tokens if max_tokens else 'default (8192)'} tokens, "
                        f"Generated: ~{estimated_tokens} tokens ({char_count} characters)"
                    )
                    if not enable_continuation:
                        logger.info("Consider using smaller chunks or chat_completion_with_continuation()")
                    break
            elif "SAFETY" in finish_reason_str:
                raise RuntimeError(
                    f"Gemini blocked response due to safety filters. Safety ratings: {candidate.safety_ratings}"
                )
            elif "RECITATION" in finish_reason_str:
                if full_response:
                    logger.warning("Gemini flagged potential recitation but returned content")
                    break
                raise RuntimeError(
                    "Gemini blocked response due to potential recitation (copyrighted content). "
                    "Try adjusting the prompt or use a different model."
                )
            else:
                if full_response:
                    logger.warning(f"Gemini finished with reason {finish_reason} but returned content")
                    break
                raise RuntimeError(f"Gemini finished with unexpected reason: {finish_reason}")

        if not full_response and not enable_continuation:
            raise RuntimeError(
                f"Gemini response exceeded max_tokens limit with no usable content. "
                f"Current limit: {max_tokens if max_tokens else 'default (8192)'}. "
                f"Try increasing max_tokens or reducing input size."
            )

        return full_response

    def _convert_messages(self, messages: List[Dict[str, str]]) -> List[genai_types.Content]:
        """Convert OpenAI-style messages to Gemini Content format"""
        contents = []
        system_instruction = None

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "system":
                system_instruction = content
            elif role == "user":
                contents.append(genai_types.Content(role="user", parts=[genai_types.Part(text=content)]))
            elif role == "assistant":
                contents.append(genai_types.Content(role="model", parts=[genai_types.Part(text=content)]))

        # If we have a system instruction, prepend it to the first user message
        if system_instruction and contents:
            first_content = contents[0]
            if first_content.role == "user" and first_content.parts:
                original_text = first_content.parts[0].text
                first_content.parts[0] = genai_types.Part(text=f"{system_instruction}\n\n{original_text}")

        return contents

    def supports_temperature(self) -> bool:
        """Gemini always supports temperature"""
        return True

    def health_check(self) -> bool:
        """Check if Gemini API is accessible"""
        try:
            config = genai_types.GenerateContentConfig(max_output_tokens=1)
            self.client.models.generate_content(
                model=self.model,
                contents="test",
                config=config,
            )
            return True
        except Exception as e:
            logger.error(f"Gemini health check failed: {e}")
            return False

    def get_model_name(self) -> str:
        """Get the current model name"""
        return self.model

    def get_model_display_name(self) -> str:
        """Get human-readable model name for display"""
        return f"Google {self.model}"

    def supports_structured_output(self) -> bool:
        """Gemini supports structured output via response_schema"""
        return True

    def generate_structured(
        self,
        messages: List[Dict[str, str]],
        response_model: Type[T],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> T:
        """
        Generate a structured response using Gemini's response_schema.

        Uses response_schema parameter for schema-validated JSON output.
        Gemini requires manual parsing of the JSON response.
        """
        contents = self._convert_messages(messages)

        config = self._build_config(
            temperature=temperature,
            max_tokens=max_tokens,
            response_schema=response_model,
        )

        response = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=config,
        )

        if not response.candidates:
            raise RuntimeError("Gemini returned no candidates in response")

        response_text = None
        try:
            response_text = response.text
        except Exception:
            candidate = response.candidates[0]
            if candidate.content and candidate.content.parts and len(candidate.content.parts) > 0:
                response_text = candidate.content.parts[0].text

        if not response_text:
            raise ValueError("Gemini returned empty response")

        data = json.loads(response_text)
        return response_model(**data)

    def chat_completion_streaming(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, str]] = None,
        on_chunk: Optional[callable] = None,
    ) -> str:
        """
        Generate a chat completion with streaming support.

        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature (0-1)
            max_tokens: Maximum tokens to generate
            response_format: Response format specification
            on_chunk: Optional callback function(chunk_text) called for each chunk

        Returns:
            The complete generated text response
        """
        contents = self._convert_messages(messages)

        config = self._build_config(
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )

        full_response = ""
        for chunk in self.client.models.generate_content_stream(
            model=self.model,
            contents=contents,
            config=config,
        ):
            if chunk.text:
                full_response += chunk.text
                if on_chunk:
                    on_chunk(chunk.text)

        return full_response


def create_llm_provider(
    provider_type: str,
    openai_api_key: str = "",
    openai_model: str = "gpt-5.1",
    ollama_base_url: str = "http://localhost:11434",
    ollama_model: str = "gemma3:4b",
    gemini_api_key: str = "",
    gemini_model: str = "gemini-3-pro-preview",
    gemini_thinking_level: Optional[str] = None,
    anthropic_api_key: str = "",
    anthropic_model: str = "claude-sonnet-4-5-20250929",
) -> LLMProvider:
    """
    Factory function to create the appropriate LLM provider.

    Args:
        provider_type: "openai", "ollama", "gemini", or "anthropic"
        openai_api_key: OpenAI API key (required if provider_type is "openai")
        openai_model: OpenAI model name
        ollama_base_url: Ollama base URL
        ollama_model: Ollama model name
        gemini_api_key: Google Gemini API key (required if provider_type is "gemini")
        gemini_model: Gemini model name
        gemini_thinking_level: Thinking level for Gemini 3 models.
                              Pro: "low", "high" (default)
                              Flash: "minimal", "low", "medium", "high" (default)
        anthropic_api_key: Anthropic API key (required if provider_type is "anthropic")
        anthropic_model: Anthropic model name

    Returns:
        LLMProvider instance

    Raises:
        ValueError: If provider_type is invalid or required config is missing
    """
    provider_type = provider_type.lower()

    if provider_type == "openai":
        if not openai_api_key:
            raise ValueError("OpenAI API key is required for OpenAI provider")
        return OpenAIProvider(api_key=openai_api_key, model=openai_model)
    elif provider_type == "ollama":
        provider = OllamaProvider(base_url=ollama_base_url, model=ollama_model)
        # Perform health check on creation
        if not provider.health_check():
            raise RuntimeError(
                f"Ollama is not available at {ollama_base_url} or model '{ollama_model}' is not installed"
            )
        return provider
    elif provider_type == "gemini":
        if not gemini_api_key:
            raise ValueError("Gemini API key is required for Gemini provider")
        return GeminiProvider(api_key=gemini_api_key, model=gemini_model, thinking_level=gemini_thinking_level)
    elif provider_type == "anthropic":
        if not anthropic_api_key:
            raise ValueError("Anthropic API key is required for Anthropic provider")
        return AnthropicProvider(api_key=anthropic_api_key, model=anthropic_model)
    else:
        raise ValueError(
            f"Unknown provider type: {provider_type}. Must be 'openai', 'ollama', 'gemini', or 'anthropic'"
        )
