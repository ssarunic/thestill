# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Project Overview

thestill.me is an automated podcast transcription and summarization pipeline built with Python. It converts audio podcasts into readable, summarized content using local Whisper or cloud-based transcription (Google, ElevenLabs), and supports multiple LLM providers for analysis.

## Development Commands

### Virtual Environment

**IMPORTANT**: Always use `./venv/bin/python` or `./venv/bin/pytest` instead of bare `python` or `pytest`.

```bash
./venv/bin/python script.py
./venv/bin/python -m pytest tests/
./venv/bin/thestill <command>
```

### Installation

```bash
./venv/bin/pip install -e .          # Development mode
./venv/bin/pip install -e ".[dev]"   # With dev dependencies
cp .env.example .env                  # Configure environment
```

### CLI Commands

```bash
# Podcast management
thestill add "https://example.com/rss"    # Add feed (RSS, Apple, YouTube)
thestill list                              # List podcasts
thestill remove <podcast-id>               # Remove podcast
thestill status                            # System status

# Pipeline (run in order)
thestill refresh                    # 1. Discover new episodes
thestill download                   # 2. Download audio
thestill downsample                 # 3. Convert to 16kHz WAV
thestill transcribe                 # 4. Transcribe to JSON
thestill clean-transcript           # 5. Clean with LLM
thestill summarize                  # 6. Summarize

# All commands support: --podcast-id, --max-episodes, --dry-run

# Web server
thestill server                     # Start on localhost:8000
```

### Testing & Quality

```bash
make test          # Run tests with coverage
make format        # black + isort
make lint          # pylint + mypy
make check         # All checks
```

## File Structure

```
thestill/
├── cli.py                 # Command-line interface
├── core/                  # Atomic processors (download, transcribe, clean, etc.)
├── models/                # Pydantic data models
├── repositories/          # SQLite persistence
├── services/              # Business logic layer
├── mcp/                   # MCP server for AI integration
├── web/                   # FastAPI server + React frontend
└── utils/                 # Config, PathManager, Logger

data/
├── podcasts.db            # SQLite database
├── original_audio/        # Downloaded audio (MP3, M4A)
├── downsampled_audio/     # 16kHz WAV files
├── raw_transcripts/       # JSON transcripts
├── clean_transcripts/     # Cleaned Markdown
├── summaries/             # Episode analysis
├── podcast_facts/         # Podcast-level facts
└── episode_facts/         # Episode-level facts
```

## Key Technologies

- **Transcription**: Whisper, WhisperX, Google Cloud Speech-to-Text, ElevenLabs
- **LLM Providers**: OpenAI, Anthropic, Google Gemini, Mistral, Ollama
- **Storage**: SQLite with indexed queries
- **Dependencies**: Pydantic, Click, feedparser, yt-dlp, pydub

## Coding Principles

**DRY**: Use existing abstractions (PathManager, services, repositories) rather than duplicating patterns.

**Markdown Style**: Follow `.markdownlint.yaml` - blank lines before/after code blocks and lists.

## Logging

Thestill uses `structlog` for structured, machine-readable logging. All `print()` statements have been eliminated in favor of structured logging.

### Quick Start

```python
from structlog import get_logger

logger = get_logger()

# Always use structured context
logger.info("Episode downloaded", episode_id=episode.guid, file_size_mb=45.2)
logger.error("Download failed", episode_id=episode.guid, error=str(e), exc_info=True)
```

### Environment Configuration

```bash
# Development: colored console output
export LOG_FORMAT=console
export LOG_LEVEL=DEBUG

# Production: JSON output for cloud platforms
export LOG_FORMAT=json
export LOG_LEVEL=INFO

# View logs with jq
LOG_FORMAT=json thestill status 2>&1 | jq .
```

### Correlation IDs

Logs automatically include correlation IDs for request tracking:

- `request_id`: HTTP requests (web layer)
- `command_id`: CLI commands
- `mcp_request_id`: MCP tool invocations
- `task_id`, `worker_id`, `episode_id`: Task processing

These IDs enable tracing requests across all layers of the application.

### Best Practices

- Always use structured context (keyword arguments) instead of string formatting
- Include relevant entity IDs (episode_id, podcast_id, etc.) for filtering
- Use `exc_info=True` when logging exceptions
- Never log secrets, API keys, or PII
- See [docs/logging-configuration.md](docs/logging-configuration.md) for full guide

## Documentation

### User Documentation (`docs/`)

- [configuration.md](docs/configuration.md) - Environment variables and settings
- [logging-configuration.md](docs/logging-configuration.md) - Structured logging setup and usage
- [transcription-providers.md](docs/transcription-providers.md) - Provider setup guides
- [web-server.md](docs/web-server.md) - API endpoints and webhooks
- [mcp-usage.md](docs/mcp-usage.md) - MCP server usage
- [transcript-cleaning.md](docs/transcript-cleaning.md) - Cleaning configuration
- [code-guidelines.md](docs/code-guidelines.md) - Development standards

### Technical Specs (`specs/`)

- [architecture.md](specs/architecture.md) - Layered architecture, design patterns, data flow
- [api-reference.md](specs/api-reference.md) - REST API endpoints, request/response formats
- [error-handling.md](specs/error-handling.md) - Exception hierarchy, retry logic
- [testing.md](specs/testing.md) - Test strategy, coverage targets, type hints

@AGENTS.md
