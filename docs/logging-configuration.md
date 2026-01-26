# Logging Configuration Guide

Complete guide to logging in thestill using structlog for structured, machine-readable output.

## Quick Start

### Local Development

```bash
# Colored console output with debug level
export LOG_FORMAT=console
export LOG_LEVEL=DEBUG
thestill status
```

### Production

```bash
# JSON output for cloud platforms
export LOG_FORMAT=json
export LOG_LEVEL=INFO
thestill server
```

### Viewing Logs

```bash
# Pretty-print JSON logs
LOG_FORMAT=json thestill status 2>&1 | jq .

# Filter by log level
LOG_FORMAT=json thestill transcribe 2>&1 | jq 'select(.level=="error")'

# Follow specific episode
LOG_FORMAT=json thestill server 2>&1 | jq 'select(.episode_id=="abc123")'
```

## Environment Variables

| Variable | Values | Default | Description |
|----------|--------|---------|-------------|
| `LOG_LEVEL` | DEBUG, INFO, WARNING, ERROR, CRITICAL | INFO | Minimum log level to emit |
| `LOG_FORMAT` | console, json, ecs, gcp, cloudwatch, auto | auto | Output format (auto detects TTY) |
| `LOG_FILE` | file path | none | Optional file output path (rotates at 100MB) |
| `SERVICE_NAME` | string | thestill | Service name for cloud logs |
| `SERVICE_VERSION` | string | 1.0.0 | Service version for cloud logs |

### LOG_FORMAT Options

- **console**: Colored output for local development (automatically selected for terminals)
- **json**: Generic structured JSON (default for non-TTY environments)
- **ecs**: Elastic Common Schema for AWS Elastic Stack (Elasticsearch, Kibana)
- **gcp**: Google Cloud Logging format for GCP Cloud Logging
- **cloudwatch**: AWS CloudWatch Logs format (simpler/cheaper alternative to ECS)
- **auto**: Automatically selects console for TTY, json otherwise

### LOG_LEVEL Guidelines

- **DEBUG**: Use during development or when investigating issues. High volume.
- **INFO**: Default for production. State changes, successful operations.
- **WARNING**: Recoverable issues, retries, degraded operation.
- **ERROR**: Operation failures, exceptions caught.
- **CRITICAL**: System failures, service unavailability.

## Log Formats

### Console (Development)

```
2026-01-25 16:42:31 [info     ] Episode downloaded         episode_id=abc123 file_size_mb=45.2 duration_sec=3600
2026-01-25 16:43:15 [error    ] Transcription failed       episode_id=abc123 error=timeout provider=whisper
```

### JSON (Production)

```json
{
  "event": "Episode downloaded",
  "level": "info",
  "timestamp": "2026-01-25T16:42:31.123456Z",
  "episode_id": "abc123",
  "file_size_mb": 45.2,
  "duration_sec": 3600
}
```

### ECS (AWS Elastic)

```json
{
  "@timestamp": "2026-01-25T16:42:31.123Z",
  "log.level": "info",
  "message": "Episode downloaded",
  "ecs.version": "8.0.0",
  "episode_id": "abc123",
  "file_size_mb": 45.2
}
```

### GCP (Google Cloud)

```json
{
  "severity": "INFO",
  "message": "Episode downloaded",
  "timestamp": "2026-01-25T16:42:31.123456Z",
  "logging.googleapis.com/labels": {
    "episode_id": "abc123"
  }
}
```

### CloudWatch (AWS)

A simpler, cheaper alternative to ECS format for AWS deployments. Works with CloudWatch Logs Insights without requiring the Elastic Stack.

```json
{
  "message": "Episode downloaded",
  "@timestamp": "2026-01-25T16:42:31.123456Z",
  "timestamp": "2026-01-25T16:42:31.123456Z",
  "level": "INFO",
  "episode_id": "abc123",
  "file_size_mb": 45.2,
  "request_id": "r-abc123"
}
```

Key differences from JSON format:

- `message` field (alias for `event`, CloudWatch convention)
- `@timestamp` field (CloudWatch convention)
- Uppercase `level` (INFO, ERROR, WARNING)

## Correlation IDs

Thestill uses correlation IDs to track requests across all layers. These IDs are automatically added to logs via structlog context variables.

### 4 Correlation Layers

1. **Web Layer** (`request_id`): HTTP requests
2. **CLI Layer** (`command_id`): CLI command invocations
3. **MCP Layer** (`mcp_request_id`): MCP tool invocations
4. **Task Layer** (`task_id`, `worker_id`, `episode_id`): Background processing

### Correlation Flow Example

```
HTTP Request (request_id=r-123)
  ↓
Task Created (task_id=t-456, request_id=r-123)
  ↓
Worker Processes (worker_id=w-789, task_id=t-456)
  ↓
Episode Transcribed (episode_id=abc, worker_id=w-789, task_id=t-456)
```

All logs in this chain will include the relevant correlation IDs, enabling you to trace the entire request lifecycle.

## Usage Examples

### Core Module Logging

```python
from structlog import get_logger

logger = get_logger()

class AudioDownloader:
    def download_episode(self, episode: Episode) -> Optional[Path]:
        """Download episode audio"""
        logger.info(
            "Downloading episode",
            episode_id=episode.guid,
            title=episode.title,
            url=episode.audio_url
        )

        try:
            audio_path = self._download(episode.audio_url)
            logger.info(
                "Downloaded successfully",
                episode_id=episode.guid,
                audio_path=str(audio_path),
                file_size_mb=audio_path.stat().st_size / 1024 / 1024
            )
            return audio_path
        except Exception as e:
            logger.error(
                "Download failed",
                episode_id=episode.guid,
                error=str(e),
                exc_info=True  # Include traceback
            )
            return None
```

### Web Layer Logging

Request logging is automatic via middleware. Each HTTP request gets a unique `request_id`:

```python
# Automatic via middleware
# GET /api/podcasts/123
# → request_id=r-abc123, method=GET, path=/api/podcasts/123

# Manual logging in routes
from structlog import get_logger
logger = get_logger()

@app.get("/api/podcasts/{podcast_id}")
async def get_podcast(podcast_id: int):
    logger.info("Fetching podcast", podcast_id=podcast_id)
    # request_id is automatically included from context
```

### MCP Layer Logging

MCP tool invocations are tracked with `mcp_request_id`:

```python
# Automatic via middleware
# Tool: search_episodes, Args: {"query": "python"}
# → mcp_request_id=mcp-xyz789, tool_name=search_episodes

# Manual logging in MCP tools
@server.call_tool()
async def search_episodes(query: str):
    logger.info("Searching episodes", query=query, search_type="fulltext")
    # mcp_request_id is automatically included
```

### CLI Layer Logging

CLI commands are tracked with `command_id`:

```python
# Automatic via CLI logging wrapper
# thestill transcribe --podcast-id 1
# → command_id=cmd-def456, command_name=transcribe

# Manual logging in CLI commands
import click
from structlog import get_logger
from thestill.utils.cli_logging import with_command_logging

logger = get_logger()

@click.command()
@with_command_logging
def transcribe(podcast_id: int):
    logger.info("Starting transcription", podcast_id=podcast_id)
    # command_id is automatically included from context
```

### Task Layer Logging

Background tasks include worker and task context:

```python
from structlog import get_logger
logger = get_logger()

def process_task(task: Task, worker_id: str):
    # Bind context for entire task lifecycle
    logger = logger.bind(task_id=task.id, worker_id=worker_id)

    logger.info("Task started", task_type=task.type)

    # All subsequent logs include task_id and worker_id
    logger.info("Processing episode", episode_id=task.episode_id)
    logger.info("Task completed", duration_sec=elapsed)
```

## Best Practices

### Always Use Structured Context

```python
# Good: Structured context
logger.info("Episode processed", episode_id=episode.guid, duration_sec=elapsed)

# Bad: String formatting
logger.info(f"Processed episode {episode.guid} in {elapsed}s")
```

### Include Relevant IDs

```python
# Always include entity IDs for filtering/correlation
logger.error(
    "Transcription failed",
    episode_id=episode.guid,
    podcast_id=podcast.id,
    provider="whisper",
    error=str(e)
)
```

### Use exc_info for Exceptions

```python
# Include full traceback for errors
try:
    transcribe_audio(audio_path)
except Exception as e:
    logger.error("Transcription error", error=str(e), exc_info=True)
    raise
```

### Never Log Secrets

```python
# Bad: Logs API key
logger.debug("Calling API", api_key=config.openai_api_key, url=url)

# Good: Mask sensitive data
logger.debug("Calling API", api_key_length=len(config.openai_api_key), url=url)
```

### Quiet Mode for CLI

Use the `--quiet` flag to suppress INFO logs and only show warnings/errors:

```bash
# Only show errors
thestill --quiet refresh

# Only show warnings and errors
thestill --quiet transcribe --podcast-id 1
```

## Troubleshooting

### Logs Not Appearing

**Symptom**: No log output when running commands

**Solution**:

```bash
# Check LOG_LEVEL
echo $LOG_LEVEL  # Should be DEBUG or INFO

# Force logging output
LOG_LEVEL=DEBUG LOG_FORMAT=console thestill status
```

### JSON Parse Errors

**Symptom**: `jq` fails with parse errors

**Solution**:

```bash
# User output is sent to stdout, logs to stderr
# Only pipe stderr to jq
thestill status 2>&1 1>/dev/null | jq .

# Or redirect stdout to /dev/null
thestill status 2>&1 | jq . 1>/dev/null
```

### Missing Correlation IDs

**Symptom**: `request_id` or `task_id` not in logs

**Solution**:

```python
# Ensure middleware is enabled
# Web: LoggingMiddleware in app.middleware
# MCP: LoggingMiddleware in mcp.server
# CLI: @with_command_logging decorator

# Check context binding
from structlog import get_logger
logger = get_logger()

# Bind context explicitly if not using middleware
logger = logger.bind(request_id="r-123")
logger.info("Now includes request_id in all logs")
```

### High Log Volume

**Symptom**: Too many DEBUG logs in production

**Solution**:

```bash
# Set appropriate log level for environment
export LOG_LEVEL=INFO  # Production
export LOG_LEVEL=DEBUG  # Development

# Use structured filtering with jq
thestill server 2>&1 | jq 'select(.level!="debug")'
```

### Performance Impact

**Symptom**: Logging slowing down requests

**Solution**:

```bash
# Use JSON format in production (faster than console)
export LOG_FORMAT=json

# Reduce log level
export LOG_LEVEL=WARNING

# Use file output instead of stderr
export LOG_FILE=/var/log/thestill/app.log
```

## Cloud Deployment

For cloud-specific deployment guides and query examples:

- **AWS CloudWatch (Simple)**: See [logging-cloud-deployment.md](logging-cloud-deployment.md#aws-cloudwatch-logs-simple)
- **AWS Elastic (ECS)**: See [logging-cloud-deployment.md](logging-cloud-deployment.md#aws-elastic-ecs)
- **GCP Cloud Logging**: See [logging-cloud-deployment.md](logging-cloud-deployment.md#google-cloud-platform-gcp)
- **CloudWatch Query Examples**: See [logging-cloudwatch-queries.md](logging-cloudwatch-queries.md)
- **Elastic Query Examples**: See [logging-elastic-queries.md](logging-elastic-queries.md)
- **GCP Query Examples**: See [logging-gcp-queries.md](logging-gcp-queries.md)

## Query Examples

### Find All Errors for Episode

```bash
# CLI with jq
LOG_FORMAT=json thestill transcribe 2>&1 | jq 'select(.episode_id=="abc123" and .level=="error")'

# Elastic
GET /logs/_search
{
  "query": {
    "bool": {
      "must": [
        { "match": { "episode_id": "abc123" } },
        { "match": { "log.level": "error" } }
      ]
    }
  }
}

# GCP
resource.type="cloud_run_revision"
jsonPayload.episode_id="abc123"
severity="ERROR"
```

### Trace HTTP Request Across Layers

```bash
# Find request_id from initial HTTP log
# → request_id=r-abc123

# Elastic
GET /logs/_search
{
  "query": { "match": { "request_id": "r-abc123" } },
  "sort": [ { "@timestamp": "asc" } ]
}

# GCP
jsonPayload.request_id="r-abc123"
| timestamp asc
```

### MCP Tool Usage Analytics

```bash
# Elastic
GET /logs/_search
{
  "query": { "match": { "event": "mcp_tool_invoked" } },
  "aggs": {
    "by_tool": {
      "terms": { "field": "tool_name.keyword" }
    }
  }
}

# GCP
jsonPayload.event="mcp_tool_invoked"
| fields jsonPayload.tool_name
| group_by jsonPayload.tool_name
```

### API Performance Monitoring

```bash
# Elastic - Average latency by endpoint
GET /logs/_search
{
  "query": { "match": { "event": "http_request_completed" } },
  "aggs": {
    "by_endpoint": {
      "terms": { "field": "endpoint.keyword" },
      "aggs": {
        "avg_latency": { "avg": { "field": "latency_ms" } }
      }
    }
  }
}

# GCP
jsonPayload.event="http_request_completed"
| fields jsonPayload.endpoint, jsonPayload.latency_ms
| group_by jsonPayload.endpoint
| avg(jsonPayload.latency_ms)
```

## File Logging

Enable file logging for persistent storage:

```bash
export LOG_FILE=/var/log/thestill/app.log
export LOG_FORMAT=json
thestill server
```

**Features**:

- Automatic rotation at 100MB
- Keeps 5 backup files
- JSON format for easy parsing
- Thread-safe writes

**Log file structure**:

```
/var/log/thestill/
├── app.log          # Current log file
├── app.log.1        # Rotated backup (newest)
├── app.log.2
├── app.log.3
├── app.log.4
└── app.log.5        # Oldest backup
```

## Testing Logging

### Validate JSON Output

```bash
# Test JSON parsing
LOG_FORMAT=json thestill status 2>&1 | jq . > /dev/null && echo "Valid JSON"

# Test all log levels
LOG_LEVEL=DEBUG LOG_FORMAT=json thestill status 2>&1 | jq '.level' | sort | uniq
```

### Validate Correlation IDs

```bash
# Web layer - check request_id
LOG_FORMAT=json thestill server 2>&1 | grep request_id | head -1 | jq .

# CLI layer - check command_id
LOG_FORMAT=json thestill status 2>&1 | grep command_id | head -1 | jq .

# Task layer - check worker_id
LOG_FORMAT=json thestill transcribe 2>&1 | grep worker_id | head -1 | jq .
```

### Performance Testing

```bash
# Measure logging overhead
time LOG_FORMAT=console thestill status  # Baseline
time LOG_FORMAT=json thestill status     # JSON overhead
time LOG_LEVEL=WARNING thestill status   # Reduced logging

# Should be < 5ms overhead for JSON format
```

## Migration from print()

All `print()` statements have been replaced with structured logging:

```python
# Old
print(f"Downloading {episode.title}...")

# New
logger.info("Downloading episode", episode_id=episode.guid, title=episode.title)
```

**Benefits**:

- Machine-readable output for automation
- Correlation IDs for request tracking
- Cloud-native observability integration
- Filterable by level, context, IDs
- No performance impact from string formatting

## Related Documentation

- [Error Handling](../specs/error-handling.md) - Exception patterns and logging guidelines
- [Code Guidelines](code-guidelines.md) - Logging best practices and examples
- [Cloud Deployment](logging-cloud-deployment.md) - AWS and GCP deployment guides
- [CloudWatch Queries](logging-cloudwatch-queries.md) - CloudWatch Logs Insights query examples
- [Elastic Queries](logging-elastic-queries.md) - Elasticsearch query examples
- [GCP Queries](logging-gcp-queries.md) - Cloud Logging query examples
