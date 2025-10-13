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
Abstract LLM provider interface and implementations for OpenAI, Ollama, Gemini, and Anthropic.
"""

import json
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

import google.generativeai as genai
import ollama
from anthropic import Anthropic
from openai import OpenAI


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


class OpenAIProvider(LLMProvider):
    """OpenAI API provider"""

    # Models that don't support custom temperature and response_format
    REASONING_MODELS = ["o1", "o1-preview", "o1-mini", "gpt-5", "gpt-5-mini", "gpt-5-turbo", "gpt-5-nano"]

    def __init__(self, api_key: str, model: str = "gpt-4o"):
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
                print(
                    f"  Token usage - Input: {response.usage.prompt_tokens}, Output: {response.usage.completion_tokens}"
                )

            # Check finish_reason
            finish_reason = response.choices[0].finish_reason

            if finish_reason == "length":  # Equivalent to max_tokens
                if enable_continuation and attempt < max_attempts - 1:
                    print(f"  Response truncated, continuing (attempt {attempt + 2}/{max_attempts})...")
                    # Continue from where we left off
                    current_messages = messages + [
                        {"role": "assistant", "content": full_response},
                        {"role": "user", "content": "Please continue from where you left off."},
                    ]
                    continue
                else:
                    print(
                        f"⚠️  Warning: OpenAI response truncated due to max_tokens limit (limit: {max_tokens or 'default'})"
                    )
                    if not enable_continuation:
                        print(f"   Consider using smaller chunks or chat_completion_with_continuation()")
                    break
            elif finish_reason == "stop":
                # Normal completion
                break
            elif finish_reason == "content_filter":
                print(f"⚠️  Warning: OpenAI content filter triggered")
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
            print(f"OpenAI health check failed: {e}")
            return False

    def get_model_name(self) -> str:
        """Get the current model name"""
        return self.model

    def get_model_display_name(self) -> str:
        """Get human-readable model name for display"""
        return f"OpenAI {self.model}"


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
                print(
                    f"  Token usage - Input: {response.get('prompt_eval_count', 0)}, Output: {response.get('eval_count', 0)}"
                )

            # Check if response was truncated (hit max_tokens limit)
            if max_tokens and "eval_count" in response:
                if response["eval_count"] >= max_tokens:
                    print(f"⚠️  Warning: Ollama response may be truncated (reached max_tokens: {max_tokens})")
                    print(f"   Consider increasing max_tokens or using smaller chunks")

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
                print(f"⚠️  Model '{self.model}' not found in Ollama.")
                print(f"   Available models: {', '.join(model_names)}")
                print(f"   Run: ollama pull {self.model}")
                return False

            return True
        except Exception as e:
            print(f"❌ Cannot connect to Ollama: {e}")
            print("   Make sure Ollama is running: ollama serve")
            return False

    def get_model_name(self) -> str:
        """Get the current model name"""
        return self.model

    def get_model_display_name(self) -> str:
        """Get human-readable model name for display"""
        return f"Ollama {self.model}"


class AnthropicProvider(LLMProvider):
    """Anthropic Claude API provider"""

    def __init__(self, api_key: str, model: str = "claude-3-5-sonnet-20241022"):
        self.client = Anthropic(api_key=api_key)
        self.model = model

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

            # Build request parameters
            params: Dict[str, Any] = {
                "model": self.model,
                "messages": conversation_messages,
                "max_tokens": max_tokens or 4096,  # Anthropic requires max_tokens
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

            # Make API request
            response = self.client.messages.create(**params)

            # Extract text from current response
            current_text = ""
            if response.content and len(response.content) > 0:
                current_text = response.content[0].text

            full_response += current_text

            # Log token usage if available
            if hasattr(response, "usage"):
                print(f"  Token usage - Input: {response.usage.input_tokens}, Output: {response.usage.output_tokens}")

            # Check stop_reason for handling different completion scenarios
            stop_reason = getattr(response, "stop_reason", None)

            if stop_reason == "max_tokens":
                if enable_continuation and attempt < max_attempts - 1:
                    print(f"  Response truncated, continuing (attempt {attempt + 2}/{max_attempts})...")
                    # Continue from where we left off
                    current_messages = messages + [
                        {"role": "assistant", "content": full_response},
                        {"role": "user", "content": "Please continue from where you left off."},
                    ]
                    continue
                else:
                    print(
                        f"⚠️  Warning: Anthropic response truncated due to max_tokens limit (limit: {max_tokens or 4096})"
                    )
                    if not enable_continuation:
                        print(f"   Consider using smaller chunks or chat_completion_with_continuation()")
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
            print(f"Anthropic health check failed: {e}")
            return False

    def get_model_name(self) -> str:
        """Get the current model name"""
        return self.model

    def get_model_display_name(self) -> str:
        """Get human-readable model name for display"""
        return f"Anthropic {self.model}"


class GeminiProvider(LLMProvider):
    """Google Gemini API provider"""

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash-exp"):
        genai.configure(api_key=api_key)
        self.model = model
        self.client = genai.GenerativeModel(model)

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, str]] = None,
    ) -> str:
        """Generate a chat completion using Gemini API"""
        # Convert OpenAI-style messages to Gemini format
        gemini_messages = self._convert_messages(messages)

        # Build generation config
        generation_config = {}
        if temperature is not None:
            generation_config["temperature"] = temperature
        if max_tokens is not None:
            generation_config["max_output_tokens"] = max_tokens

        # Handle JSON response format
        if response_format and response_format.get("type") == "json_object":
            generation_config["response_mime_type"] = "application/json"

        # Configure safety settings to be less restrictive for transcript processing
        # This helps avoid false positives when processing podcast content
        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]

        # Generate response
        response = self.client.generate_content(
            gemini_messages,
            generation_config=genai.GenerationConfig(**generation_config) if generation_config else None,
            safety_settings=safety_settings,
        )

        # Handle different finish reasons
        # See: https://ai.google.dev/api/generate-content#finishreason
        if not response.candidates:
            raise RuntimeError("Gemini returned no candidates in response")

        candidate = response.candidates[0]
        finish_reason = candidate.finish_reason

        # Try to get text first, regardless of finish reason
        response_text = None
        try:
            response_text = response.text
        except:
            # If response.text fails, try getting from candidate
            if candidate.content and candidate.content.parts and len(candidate.content.parts) > 0:
                try:
                    response_text = candidate.content.parts[0].text
                except:
                    pass

        # 0 = FINISH_REASON_UNSPECIFIED, 1 = STOP (success), 2 = MAX_TOKENS,
        # 3 = SAFETY, 4 = RECITATION, 5 = OTHER
        if finish_reason == 1:  # STOP - normal completion
            return response_text if response_text else ""
        elif finish_reason == 2:  # MAX_TOKENS
            # We already extracted text above, return it with a warning
            if response_text:
                char_count = len(response_text)
                # Rough estimate: 1 token ≈ 4 characters
                estimated_tokens = char_count // 4
                print(f"⚠️  Warning: Gemini response truncated due to max_tokens limit")
                print(f"   Limit: {max_tokens if max_tokens else 'default (8192)'} tokens")
                print(f"   Generated: ~{estimated_tokens} tokens ({char_count} characters)")
                print(f"   Consider using smaller chunks or increasing max_tokens")
                return response_text

            raise RuntimeError(
                f"Gemini response exceeded max_tokens limit with no usable content. "
                f"Current limit: {max_tokens if max_tokens else 'default (8192)'}. "
                f"Try increasing max_tokens or reducing input size."
            )
        elif finish_reason == 3:  # SAFETY
            raise RuntimeError(
                f"Gemini blocked response due to safety filters. " f"Safety ratings: {candidate.safety_ratings}"
            )
        elif finish_reason == 4:  # RECITATION
            # We already extracted text above, return it if available
            if response_text:
                print(f"⚠️  Warning: Gemini flagged potential recitation but returned content")
                return response_text
            raise RuntimeError(
                "Gemini blocked response due to potential recitation (copyrighted content). "
                "Try adjusting the prompt or use a different model."
            )
        else:
            # For any other finish reason, try to return text if we have it
            if response_text:
                print(f"⚠️  Warning: Gemini finished with reason {finish_reason} but returned content")
                return response_text
            raise RuntimeError(f"Gemini finished with unexpected reason: {finish_reason}")

    def _convert_messages(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Convert OpenAI-style messages to Gemini format"""
        gemini_messages = []
        system_instruction = None

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "system":
                # Gemini handles system messages differently
                system_instruction = content
            elif role == "user":
                gemini_messages.append({"role": "user", "parts": [content]})
            elif role == "assistant":
                gemini_messages.append({"role": "model", "parts": [content]})

        # If we have a system instruction, prepend it to the first user message
        if system_instruction and gemini_messages:
            first_msg = gemini_messages[0]
            if first_msg["role"] == "user":
                first_msg["parts"][0] = f"{system_instruction}\n\n{first_msg['parts'][0]}"

        return gemini_messages

    def supports_temperature(self) -> bool:
        """Gemini always supports temperature"""
        return True

    def health_check(self) -> bool:
        """Check if Gemini API is accessible"""
        try:
            # Simple test with minimal tokens
            response = self.client.generate_content(
                "test", generation_config=genai.GenerationConfig(max_output_tokens=1)
            )
            return True
        except Exception as e:
            print(f"Gemini health check failed: {e}")
            return False

    def get_model_name(self) -> str:
        """Get the current model name"""
        return self.model

    def get_model_display_name(self) -> str:
        """Get human-readable model name for display"""
        return f"Google {self.model}"


def create_llm_provider(
    provider_type: str,
    openai_api_key: str = "",
    openai_model: str = "gpt-4o",
    ollama_base_url: str = "http://localhost:11434",
    ollama_model: str = "gemma3:4b",
    gemini_api_key: str = "",
    gemini_model: str = "gemini-2.0-flash-exp",
    anthropic_api_key: str = "",
    anthropic_model: str = "claude-3-5-sonnet-20241022",
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
        return GeminiProvider(api_key=gemini_api_key, model=gemini_model)
    elif provider_type == "anthropic":
        if not anthropic_api_key:
            raise ValueError("Anthropic API key is required for Anthropic provider")
        return AnthropicProvider(api_key=anthropic_api_key, model=anthropic_model)
    else:
        raise ValueError(
            f"Unknown provider type: {provider_type}. Must be 'openai', 'ollama', 'gemini', or 'anthropic'"
        )
