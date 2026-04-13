# Mistral LLM Provider Support

**Status**: ✅ Complete
**Created**: 2026-01-12
**Completed**: 2026-01-12

## Overview

Add Mistral AI as a fifth LLM provider alongside OpenAI, Anthropic, Google Gemini, and Ollama. This follows the existing provider architecture pattern with full feature parity including chat completion, streaming, and structured output support.

---

## Features

- **Chat Completion**: Standard request/response with temperature and max_tokens control
- **Streaming**: Real-time token output via `chat.stream()` with `on_chunk` callback
- **Structured Output**: Native JSON schema validation via `chat.parse()` method
- **Auto-Continuation**: Handle MAX_TOKENS truncation with automatic continuation
- **Retry Logic**: Rate limit handling with exponential backoff

---

## Supported Models

| Model | Context Window | Max Output | Use Case |
|-------|----------------|------------|----------|
| `mistral-large-latest` | 256K | 32K | Flagship, highest quality |
| `mistral-small-latest` | 128K | 32K | Fast, cost-effective |
| `mistral-medium-latest` | 128K | 32K | Balanced performance |
| `ministral-8b-latest` | 262K | 32K | Smallest, fastest |
| `codestral-latest` | 256K | 32K | Code-focused tasks |

All models support structured output except `codestral-mamba`.

---

## Configuration

### Environment Variables

```bash
LLM_PROVIDER=mistral
MISTRAL_API_KEY=your-api-key-here
MISTRAL_MODEL=mistral-large-latest  # or mistral-small-latest, etc.
```

### Config Fields

```python
# In Config class
mistral_api_key: str = ""
mistral_model: str = "mistral-large-latest"
```

---

## Implementation

### Files to Modify

#### 1. `thestill/core/llm_provider.py`

**Add import:**

```python
from mistralai import Mistral
```

**Add to MODEL_CONFIGS:**

```python
# Mistral Large 3 (flagship, 256K context)
"mistral-large-latest": ModelLimits(
    tpm=500000, rpm=500, tpd=5000000,
    context_window=256000, max_output_tokens=32768,
    supports_temperature=True, supports_structured_output=True
),
# Mistral Small 3.1 (efficient, 128K context)
"mistral-small-latest": ModelLimits(
    tpm=500000, rpm=500, tpd=5000000,
    context_window=128000, max_output_tokens=32768,
    supports_temperature=True, supports_structured_output=True
),
# Mistral Medium 3 (balanced)
"mistral-medium-latest": ModelLimits(
    tpm=500000, rpm=500, tpd=5000000,
    context_window=128000, max_output_tokens=32768,
    supports_temperature=True, supports_structured_output=True
),
# Ministral 8B (small, fast)
"ministral-8b-latest": ModelLimits(
    tpm=500000, rpm=500, tpd=5000000,
    context_window=262000, max_output_tokens=32768,
    supports_temperature=True, supports_structured_output=True
),
# Codestral (code-focused, 256K context)
"codestral-latest": ModelLimits(
    tpm=500000, rpm=500, tpd=5000000,
    context_window=256000, max_output_tokens=32768,
    supports_temperature=True, supports_structured_output=True
),
```

**Add MistralProvider class:**

```python
class MistralProvider(LLMProvider):
    """Mistral AI API provider"""

    def __init__(self, api_key: str, model: str = "mistral-large-latest"):
        self.client = Mistral(api_key=api_key)
        self.model = model

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, str]] = None,
    ) -> str:
        """Generate chat completion using Mistral API"""
        params = {
            "model": self.model,
            "messages": messages,
        }
        if temperature is not None:
            params["temperature"] = temperature
        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        if response_format and response_format.get("type") == "json_object":
            params["response_format"] = {"type": "json_object"}

        response = self.client.chat.complete(**params)
        return response.choices[0].message.content or ""

    def chat_completion_streaming(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, str]] = None,
        on_chunk: Optional[callable] = None,
    ) -> str:
        """Generate chat completion with streaming"""
        params = {
            "model": self.model,
            "messages": messages,
        }
        if temperature is not None:
            params["temperature"] = temperature
        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        if response_format and response_format.get("type") == "json_object":
            params["response_format"] = {"type": "json_object"}

        full_response = ""
        response = self.client.chat.stream(**params)
        for event in response:
            if event.data.choices and event.data.choices[0].delta.content:
                chunk = event.data.choices[0].delta.content
                full_response += chunk
                if on_chunk:
                    on_chunk(chunk)
        return full_response

    def generate_structured(
        self,
        messages: List[Dict[str, str]],
        response_model: Type[T],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> T:
        """Generate structured output using Mistral's chat.parse()"""
        params = {
            "model": self.model,
            "messages": messages,
            "response_format": response_model,
        }
        if temperature is not None:
            params["temperature"] = temperature
        if max_tokens is not None:
            params["max_tokens"] = max_tokens

        response = self.client.chat.parse(**params)
        return response.choices[0].message.parsed

    def supports_temperature(self) -> bool:
        return True

    def supports_structured_output(self) -> bool:
        if self.model in MODEL_CONFIGS:
            return MODEL_CONFIGS[self.model].supports_structured_output
        return True  # Most Mistral models support it

    def health_check(self) -> bool:
        try:
            self.client.chat.complete(
                model=self.model,
                messages=[{"role": "user", "content": "test"}],
                max_tokens=1
            )
            return True
        except Exception as e:
            logger.error(f"Mistral health check failed: {e}")
            return False

    def get_model_name(self) -> str:
        return self.model

    def get_model_display_name(self) -> str:
        return f"Mistral {self.model}"
```

**Update factory function:**

```python
def create_llm_provider(
    provider_type: str,
    # ... existing params ...
    mistral_api_key: str = "",
    mistral_model: str = "mistral-large-latest",
) -> LLMProvider:
    # ... existing code ...
    elif provider_type == "mistral":
        if not mistral_api_key:
            raise ValueError("Mistral API key is required for Mistral provider")
        return MistralProvider(api_key=mistral_api_key, model=mistral_model)
    else:
        raise ValueError(
            f"Unknown provider type: {provider_type}. "
            f"Must be 'openai', 'ollama', 'gemini', 'anthropic', or 'mistral'"
        )
```

#### 2. `thestill/utils/config.py`

**Add Config fields:**

```python
# Mistral Configuration
mistral_api_key: str = ""
mistral_model: str = "mistral-large-latest"
```

**Update load_config():**

```python
# Add validation
mistral_api_key = os.getenv("MISTRAL_API_KEY", "")
if llm_provider == "mistral" and not mistral_api_key:
    raise ValueError(
        "MISTRAL_API_KEY environment variable is required when using Mistral provider. "
        "Please set it in your .env file or environment, or switch to another provider."
    )

# Add to config_data dict
"mistral_api_key": mistral_api_key,
"mistral_model": os.getenv("MISTRAL_MODEL", "mistral-large-latest"),
```

#### 3. `pyproject.toml`

**Add dependency:**

```toml
dependencies = [
    # ... existing deps ...
    "mistralai>=1.0.0",
]
```

#### 4. `CLAUDE.md`

Update LLM Providers section and configuration documentation.

---

## Error Handling

Follow existing patterns from AnthropicProvider:

| Error Type | Status Code | Action |
|------------|-------------|--------|
| Rate limit | 429 | Retry with exponential backoff, respect retry-after header |
| Server error | 500, 502, 503, 529 | Retry with 30s delay |
| Bad request | 400 | Fail fast with clear message |
| Auth error | 401, 403 | Fail fast |
| Not found | 404 | Fail fast |

---

## Verification

### Manual Testing

```bash
# Set environment
export LLM_PROVIDER=mistral
export MISTRAL_API_KEY=your-key
export MISTRAL_MODEL=mistral-small-latest  # Use small for faster tests

# Test dry-run commands
./venv/bin/thestill clean-transcript --dry-run
./venv/bin/thestill summarize --dry-run

# Test actual processing (with a short episode)
./venv/bin/thestill clean-transcript --max-episodes 1
```

### Unit Tests

Test the following:

- `MistralProvider.chat_completion()` with mocked client
- `MistralProvider.chat_completion_streaming()` with mocked stream
- `MistralProvider.generate_structured()` with mocked parse response
- Rate limit retry logic
- Factory function with "mistral" provider type

---

## References

- [Mistral Python SDK](https://github.com/mistralai/client-python)
- [Mistral Structured Output Docs](https://docs.mistral.ai/capabilities/structured_output/custom)
- [Mistral API Reference](https://docs.mistral.ai/api)
