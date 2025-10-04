"""
Abstract LLM provider interface and implementations for OpenAI and Ollama.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
import json
from openai import OpenAI
import ollama
import google.generativeai as genai


class LLMProvider(ABC):
    """Abstract base class for LLM providers"""

    @abstractmethod
    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, str]] = None
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


class OpenAIProvider(LLMProvider):
    """OpenAI API provider"""

    # Models that don't support custom temperature and response_format
    REASONING_MODELS = [
        "o1", "o1-preview", "o1-mini",
        "gpt-5", "gpt-5-mini", "gpt-5-turbo", "gpt-5-nano"
    ]

    def __init__(self, api_key: str, model: str = "gpt-4o"):
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, str]] = None
    ) -> str:
        """Generate a chat completion using OpenAI API"""
        params: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
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
        return response.choices[0].message.content or ""

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
                model=self.model,
                messages=[{"role": "user", "content": "test"}],
                max_completion_tokens=1
            )
            return True
        except Exception as e:
            print(f"OpenAI health check failed: {e}")
            return False

    def get_model_name(self) -> str:
        """Get the current model name"""
        return self.model


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
        response_format: Optional[Dict[str, str]] = None
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
                options=options if options else None
            )
            return response.get("response", "")
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
            model_available = any(
                self.model == name or self.model == name.split(':')[0]
                for name in model_names
            )

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
        response_format: Optional[Dict[str, str]] = None
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
            {
                "category": "HARM_CATEGORY_HARASSMENT",
                "threshold": "BLOCK_NONE"
            },
            {
                "category": "HARM_CATEGORY_HATE_SPEECH",
                "threshold": "BLOCK_NONE"
            },
            {
                "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                "threshold": "BLOCK_NONE"
            },
            {
                "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                "threshold": "BLOCK_NONE"
            }
        ]

        # Generate response
        response = self.client.generate_content(
            gemini_messages,
            generation_config=genai.GenerationConfig(**generation_config) if generation_config else None,
            safety_settings=safety_settings
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
                print(f"⚠️  Warning: Gemini response was truncated due to max_tokens limit (limit: {max_tokens if max_tokens else 'default'})")
                return response_text

            raise RuntimeError(
                f"Gemini response exceeded max_tokens limit with no usable content. "
                f"Current limit: {max_tokens if max_tokens else 'default (8192)'}. "
                f"Try increasing max_tokens or reducing input size."
            )
        elif finish_reason == 3:  # SAFETY
            raise RuntimeError(
                f"Gemini blocked response due to safety filters. "
                f"Safety ratings: {candidate.safety_ratings}"
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
                "test",
                generation_config=genai.GenerationConfig(max_output_tokens=1)
            )
            return True
        except Exception as e:
            print(f"Gemini health check failed: {e}")
            return False

    def get_model_name(self) -> str:
        """Get the current model name"""
        return self.model


def create_llm_provider(
    provider_type: str,
    openai_api_key: str = "",
    openai_model: str = "gpt-4o",
    ollama_base_url: str = "http://localhost:11434",
    ollama_model: str = "gemma3:4b",
    gemini_api_key: str = "",
    gemini_model: str = "gemini-2.0-flash-exp"
) -> LLMProvider:
    """
    Factory function to create the appropriate LLM provider.

    Args:
        provider_type: "openai", "ollama", or "gemini"
        openai_api_key: OpenAI API key (required if provider_type is "openai")
        openai_model: OpenAI model name
        ollama_base_url: Ollama base URL
        ollama_model: Ollama model name
        gemini_api_key: Google Gemini API key (required if provider_type is "gemini")
        gemini_model: Gemini model name

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
    else:
        raise ValueError(f"Unknown provider type: {provider_type}. Must be 'openai', 'ollama', or 'gemini'")
