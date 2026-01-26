# Error Handling

This document describes error handling patterns, exception hierarchy, and error classification in thestill.

## Custom Exception Hierarchy

Domain-specific exceptions are defined in `utils/exceptions.py`:

```python
class ThestillError(Exception):
    """Base exception for all domain errors"""

class FeedParseError(ThestillError):
    """Raised when RSS/YouTube feed parsing fails"""

class TranscriptionError(ThestillError):
    """Raised when audio transcription fails"""
```

## Error Handling Patterns

### 1. Fail Fast with Validation

```python
def require_file_exists(self, file_path: Path, error_message: str) -> Path:
    """Validate file exists or raise FileNotFoundError"""
    if not file_path.exists():
        raise FileNotFoundError(error_message)
    return file_path
```

### 2. Structured Logging

All logging uses `structlog` for structured, machine-readable output:

```python
from structlog import get_logger
logger = get_logger()

# Log levels with structured context
logger.debug("Detailed diagnostic info", module="downloader", step=1)
logger.info("Episode downloaded", episode_id="abc123", file_size_mb=45.2)
logger.warning("Retry attempt", attempt=2, max_attempts=3, reason="network_timeout")
logger.error("Download failed", episode_id="xyz789", error="404_not_found")
logger.critical("Cannot load config", config_file="/path/to/config.yaml")
```

**Logging Levels**:

- `DEBUG`: Detailed diagnostic info for development
- `INFO`: Important state changes (episode downloaded, transcribed)
- `WARNING`: Recoverable issues (retry after failure)
- `ERROR`: Failures that affect single operations
- `CRITICAL`: System-wide failures

**Correlation IDs**:

Logs include correlation IDs for tracing requests across layers:

- `request_id`: HTTP request tracking (web layer)
- `command_id`: CLI command tracking
- `mcp_request_id`: MCP tool invocation tracking
- `task_id`, `worker_id`, `episode_id`: Task processing tracking

**Never Log**:

- API keys, tokens, credentials
- Full file contents
- PII (personally identifiable information)

### 3. Retry Logic with Exponential Backoff

```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=60)
)
def _download_with_retry(self, url: str) -> bytes:
    """Download with automatic retry on network errors"""
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.content
```

### 4. No Silent Failures

```python
# BAD
try:
    process_episode(episode)
except Exception:
    pass  # Silent failure - debugging nightmare

# GOOD
try:
    process_episode(episode)
except ProcessingError as e:
    logger.error("Failed to process episode", episode_guid=episode.guid, error=str(e))
    raise  # Re-raise for caller to handle
```

## Error Handling Guidelines

- **Never catch bare `except:`** - always specify exception types
- **Never silently fail** - always log errors before handling
- **Early returns** - use guard clauses to reduce nesting
- **Context in logs** - include structured context (episode_id, podcast_url, file paths, correlation IDs)
- **User-friendly CLI errors** - catch and format for end users
- **Use structlog** - all logging should use `structlog.get_logger()` for structured output

See [docs/logging-configuration.md](../docs/logging-configuration.md) for detailed logging setup and usage.

## Error Classification (`core/error_classifier.py`)

The error classifier categorizes exceptions as transient (retryable) or fatal.

### Transient Errors (auto-retry with backoff)

- HTTP 502, 503, 504, 429 (rate limit)
- Network timeouts, connection resets
- LLM API 500 errors, invalid JSON responses
- Database locked errors

### Fatal Errors (moved to DLQ)

- HTTP 404, 403, 401
- Corrupt audio files, unsupported formats
- Episode/podcast not found
- Disk full, invalid configuration

## Error Recovery

### Idempotent Operations

All pipeline steps can be safely re-run:

- Operations check for existing artifacts before processing
- Partial state is tracked and resumed

### State Tracking

Episodes track progress through pipeline via `EpisodeState` enum:

- `discovered`, `downloaded`, `downsampled`, `transcribed`, `cleaned`, `summarized`
- `failed` state includes `failed_at_stage`, `failure_reason`, `failure_type`

### Partial Failures

One episode failure doesn't stop batch processing:

- Each episode is processed independently
- Failures are logged and reported at the end

### Transaction Support

Batch updates with rollback on error:

```python
with feed_manager.transaction():
    # Multiple updates
    # Automatically rolled back if exception occurs
```

## Episode Failure Tracking

Episodes have dedicated failure tracking fields in the model:

```python
class Episode(BaseModel):
    # ... other fields ...
    failed_at_stage: Optional[str] = None  # download, transcribe, etc.
    failure_reason: Optional[str] = None   # Human-readable error
    failure_type: Optional[FailureType] = None  # transient or fatal
    failed_at: Optional[datetime] = None   # Timestamp of failure
```

**Computed Properties**:

- `is_failed`: True if episode has failure recorded
- `can_retry`: True if failure is transient (can be retried)
- `last_successful_state`: State before failure occurred

## Exponential Backoff Configuration

Task queue retry delays:

- Attempt 1: ~5 seconds
- Attempt 2: ~30 seconds
- Attempt 3: ~3 minutes
- After 3 failures: marked as `failed` (transient) or `dead` (fatal)

Jitter (±20%) is applied to prevent thundering herd effect.
