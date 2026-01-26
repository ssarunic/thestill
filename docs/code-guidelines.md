# Code Guidelines for Thestill

## Project Context

Thestill is an automated podcast transcription and summarization pipeline built with Python. The project emphasizes atomic operations, clean separation of concerns, and maintainable architecture for processing audio → transcription → cleaning → summarization workflows.

## Language and Toolchain

**Python Version**: 3.9+
**Package Manager**: pip with pyproject.toml (hatchling build system)
**Key Dependencies**: pydantic, click, openai-whisper, whisperx, feedparser, yt-dlp, google-cloud-speech

### Development Tools

```bash
# Formatting
black thestill/
isort thestill/

# Type Checking
mypy thestill/

# Linting
pylint thestill/

# Testing
pytest
```

### Tool Configuration

Create the following configuration files:

**pyproject.toml (add these sections)**:

```toml
[tool.black]
line-length = 120
target-version = ['py39']
include = '\.pyi?$'

[tool.isort]
profile = "black"
line_length = 120
multi_line_output = 3

[tool.mypy]
python_version = "3.9"
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = false
disallow_any_unimported = false
no_implicit_optional = true
warn_redundant_casts = true
warn_unused_ignores = true
warn_no_return = true
check_untyped_defs = true

[tool.pylint]
max-line-length = 120
disable = [
    "missing-docstring",
    "too-few-public-methods",
    "too-many-arguments",
    "too-many-instance-attributes"
]
```

**.pylintrc** (create at root):

```ini
[MASTER]
max-line-length=120
disable=missing-docstring,too-few-public-methods,too-many-arguments,too-many-instance-attributes,duplicate-code
```

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

```
thestill/
├── cli.py                    # Click CLI entry point
├── core/                     # Core processing modules (single responsibility)
│   ├── feed_manager.py       # RSS/YouTube feed parsing
│   ├── audio_downloader.py   # Audio download (atomic: only downloads)
│   ├── audio_preprocessor.py # Audio downsampling (atomic: only downsamples)
│   ├── transcriber.py        # Whisper transcription
│   ├── google_transcriber.py # Google Cloud transcription
│   ├── transcript_cleaning_processor.py  # LLM-based cleaning
│   ├── llm_provider.py       # Abstract LLM interface
│   └── ...
├── models/                   # Pydantic data models
│   └── podcast.py           # Episode, Podcast, Transcript models
├── services/                 # Business logic layer
│   ├── podcast_service.py   # Podcast CRUD operations
│   └── stats_service.py     # Statistics and reporting
├── utils/                    # Shared utilities
│   ├── config.py            # Environment-based configuration
│   ├── logger.py            # Logging setup
│   └── path_manager.py      # Centralized path management
├── mcp/                      # MCP server integration (optional)
└── tests/                    # Test suite
    ├── test_*.py
    └── fixtures/
```

### Layer Separation

1. **CLI Layer** (`cli.py`): User interface, argument parsing, output formatting
2. **Service Layer** (`services/`): Business logic, orchestration, high-level operations
3. **Core Layer** (`core/`): Atomic processors, single-responsibility workers
4. **Model Layer** (`models/`): Data structures, validation, serialization
5. **Infrastructure Layer** (`utils/`): Config, logging, paths, external integrations

**Rules**:

- CLI depends on Services and Core, never the reverse
- Core modules should not depend on CLI
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

- `request_id`: HTTP requests (web layer)
- `command_id`: CLI commands
- `mcp_request_id`: MCP tool invocations
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

```
tests/
├── test_feed_manager.py       # Unit tests for FeedManager
├── test_audio_downloader.py   # Unit tests for AudioDownloader
├── test_cleaning.py           # Integration tests for cleaning
├── conftest.py                # Pytest fixtures
└── fixtures/
    ├── sample_feed.xml
    └── sample_audio.mp3
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

- **Minimum coverage**: 70%
- **Core modules**: Target 90%+ (feed_manager, audio_downloader, transcriber)
- **Focus on**: Public APIs, error paths, edge cases
- **Skip**: CLI formatting, logging statements, simple getters/setters

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

- **Test coverage**: Target 70%+ overall, 90%+ for core modules
- **Build time**: Keep under 2 minutes for full test suite
- **Cyclomatic complexity**: Target < 10 per function
- **Duplication**: Track with tools like `radon` or `pylint`
- **Type coverage**: Target 90%+ with mypy

## Dependencies

### Adding Dependencies

1. Justify the need (avoid dependency bloat)
2. Check license compatibility (Apache 2.0)
3. Verify maintenance status (recent commits, active issues)
4. Add to `pyproject.toml` under `dependencies`
5. Update `requirements.txt` if present

### Pinning Versions

- Pin major versions in production: `>=1.0.0,<2.0.0`
- Use exact pins for development: `black==23.0.0`

## Pre-Commit Checklist

Before committing:

- [ ] Code compiles (no syntax errors)
- [ ] All tests pass locally (`pytest`)
- [ ] Linter runs clean (`pylint thestill/`)
- [ ] Formatter applied (`black thestill/ && isort thestill/`)
- [ ] No new type errors (`mypy thestill/`)
- [ ] No public API changes (unless documented)
- [ ] No new dependencies (unless justified)
- [ ] No secrets in code or config files

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

- All automated checks pass (tests, linting, type checking)
- Code review approved by at least one maintainer
- Documentation updated for user-facing changes
- No merge conflicts with main branch
- Conventional commit message format followed
