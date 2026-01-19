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

## Documentation

### User Documentation (`docs/`)

- [configuration.md](docs/configuration.md) - Environment variables and settings
- [transcription-providers.md](docs/transcription-providers.md) - Provider setup guides
- [web-server.md](docs/web-server.md) - API endpoints and webhooks
- [mcp-usage.md](docs/mcp-usage.md) - MCP server usage
- [transcript-cleaning.md](docs/transcript-cleaning.md) - Cleaning configuration
- [code-guidelines.md](docs/code-guidelines.md) - Development standards

### Technical Specs (`specs/`)

- [architecture.md](specs/architecture.md) - Layered architecture, design patterns, data flow
- [error-handling.md](specs/error-handling.md) - Exception hierarchy, retry logic
- [testing.md](specs/testing.md) - Test strategy, coverage targets, type hints

@AGENTS.md
