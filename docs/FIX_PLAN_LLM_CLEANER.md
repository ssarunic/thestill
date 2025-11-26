# Implementation Plan: LLM Provider & Transcript Cleaner Fixes

**Created:** 2025-11-26
**Updated:** 2025-11-26
**Status:** Complete (All 5 Tasks Done)
**Estimated Remaining Time:** 0

## Completed Tasks

- [x] **Task 1**: Fail fast on BadRequest errors in AnthropicProvider
- [x] **Task 2**: Add provider-specific max_tokens validation (using MODEL_CONFIGS)
- [x] **Task 3**: Replace print() with structured logging in `llm_provider.py` (46 statements)
- [x] **Task 4**: Deprecate legacy transcript_cleaner.py (17 print→logger, deprecation warning added)
- [x] **Task 5**: Create MockLLMProvider for tests (conftest.py, 15 tests)

## Changes Made

### llm_provider.py

- Added `BadRequestError` and `APIStatusError` imports from anthropic SDK
- Added `_get_max_output_tokens()` method to look up limits from MODEL_CONFIGS
- Added `_validate_max_tokens()` method to cap requests to model limits
- Updated `_create_completion()` to validate max_tokens and handle errors properly
- Updated `chat_completion_streaming()` with same improvements
- Replaced all 46 print() statements with logger calls (debug/info/warning/error)

### post_processor.py

- Extended `ModelLimits` NamedTuple with `max_output_tokens` field
- Updated all MODEL_CONFIGS entries with correct output token limits:
  - Claude Sonnet 4.5: 64,000 tokens
  - Claude 3.5 Sonnet/Haiku: 8,192 tokens
  - Claude 3 Opus/Sonnet/Haiku: 4,096 tokens
  - GPT-4o series: 16,384 tokens
  - Gemma models: 8,192 tokens

### transcript_cleaner.py

- Added module-level deprecation notice in docstring
- Added `DeprecationWarning` in `__init__()` method
- Replaced all 17 print() statements with logger calls

### tests/conftest.py (NEW)

- Created `MockLLMProvider` class implementing `LLMProvider` interface
- Pattern-based response matching for flexible test responses
- Tracks call count, last messages, temperature, max_tokens for assertions
- Pre-configured fixtures:
  - `mock_llm_provider`: Basic mock provider
  - `mock_llm_provider_with_defaults`: Pre-configured for transcript cleaning
  - `mock_anthropic_provider`: Anthropic-like mock with Claude model name

### tests/test_mock_llm_provider.py (NEW)

- 15 tests verifying MockLLMProvider functionality
- Tests for pattern matching, parameter tracking, default responses

### transcript_cleaning_processor.py (Follow-up Fixes)

- Added `get_max_output_tokens()` helper function that queries MODEL_CONFIGS
- Replaced hardcoded 64K tokens with dynamic lookup based on actual model
- Now correctly uses 8,192 for Claude 3.5 Sonnet (default), 64K only for Sonnet 4.5
- Phase 3 caps at 32K for reasonable response times
- **Additional cleanup**: Replaced all 40+ print() statements with logger calls (debug/info/error)

## Remaining Tasks

None - all tasks complete!

---

## Original Plan (Reference)

## Overview

This plan addresses issues identified in the senior engineer's code review:
1. Retry logic treats permanent errors (400 BadRequest) as transient
2. Missing provider-specific max_tokens validation
3. Heavy use of `print()` instead of structured logging
4. Duplicate transcript cleaning implementations
5. Tests calling real LLM APIs

---

## Task 1: Fail Fast on BadRequest Errors (HIGH PRIORITY)

**File:** `thestill/core/llm_provider.py`
**Estimated Time:** 45 minutes
**Risk:** Low (additive change)

### Problem
The `AnthropicProvider._create_completion()` method catches all exceptions with a generic handler and retries with 30-second delays. This includes 400 BadRequest errors (e.g., invalid `max_tokens`), causing the CLI to appear "stuck" for 90+ seconds before failing.

### Current Code (lines 520-535)
```python
except Exception as e:
    # Catch any other API errors (timeout, connection, etc.)
    total_retry_count += 1
    error_type = type(e).__name__
    print(f"⚠️  API error ({error_type}): {str(e)}")
    if retry_attempt < max_retries - 1:
        retry_after = 30  # Wait 30 seconds for other errors
        ...
```

### Solution
1. Import `BadRequestError` from anthropic SDK
2. Catch `BadRequestError` separately and fail immediately (no retry)
3. Keep retry logic only for transient errors (connection, timeout, 500, 529)

### Implementation

```python
# At top of file, update import
from anthropic import Anthropic, RateLimitError, BadRequestError, APIStatusError

# In _create_completion(), replace generic exception handler:
except BadRequestError as e:
    # Permanent error - invalid request parameters (e.g., max_tokens too high)
    # Do NOT retry - fail fast with clear error message
    logger.error(f"BadRequest error (not retrying): {e}")
    raise ValueError(
        f"Invalid API request: {e}. "
        f"This may be due to max_tokens ({max_tokens}) exceeding model limits. "
        f"Check provider-specific limits for {self.model}."
    ) from e

except APIStatusError as e:
    # Server errors (500, 529) - these are transient, retry
    if e.status_code in (500, 529):
        total_retry_count += 1
        if retry_attempt < max_retries - 1:
            retry_after = 30
            logger.warning(f"Server error {e.status_code}, retrying in {retry_after}s...")
            time.sleep(retry_after)
            continue
    # Other status errors - fail fast
    logger.error(f"API error (status {e.status_code}): {e}")
    raise

except Exception as e:
    # Connection errors, timeouts - transient, retry
    total_retry_count += 1
    if retry_attempt < max_retries - 1:
        retry_after = 30
        logger.warning(f"Connection error, retrying in {retry_after}s: {e}")
        time.sleep(retry_after)
        continue
    raise
```

### Apply Same Pattern To
- `_create_completion()` method (lines 468-535)
- `chat_completion_streaming()` method (lines 655-726)

### Testing
- Unit test: Mock `BadRequestError` and verify immediate failure
- Unit test: Mock `APIStatusError(500)` and verify retry behavior
- Integration test: Intentionally pass invalid `max_tokens` and verify fast failure

---

## Task 2: Add Provider-Specific Max Tokens Validation (HIGH PRIORITY)

**File:** `thestill/core/llm_provider.py`
**Estimated Time:** 1 hour
**Risk:** Low (validation before API call)

### Problem
No validation of `max_tokens` against provider limits before making API calls. Invalid values only discovered after network round-trip.

### Solution
Add a class constant for each provider's max output tokens and validate before API call.

### Implementation

```python
class AnthropicProvider(LLMProvider):
    """Anthropic Claude API provider"""

    # Model-specific output token limits
    # Source: https://docs.anthropic.com/en/docs/about-claude/models
    MODEL_MAX_OUTPUT_TOKENS = {
        "claude-sonnet-4-5": 64000,      # Sonnet 4.5
        "claude-3-5-sonnet": 8192,       # Claude 3.5 Sonnet (legacy)
        "claude-3-5-haiku": 8192,        # Claude 3.5 Haiku
        "claude-3-opus": 4096,           # Claude 3 Opus
        "claude-3-sonnet": 4096,         # Claude 3 Sonnet
        "claude-3-haiku": 4096,          # Claude 3 Haiku
    }
    DEFAULT_MAX_OUTPUT_TOKENS = 4096     # Conservative default

    def _get_max_output_tokens(self) -> int:
        """Get the maximum output tokens for the current model."""
        # Check for exact match first
        if self.model in self.MODEL_MAX_OUTPUT_TOKENS:
            return self.MODEL_MAX_OUTPUT_TOKENS[self.model]

        # Check for partial match (model names often have date suffixes)
        for model_prefix, limit in self.MODEL_MAX_OUTPUT_TOKENS.items():
            if self.model.startswith(model_prefix):
                return limit

        return self.DEFAULT_MAX_OUTPUT_TOKENS

    def _validate_max_tokens(self, requested_max_tokens: int) -> int:
        """
        Validate and potentially cap max_tokens to model limit.

        Returns:
            The validated max_tokens value (may be capped)
        """
        model_limit = self._get_max_output_tokens()

        if requested_max_tokens > model_limit:
            logger.warning(
                f"Requested max_tokens ({requested_max_tokens}) exceeds model limit "
                f"({model_limit}) for {self.model}. Capping to {model_limit}."
            )
            return model_limit

        return requested_max_tokens
```

### Update _create_completion()
```python
def _create_completion(self, ..., max_tokens: Optional[int] = None, ...):
    # Validate max_tokens before API call
    effective_max_tokens = max_tokens or 4096
    effective_max_tokens = self._validate_max_tokens(effective_max_tokens)

    params = {
        ...
        "max_tokens": effective_max_tokens,
        ...
    }
```

### Similar Updates Needed For
- `OpenAIProvider`: Add `MODEL_MAX_OUTPUT_TOKENS` dict
- `GeminiProvider`: Add validation (Gemini 2.0 Flash supports 8192 default, 65536 with thinking)
- `OllamaProvider`: Model-dependent, may need dynamic lookup

### Testing
- Unit test: Request 64000 tokens with claude-3-5-sonnet, verify capped to 8192
- Unit test: Request 64000 tokens with claude-sonnet-4-5, verify no cap
- Unit test: Verify warning logged when capping

---

## Task 3: Replace print() with Structured Logging (MEDIUM PRIORITY)

**Files:** Multiple core files (258 occurrences)
**Estimated Time:** 2 hours
**Risk:** Low (logging infrastructure already exists)

### Problem
Heavy use of `print()` throughout core modules. Cannot filter, redirect, or timestamp output.

### Existing Infrastructure
`thestill/utils/logger.py` already provides:
- `setup_logger()` function
- `ProcessingLogger` class with domain-specific methods
- Configurable log levels and file output

### Implementation Strategy

#### Phase 1: Core LLM modules (highest impact)
1. `llm_provider.py` (46 print statements)
2. `transcript_cleaning_processor.py` (38 print statements)

#### Phase 2: Supporting modules
3. `transcriber.py` (58 print statements)
4. `transcript_cleaner.py` (20 print statements)
5. Other core modules

### Pattern to Follow

```python
# At top of file
import logging

logger = logging.getLogger(__name__)

# Replace print patterns:

# Before:
print(f"  Token usage - Input: {input_tokens}, Output: {output_tokens}")

# After:
logger.debug(f"Token usage - Input: {input_tokens}, Output: {output_tokens}")

# Before:
print(f"⚠️  Warning: Response truncated...")

# After:
logger.warning(f"Response truncated due to max_tokens limit ({max_tokens})")

# Before:
print(f"❌ Rate limit retry failed after {max_retries} attempts")

# After:
logger.error(f"Rate limit retry failed after {max_retries} attempts")
```

### Log Level Guidelines
| Pattern | Level | Example |
|---------|-------|---------|
| Progress updates | `INFO` | "Processing chunk 2/5..." |
| Token usage | `DEBUG` | "Token usage - Input: 1000, Output: 500" |
| Warnings (recoverable) | `WARNING` | "Response truncated, continuing..." |
| Errors (non-recoverable) | `ERROR` | "API request failed after 3 attempts" |
| Retry attempts | `WARNING` | "Rate limit exceeded, waiting 60s..." |

### Testing
- Verify log output with `LOG_LEVEL=DEBUG`
- Verify no output with `LOG_LEVEL=ERROR`
- Verify log files written when configured

---

## Task 4: Deprecate Legacy transcript_cleaner.py (MEDIUM PRIORITY)

**Files:**
- `thestill/core/transcript_cleaner.py` (deprecate)
- `thestill/core/transcriber.py` (remove wrapper)
- `thestill/cli.py` (ensure using new processor)

**Estimated Time:** 1 hour
**Risk:** Medium (need to verify no active usage)

### Problem
Two parallel cleaning implementations:
1. `transcript_cleaning_processor.py` - New 3-phase pipeline (active)
2. `transcript_cleaner.py` - Legacy overlapping chunk approach

### Implementation

#### Step 1: Add Deprecation Warning
```python
# transcript_cleaner.py - at top of TranscriptCleaner class
import warnings

class TranscriptCleaner:
    """
    DEPRECATED: Use TranscriptCleaningProcessor instead.

    This class will be removed in a future version.
    """

    def __init__(self, ...):
        warnings.warn(
            "TranscriptCleaner is deprecated. Use TranscriptCleaningProcessor instead.",
            DeprecationWarning,
            stacklevel=2
        )
        ...
```

#### Step 2: Remove from transcriber.py
The `_clean_transcript_with_llm()` method (lines 591-655) wraps the legacy cleaner. Options:
1. **Remove entirely** - Clean separation of concerns
2. **Update to use new processor** - Keeps convenience method

Recommended: Remove entirely. Cleaning should be a separate pipeline step (`thestill clean-transcript`), not embedded in transcription.

#### Step 3: Verify CLI Usage
Ensure `thestill clean-transcript` command uses `TranscriptCleaningProcessor`:
```python
# cli.py - verify import
from thestill.core.transcript_cleaning_processor import TranscriptCleaningProcessor
```

#### Step 4: Update Documentation
- Update CLAUDE.md to remove references to legacy cleaner
- Add migration note if needed

### Testing
- Verify deprecation warning fires when using old class
- Verify `thestill clean-transcript` works with new processor
- Verify transcription still works after removing `_clean_transcript_with_llm()`

---

## Task 5: Mock LLM Calls in Tests (LOW PRIORITY)

**Files:** `tests/` directory
**Estimated Time:** 1-2 hours
**Risk:** Low (test-only changes)

### Problem
Existing tests call real LLM APIs, causing:
- 2+ minute test execution times
- Flaky tests (network/API issues)
- API costs during CI

### Solution
Create mock LLM provider for tests.

### Implementation

```python
# tests/conftest.py

import pytest
from unittest.mock import MagicMock
from thestill.core.llm_provider import LLMProvider

class MockLLMProvider(LLMProvider):
    """Mock LLM provider for testing"""

    def __init__(self, responses: dict = None):
        self.responses = responses or {}
        self.call_count = 0
        self.last_messages = None

    def chat_completion(self, messages, temperature=None, max_tokens=None, response_format=None):
        self.call_count += 1
        self.last_messages = messages

        # Return pre-configured response or default
        key = messages[-1]["content"][:50]  # Use first 50 chars as key
        return self.responses.get(key, '{"corrections": []}')

    def supports_temperature(self):
        return True

    def health_check(self):
        return True

    def get_model_name(self):
        return "mock-model"

    def get_model_display_name(self):
        return "Mock Model"


@pytest.fixture
def mock_llm_provider():
    """Fixture providing a mock LLM provider"""
    return MockLLMProvider()


@pytest.fixture
def mock_anthropic_responses():
    """Pre-configured responses for Anthropic-style testing"""
    return MockLLMProvider(responses={
        # Phase 1: Corrections
        "PODCAST CONTEXT": '{"corrections": [{"type": "spelling", "original": "teh", "corrected": "the"}]}',
        # Phase 2: Speakers
        "TRANSCRIPT:": '{"speaker_mapping": {"SPEAKER_00": "Host"}}',
        # Phase 3: Cleaned
        "ORIGINAL TRANSCRIPT": "**Host:** Welcome to the podcast.",
    })
```

### Update Existing Tests
```python
# tests/test_transcript_cleaning_processor.py

def test_clean_transcript_basic(mock_llm_provider):
    processor = TranscriptCleaningProcessor(provider=mock_llm_provider)

    result = processor.clean_transcript(
        transcript_data={"segments": [...]},
        podcast_title="Test Podcast",
        episode_title="Test Episode",
    )

    assert "cleaned_markdown" in result
    assert mock_llm_provider.call_count >= 1
```

### Testing
- Verify tests run in <5 seconds
- Verify no network calls during `pytest -m "not slow"`
- Keep integration tests with `@pytest.mark.slow` for real API testing

---

## Implementation Order

| Order | Task | Priority | Est. Time | Dependencies |
|-------|------|----------|-----------|--------------|
| 1 | Task 1: Fail fast on BadRequest | HIGH | 45 min | None |
| 2 | Task 2: Max tokens validation | HIGH | 1 hour | Task 1 |
| 3 | Task 3: Replace print() with logging | MEDIUM | 2 hours | None |
| 4 | Task 4: Deprecate legacy cleaner | MEDIUM | 1 hour | None |
| 5 | Task 5: Mock LLM in tests | LOW | 1-2 hours | Tasks 1-4 |

**Total Estimated Time:** 5.75 - 7.75 hours

---

## Validation Checklist

Before marking complete:

- [ ] `BadRequestError` triggers immediate failure (no 30s waits)
- [ ] Invalid `max_tokens` logged as warning and capped
- [ ] No `print()` statements in modified files
- [ ] All tests pass: `pytest -v`
- [ ] Type checks pass: `mypy thestill/`
- [ ] Lint passes: `make lint`
- [ ] Manual test: `thestill clean-transcript` with Anthropic provider

---

## Rollback Plan

If issues arise:
1. All changes are isolated to specific files
2. Git revert individual commits if needed
3. Legacy `transcript_cleaner.py` remains functional until explicitly removed

---

## References

- [Anthropic API Errors](https://platform.claude.com/docs/en/api/errors)
- [Claude Model Limits](https://docs.anthropic.com/en/docs/about-claude/models)
- [Claude Sonnet 4.5 Specs](https://www.anthropic.com/claude/sonnet)
- Existing logger: `thestill/utils/logger.py`
