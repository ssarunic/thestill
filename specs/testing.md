# Testing

This document describes the testing strategy, coverage targets, and type checking standards for thestill.

## Coverage Targets

- **Overall**: 70%+
- **Core modules**: 90%+ (feed_manager, audio_downloader, transcriber)
- **Models**: 100% with branch coverage
- **Focus areas**: Public APIs, error paths, edge cases

## Test Types

### Unit Tests

Test individual functions in isolation with mocked dependencies:

- Example: `test_transcript_parser.py` (47 tests)
- Pattern: Mock external dependencies (requests, feedparser)
- Fast execution, isolated failures

### Feature Tests

Test complete feature modules:

- Example: `test_external_transcript_downloader.py` (19 tests)
- Pattern: Use real PathManager, mock external APIs only

### Integration Tests

Test interactions between components:

- Full pipeline scenarios (9 end-to-end tests)
- Service boundary validation

### Contract Tests

Test API boundaries and data models:

- 32 tests prevent API breakage
- Critical for refactoring service boundaries

## Test Organization

```
tests/
├── test_transcript_parser.py              # Transcript parsing (47 tests)
└── test_external_transcript_downloader.py # External transcript downloads (19 tests)
```

## Running Tests

```bash
# Run all tests with coverage
pytest --cov=thestill --cov-report=html

# Run specific test file
pytest tests/test_transcript_parser.py -v

# Run tests matching pattern
pytest -k "test_download" -v
```

## Test Naming

```python
def test_download_episode_success():
    """Test successful episode download"""

def test_download_episode_invalid_url_raises_error():
    """Test that invalid URL raises ValueError"""

def test_get_new_episodes_respects_max_limit():
    """Test that max_episodes_per_podcast limit is enforced"""
```

## Test Structure (Arrange-Act-Assert)

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

## Test Fixtures and Mocking

- Use `@pytest.fixture` for reusable test data
- Mock external APIs (requests, feedparser, LLM providers)
- Use `tmp_path` fixture for file system tests
- Never mock the code under test (only dependencies)

## Testing Best Practices

- Mock external dependencies (requests, feedparser, LLM APIs)
- Use `tmp_path` for file system tests
- Arrange-Act-Assert pattern
- Descriptive test names (`test_download_retries_on_network_error`)

## Type Coverage

### Type Checking

This project uses `mypy` for static type analysis.

**Current Status**: 100% core and service layers type-hinted

### Type Hint Standards

```python
from typing import List, Optional, Dict, Any, Tuple

# Always type-hint function signatures
def download_episode(
    self,
    episode: Episode,
    podcast_title: str
) -> Optional[str]:
    """Download audio file for episode"""
    pass

# Type-hint class attributes
class PodcastService:
    def __init__(
        self,
        repository: PodcastRepository,
        path_manager: PathManager
    ) -> None:
        self.repository: PodcastRepository = repository
        self.path_manager: PathManager = path_manager

# Use Pydantic models for complex data structures
class Episode(BaseModel):
    guid: str
    title: str
    audio_url: Optional[HttpUrl] = None
    audio_path: Optional[str] = None
```

### Running Type Checks

```bash
# Check all files
mypy thestill/

# Check specific module
mypy thestill/core/feed_manager.py
```

### Type Configuration (pyproject.toml)

```toml
[tool.mypy]
python_version = "3.9"
warn_return_any = true
warn_unused_configs = true
check_untyped_defs = true
```

**Type Hints Coverage Target**: 90%+ (measured by mypy)
