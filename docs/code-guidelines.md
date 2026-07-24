# Code Guidelines for Thestill

## Project Context

Thestill is an automated podcast transcription and summarization pipeline built with Python. The project emphasizes atomic operations, clean separation of concerns, and maintainable architecture for processing audio → transcription → cleaning → summarization workflows.

## Language and Toolchain

**Python Version**: 3.10+ (`requires-python = ">=3.10"`; `.python-version` pins 3.12.0 and CI runs 3.12)
**Package Manager**: uv with a committed `uv.lock` (CI installs with `uv sync --frozen`); hatchling remains the build backend
**Key Dependencies**: fastapi, uvicorn, pydantic, click, openai, anthropic, google-genai, mcp, structlog, authlib/PyJWT, feedparser, yt-dlp. Local Whisper/WhisperX live in the optional `[local-transcription]` extra, not in core dependencies.

### Development Tools

```bash
# Linting — ruff is the only linter gated in CI
ruff check thestill/

# Formatting (local-only; format the files you touched)
black thestill/ tests/
isort thestill/ tests/

# Type Checking (local-only)
mypy thestill/

# Linting (local-only)
pylint thestill/

# Testing (e2e suite excluded by default)
pytest --ignore=tests/e2e
```

The repo is not currently black-clean — format only the files you edited rather than running repo-wide `make format`.

### Make Targets

The Makefile is the canonical interface for these tools:

- `make test` — pytest with coverage (`--ignore=tests/e2e`)
- `make test-unit` / `make test-integration` — scoped suites with coverage
- `make test-e2e` — browser E2E (`node tests/e2e/web/test_web_auth.cjs`; requires a running server)
- `make lint` — ruff + pylint + mypy
- `make format` — black + isort over `thestill/` and `tests/`
- `make check` — format → lint → typecheck → test
- `make run-mcp` — run the MCP server
- `make corpus-backfill` — embed and index cleaned transcripts into the chunk index
- `make rebuild-entity-pages` — regenerate Obsidian entity Markdown pages

### Tool Configuration

Tool configuration lives in `pyproject.toml`; pylint additionally reads the `.pylintrc` that already exists at the repo root (py-version 3.12, message disables, naming rules).

**pyproject.toml**:

```toml
[tool.black]
line-length = 120
target-version = ['py39', 'py310', 'py311']
include = '\.pyi?$'

[tool.isort]
profile = "black"
line_length = 120
multi_line_output = 3

[tool.mypy]
python_version = "3.9"  # note: lags the 3.10 floor in requires-python
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = false
ignore_missing_imports = true
no_strict_optional = true
exclude = [
    "tests/",
    "data/",
    ".venv/",
]

[tool.pylint.main]
max-line-length = 120
disable = [
    "C0114",  # missing-module-docstring
    "C0115",  # missing-class-docstring
    "C0116",  # missing-function-docstring
    "R0913",  # too-many-arguments
    "R0914",  # too-many-locals
    "R0801",  # duplicate-code
    "W0212",  # protected-access
]
ignore-paths = [
    "^tests/.*$",
    "^data/.*$",
    "^\\.venv/.*$",
]

[tool.ruff]
# Minimal ruff configuration: forbid print() and tz-naive datetimes.
exclude = ["tests/", "data/", ".venv/"]

[tool.ruff.lint]
select = [
    "T201",    # print() found
    "DTZ001",  # datetime(...) without tzinfo
    "DTZ002",  # datetime.today() (naive local)
    "DTZ003",  # datetime.utcnow()
    "DTZ004",  # datetime.utcfromtimestamp()
    "DTZ005",  # datetime.now() without tz
    "DTZ006",  # datetime.fromtimestamp() without tz
]
```

CI gates only ruff (`uv run ruff check thestill/`) — the T201 print ban and the DTZ tz-naive datetime ban (spec #42). black, isort, pylint, and mypy are local-only checks run via `make`.

## Naming Conventions

### Files and Directories

- **Module files**: `snake_case.py` (e.g., `feed_manager.py`, `audio_downloader.py`)
- **Test files**: `test_*.py` (e.g., `test_feed_manager.py`, `test_cleaning.py`)
- **Package directories**: `lowercase` (e.g., `core/`, `utils/`, `models/`)

### Classes and Functions

- **Classes**: `PascalCase` (e.g., `PodcastFeedManager`, `AudioDownloader`, `PathManager`)
- **Functions/methods**: `snake_case` (e.g., `download_episode()`, `get_new_episodes()`)
- **Private methods**: `_leading_underscore()` (e.g., `_save_podcasts()`, `_load_podcasts()`)
- **Constants**: `UPPER_SNAKE_CASE` (e.g., `MAX_EPISODES_PER_PODCAST`, `REASONING_MODELS`)

### Variables

- **Local variables**: `snake_case` (e.g., `episode_count`, `audio_path`)
- **Instance variables**: `snake_case` (e.g., `self.storage_path`, `self.podcasts`)
- **Type hints**: Always use for function signatures and class attributes
- **Booleans**: Use `is_*`, `has_*`, `enable_*` prefixes (e.g., `is_youtube_url`, `enable_diarization`)

### Model Fields (Pydantic)

- Use `snake_case` for all Pydantic model fields
- Use `Optional[type]` for nullable fields with `None` default
- Document complex fields with Field() descriptors

## Project Structure

The tree below is illustrative, not exhaustive — `core/` alone has grown to ~50 modules.

```
thestill/
├── cli.py                    # Click CLI entry point
├── logging.py                # structlog configuration
├── core/                     # Core processing modules (~50, single responsibility)
│   ├── feed_manager.py       # RSS/YouTube feed parsing
│   ├── audio_downloader.py   # Audio download (atomic: only downloads)
│   ├── audio_preprocessor.py # Audio downsampling (atomic: only downsamples)
│   ├── transcriber_factory.py # Whisper/Google/ElevenLabs/Dalston transcribers
│   ├── transcript_cleaning_processor.py  # LLM-based cleaning
│   ├── llm_provider.py       # Abstract LLM interface
│   ├── task_worker.py        # Background task processing
│   ├── queue_manager.py      # Task queue (SQLite + Postgres variants)
│   ├── circuit_breaker.py    # Provider failure isolation
│   ├── briefing_scheduler.py # Per-user scheduled briefings
│   ├── entity_*.py           # Entity extraction/resolution pipeline
│   ├── facts_*.py            # Podcast/episode facts pipeline
│   └── ...
├── models/                   # Pydantic data models (podcast, user, briefing, ...)
├── repositories/             # Persistence layer (spec #44) — dual SQLite/Postgres
│   ├── factory.py            #   implementations selected behind factory.py
│   ├── sqlite_*.py           #   via DATABASE_URL; a defining pattern of the codebase
│   └── postgres_*.py
├── services/                 # Business logic layer
│   ├── podcast_service.py    # Podcast CRUD operations
│   ├── auth_service.py       # Multi-user authentication
│   ├── follower_service.py   # Per-user podcast follows
│   ├── briefing_service.py   # Briefing generation (+ narration/ for audio)
│   └── ...
├── search/                   # Semantic search: sqlite-vec + pgvector clients
├── web/                      # FastAPI app + middleware + Vite/TS SPA
│   ├── app.py
│   ├── routes/               # api_*.py (incl. api_briefings.py), auth.py, webhooks.py
│   ├── middleware/
│   └── frontend/             # React SPA source (built output ships in web/static/)
├── webhook/                  # Webhook delivery
├── evals/                    # LLM-as-judge quality evals
├── migrations/               # Alembic migrations (Postgres)
├── mcp/                      # MCP server integration
└── utils/                    # Config, PathManager, shared utilities

tests/                        # Top-level test suite (sibling of thestill/, see Testing)
```

### Layer Separation

1. **Interface Layer** (`cli.py`, `web/`, `mcp/`): User interfaces — CLI argument parsing, FastAPI routes/middleware and the React SPA, MCP tools
2. **Service Layer** (`services/`): Business logic, orchestration, high-level operations (podcast, auth, follower, briefing, narration)
3. **Core Layer** (`core/`): Atomic processors, single-responsibility workers
4. **Repository Layer** (`repositories/`): Persistence behind abstract interfaces; `factory.py` selects the SQLite or Postgres implementation from `DATABASE_URL` (spec #44)
5. **Model Layer** (`models/`): Data structures, validation, serialization
6. **Infrastructure Layer** (`utils/`, `logging.py`): Config, logging, paths, external integrations

**Rules**:

- CLI depends on Services and Core, never the reverse
- Core modules should not depend on CLI
- Depend on repository interfaces via the factory, never on a concrete backend
- Use dependency injection for services and providers
- Keep third-party API calls in Core or Utils, not in Models

## Code Style

### Line Length and Formatting

- **Max line length**: 120 characters
- Use Black formatter for consistent style
- Use isort for import ordering
- No trailing whitespace

### Imports

```python
# Standard library (alphabetical)
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# Third-party (alphabetical)
import click
import feedparser
from pydantic import BaseModel

# Local imports (relative, alphabetical)
from ..models.podcast import Podcast, Episode
from ..utils.config import Config
from ..utils.logger import setup_logger
```

### Docstrings

Use Google-style docstrings for public functions and classes:

```python
def download_episode(self, episode: Episode, podcast_title: str) -> Optional[str]:
    """
    Download audio file for a single episode.

    Args:
        episode: Episode object with audio_url
        podcast_title: Name of the podcast for file organization

    Returns:
        Path to downloaded audio file, or None if download failed

    Raises:
        ValueError: If episode.audio_url is invalid
        IOError: If download fails after retries
    """
```

Private functions may use single-line docstrings or skip docstrings if the code is self-explanatory.

### Type Hints

Always use type hints for function signatures:

```python
def get_new_episodes(
    self,
    max_episodes_per_podcast: Optional[int] = None
) -> List[tuple[Podcast, List[Episode]]]:
    """Check all feeds for new episodes"""
```

## Functions and Classes

### Function Design

- **Single Responsibility**: Each function does ONE thing
- **Small Functions**: Target 10-20 lines, max 50 lines
- **Limit Arguments**: Max 4 positional args. Use dataclasses/Pydantic for more.
- **No Side Effects**: Make side effects explicit in function names (e.g., `mark_episode_downloaded()`)
- **Early Returns**: Use guard clauses to reduce nesting

**Good**:

```python
def mark_episode_downloaded(self, rss_url: str, guid: str, audio_filename: str) -> bool:
    """Mark episode as downloaded with audio file path"""
    podcast = self._find_podcast(rss_url)
    if not podcast:
        return False

    episode = self._find_episode(podcast, guid)
    if not episode:
        return False

    episode.audio_path = audio_filename
    self._save_podcasts()
    return True
```

**Bad**:

```python
def process_episode(self, rss_url, guid, audio_url, should_download, should_transcribe):
    # Too many args, unclear responsibilities
    podcast = self._find_podcast(rss_url)
    if podcast:
        episode = self._find_episode(podcast, guid)
        if episode:
            if should_download:
                # download logic
            if should_transcribe:
                # transcribe logic
```

### Class Design

- **Single Responsibility**: Each class has ONE clear purpose
- **Composition Over Inheritance**: Prefer dependency injection
- **Immutable Data**: Use Pydantic models for data structures
- **Small Interfaces**: Keep public API minimal

**Good**:

```python
class AudioDownloader:
    """Downloads podcast audio files from URLs"""

    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)
        self.youtube_downloader = YouTubeDownloader(output_dir)

    def download_episode(self, episode: Episode, podcast_title: str) -> Optional[str]:
        """Download a single episode"""
        # Atomic: only downloads, doesn't transcribe or process
```

**Bad**:

```python
class PodcastProcessor:
    """Handles everything podcast-related"""
    # God class anti-pattern
```

## Error Handling

### Fail Fast

```python
def transcribe_audio(self, audio_path: str) -> Dict:
    """Transcribe audio file"""
    if not Path(audio_path).exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    if not audio_path.endswith(('.mp3', '.wav', '.m4a')):
        raise ValueError(f"Unsupported audio format: {audio_path}")

    # Continue with transcription
```

### Typed Exceptions

Define custom exceptions for domain errors:

```python
class TranscriptionError(Exception):
    """Raised when audio transcription fails"""

class FeedParseError(Exception):
    """Raised when RSS feed parsing fails"""
```

### Logging

Use `structlog` for all logging. This provides structured, machine-readable output with automatic context:

```python
from structlog import get_logger

logger = get_logger()

def download_episode(self, episode: Episode) -> Optional[str]:
    """Download episode audio"""
    logger.info("Downloading episode", episode_id=episode.guid, title=episode.title)

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
            title=episode.title,
            error=str(e),
            exc_info=True
        )
        return None
```

**Logging Levels**:

- `DEBUG`: Detailed diagnostic info for development
- `INFO`: Important state changes (episode downloaded, transcribed)
- `WARNING`: Recoverable issues (retry after failure)
- `ERROR`: Failures that affect single operations
- `CRITICAL`: System-wide failures

**Structured Context**:

Always include relevant context as keyword arguments:

```python
# Good: Structured context
logger.info("Task started", task_id=task.id, worker_id=worker.id, episode_id=episode.guid)

# Bad: String formatting
logger.info(f"Task {task.id} started by worker {worker.id} for episode {episode.guid}")
```

**Correlation IDs**:

Logs automatically include correlation IDs from context:

- `request_id`: HTTP requests (web layer) and MCP requests (with `mcp_method`)
- `command_id`: CLI commands
- `task_id`, `worker_id`, `episode_id`: Task processing

**Never Log**:

- API keys, tokens, credentials
- Full file contents
- PII (personally identifiable information)

**Configuration**:

See [docs/logging-configuration.md](logging-configuration.md) for environment variables, output formats, and cloud deployment.

### No Silent Failures

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
    logger.error("Failed to process episode", episode_guid=episode.guid, error=str(e), exc_info=True)
    raise  # Re-raise for caller to handle
```

## Testing

### Test Organization

Tests live in a top-level `tests/` directory (a sibling of `thestill/`, not inside it), organized by test type and then by package:

```
tests/
├── unit/                      # Per-package unit tests
│   ├── core/
│   ├── web/
│   ├── services/
│   ├── repositories/
│   ├── mcp/
│   ├── models/
│   ├── cli/
│   ├── search/
│   ├── providers/
│   ├── security/
│   ├── utils/
│   └── evals/
├── integration/               # Cross-component tests
│   ├── pipeline/
│   ├── auth/
│   ├── web/
│   ├── follower/
│   ├── cli/
│   └── test_*_contract.py     # Dual-backend repository contract suites (spec #44)
├── e2e/
│   └── web/                   # Browser tests (Node; requires a running server)
├── perf/                      # Latency budget suite (separate CI job)
├── conftest.py                # Shared pytest fixtures
└── fixtures/
```

### Test Naming

```python
def test_download_episode_success():
    """Test successful episode download"""

def test_download_episode_invalid_url_raises_error():
    """Test that invalid URL raises ValueError"""

def test_get_new_episodes_respects_max_limit():
    """Test that max_episodes_per_podcast limit is enforced"""
```

### Test Structure (Arrange-Act-Assert)

```python
def test_mark_episode_downloaded():
    # Arrange
    manager = PodcastFeedManager("./test_data")
    manager.add_podcast("https://example.com/feed.xml")

    # Act
    result = manager.mark_episode_downloaded(
        "https://example.com/feed.xml",
        "episode-123",
        "episode_audio.mp3"
    )

    # Assert
    assert result is True
    episode = manager.get_episode("https://example.com/feed.xml", "episode-123")
    assert episode.audio_path == "episode_audio.mp3"
```

### Coverage Target

- **Minimum coverage**: 70% (aspirational — no `--cov-fail-under` is configured anywhere, so coverage is reported but not enforced)
- **Core modules**: Target 90%+ (feed_manager, audio_downloader, transcriber) — also aspirational
- **Focus on**: Public APIs, error paths, edge cases
- **Skip**: CLI formatting, logging statements, simple getters/setters

Note: `pytest.ini` is the active pytest configuration (markers, addopts, coverage via `.coveragerc`); the `[tool.pytest.ini_options]` block in `pyproject.toml` is shadowed by it and effectively dead.

### Test Types

1. **Unit Tests**: Test individual functions in isolation
2. **Integration Tests**: Test interactions between components
3. **Contract Tests**: Test API boundaries and data models
4. **Property Tests**: Use hypothesis for property-based testing (optional)

## Configuration and Environment

### Environment Variables

- Store ALL secrets in `.env` file (never commit)
- Provide `.env.example` with safe defaults
- Use `python-dotenv` to load environment variables
- Validate required config at startup

### Configuration Hierarchy

1. Environment variables (highest priority)
2. `.env` file
3. Code defaults (lowest priority)

```python
# Good: Centralized config with validation
class Config(BaseModel):
    openai_api_key: str = ""
    storage_path: Path = Path("./data")
    max_workers: int = 3

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required")
```

## Performance and Optimization

### General Rules

- **Measure First**: Use profiling before optimizing
- **Premature Optimization**: Avoid clever tricks for negligible gains
- **Clear Over Clever**: Prefer readable code over micro-optimizations

### I/O Operations

- Use `pathlib.Path` for file operations (not string concatenation)
- Close file handles explicitly or use context managers
- Stream large files instead of loading into memory

```python
# Good: Stream large files
def process_transcript(self, transcript_path: str):
    with open(transcript_path, 'r', encoding='utf-8') as f:
        for line in f:
            process_line(line)

# Bad: Load entire file into memory
def process_transcript(self, transcript_path: str):
    data = open(transcript_path).read()  # No context manager, memory issue
```

### Concurrency

- Use `asyncio` for I/O-bound operations (network, file)
- Use `multiprocessing` for CPU-bound operations (transcription)
- Limit concurrent operations with `max_workers` config

## Security

### Input Validation

- Validate all external inputs (URLs, file paths, user input)
- Use Pydantic models for automatic validation
- Sanitize file paths to prevent directory traversal

```python
def download_episode(self, episode: Episode) -> Optional[str]:
    """Download episode with validated URL"""
    # Pydantic validates episode.audio_url is HttpUrl
    if not episode.audio_url:
        raise ValueError("audio_url is required")

    # Sanitize filename
    safe_filename = self._sanitize_filename(episode.title)
```

### Secrets Management

- NEVER commit API keys, tokens, or credentials
- Use environment variables for all secrets
- Rotate secrets regularly
- Use service accounts with minimal permissions

## Git and PR Hygiene

### Commit Messages

Use conventional commits format:

```
type(scope): subject

[optional body]

[optional footer]
```

**Types**:

- `feat`: New feature
- `fix`: Bug fix
- `refactor`: Code change that neither fixes a bug nor adds a feature
- `test`: Adding or updating tests
- `docs`: Documentation changes
- `chore`: Build, CI, or tooling changes

**Examples**:

```
feat(transcription): add Google Cloud Speech-to-Text support

Add GoogleCloudTranscriber class to support cloud-based transcription
with built-in speaker diarization. Automatically handles large files
via GCS bucket upload.

Closes #42
```

```
refactor(cli): extract episode filtering logic into service layer

Move podcast filtering logic from CLI commands into PodcastService
to improve testability and reduce duplication.
```

### Pull Requests

**Size**: Keep PRs small (< 300 lines changed)
**Structure**: One topic per PR
**Description Template**:

```markdown
## Summary
Brief description of what changed and why

## Changes
- List specific changes
- Include rationale for non-obvious decisions

## Testing
- [ ] Added unit tests for new functionality
- [ ] Ran full test suite locally
- [ ] Tested manually with sample podcast

## Risks
- Note any breaking changes
- Highlight areas needing careful review

## Rollback Plan
- How to revert if issues arise
```

### Branch Naming

```
feature/add-google-transcription
fix/episode-download-retry-logic
refactor/extract-path-manager
chore/update-dependencies
```

## Performance Metrics

Track these metrics to measure code health:

- **Test coverage**: Target 70%+ overall, 90%+ for core modules (aspirational; not CI-enforced)
- **Build time**: Keep under 2 minutes for full test suite
- **Cyclomatic complexity**: Target < 10 per function
- **Duplication**: Track with tools like `radon` or `pylint`
- **Type coverage**: Target 90%+ with mypy

## Dependencies

### Adding Dependencies

1. Justify the need (avoid dependency bloat)
2. Check license compatibility (Apache 2.0)
3. Verify maintenance status (recent commits, active issues)
4. Add to `pyproject.toml` under `dependencies` (or the appropriate
   `[project.optional-dependencies]` group for optional stacks)

### Pinning Versions

- Pin major versions in production: `>=1.0.0,<2.0.0`
- Use exact pins for development: `black==23.0.0`

## Pre-Commit Checklist

Before committing:

- [ ] Code compiles (no syntax errors)
- [ ] All tests pass locally (`pytest`) — CI-gated
- [ ] ruff runs clean (`ruff check thestill/`) — the only CI-gated linter
- [ ] Formatter applied to the files you touched (`black`/`isort` — local-only; the repo is not fully black-clean, so avoid repo-wide formatting)
- [ ] No new pylint or mypy errors in touched code (local-only checks)
- [ ] No public API changes (unless documented)
- [ ] No new dependencies (unless justified)
- [ ] No secrets in code or config files (CI runs a gitleaks scan)

## Review Checklist

When reviewing PRs:

- [ ] Names are clear and follow conventions
- [ ] Functions are small and focused
- [ ] No code duplication
- [ ] Layer boundaries are respected
- [ ] Errors are handled properly
- [ ] No secrets in logs
- [ ] Tests cover the changes
- [ ] Documentation updated (README, CLAUDE.md, docstrings)

## Acceptance Criteria

A change is ready to merge when:

- CI passes: ruff lint, pytest (including the Postgres contract suites), frontend (vitest + build), gitleaks secret scan, Docker build, and the latency budget job
- Local-only checks (black, isort, pylint, mypy) are clean for the files you touched — CI does not gate them
- Code review approved by at least one maintainer
- Documentation updated for user-facing changes
- No merge conflicts with main branch
- Conventional commit message format followed
