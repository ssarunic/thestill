"""
Abstract LLM provider interface and implementations for OpenAI and Ollama.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
import json
import requests
from openai import OpenAI


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

    # Models that don't support custom temperature
    TEMPERATURE_RESTRICTED_MODELS = [
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

        # Only add temperature if supported
        if temperature is not None and self.supports_temperature():
            params["temperature"] = temperature

        # Add max_tokens if specified
        if max_tokens is not None:
            params["max_completion_tokens"] = max_tokens

        # Add response format if specified
        if response_format is not None:
            params["response_format"] = response_format

        response = self.client.chat.completions.create(**params)
        return response.choices[0].message.content or ""

    def supports_temperature(self) -> bool:
        """Check if the current model supports custom temperature"""
        for restricted_model in self.TEMPERATURE_RESTRICTED_MODELS:
            if self.model.startswith(restricted_model):
                return False
        return True

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
    """Ollama local LLM provider"""

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "llama3.2"):
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.api_generate = f"{self.base_url}/api/generate"
        self.api_chat = f"{self.base_url}/api/chat"
        self.api_tags = f"{self.base_url}/api/tags"

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, str]] = None
    ) -> str:
        """Generate a chat completion using Ollama API"""
        # Convert chat messages to a single prompt
        # Using /api/generate for better compatibility across Ollama versions
        prompt = self._messages_to_prompt(messages)

        payload: Dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False
        }

        # Add options if specified
        options = {}
        if temperature is not None:
            options["temperature"] = temperature
        if max_tokens is not None:
            options["num_predict"] = max_tokens

        if options:
            payload["options"] = options

        # Handle JSON response format
        if response_format and response_format.get("type") == "json_object":
            payload["format"] = "json"

        try:
            response = requests.post(
                self.api_generate,
                json=payload,
                timeout=300  # 5 minute timeout for local inference
            )
            response.raise_for_status()
            result = response.json()
            return result.get("response", "")
        except requests.exceptions.RequestException as e:
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
            # Check if Ollama is running
            response = requests.get(self.api_tags, timeout=5)
            response.raise_for_status()

            # Check if the specified model is available
            models = response.json().get("models", [])
            model_names = [m.get("name", "") for m in models]

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
        except requests.exceptions.ConnectionError:
            print(f"❌ Cannot connect to Ollama at {self.base_url}")
            print("   Make sure Ollama is running: ollama serve")
            return False
        except Exception as e:
            print(f"Ollama health check failed: {e}")
            return False

    def get_model_name(self) -> str:
        """Get the current model name"""
        return self.model


def create_llm_provider(
    provider_type: str,
    openai_api_key: str = "",
    openai_model: str = "gpt-4o",
    ollama_base_url: str = "http://localhost:11434",
    ollama_model: str = "llama3.2"
) -> LLMProvider:
    """
    Factory function to create the appropriate LLM provider.

    Args:
        provider_type: "openai" or "ollama"
        openai_api_key: OpenAI API key (required if provider_type is "openai")
        openai_model: OpenAI model name
        ollama_base_url: Ollama base URL
        ollama_model: Ollama model name

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
    else:
        raise ValueError(f"Unknown provider type: {provider_type}. Must be 'openai' or 'ollama'")
