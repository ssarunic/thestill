# Refactoring Plan for Thestill

> Generated: 2025-10-11
> Last Updated: 2025-10-13
> Duration: 2-4 weeks (assuming 1-2 hours per day)
> Approach: Small atomic commits, tests green at all times
> **Progress: 7/35 tasks complete (20%)**

## Overview

This plan breaks down refactoring work into ~35 atomic tasks, each taking under 1 hour. Tasks are organized by week and priority. All changes maintain existing behavior (no feature additions).

**✅ Completed: 7 tasks (10.5 hours invested)**
**🚧 In Progress: 1 task (R-006b phases 4-5)**
**⏳ Remaining: 27 tasks**

**Current Status:**
- Test coverage: 18% → Target 70%+ by Week 3
- Repository layer: 75% complete (needs integration)
- Week 1 foundation: Nearly complete (7/8 tasks)

**Guiding Principles**:
1. Keep tests passing after every commit
2. One logical change per commit
3. Refactor → Test → Commit → Repeat
4. No behavior changes
5. Ship small PRs (< 300 lines changed)

---

## Week 1: Foundation & Testing Infrastructure

**Goal**: Establish testing foundation and fix critical issues
**Status**: 7/8 tasks complete (87.5%) ✅ | **Time invested: 10.5 hours**

### Task R-001: Set Up Testing Infrastructure ✅
**Status**: ✅ **COMPLETED** | **Effort**: 1 hour
**Commit**: `c6d5418` - Add pytest testing infrastructure with branch coverage

**Completed**:
- ✅ Added pytest-cov to dev dependencies
- ✅ Created `pytest.ini` with branch coverage settings
- ✅ Established baseline coverage (~18%)
- ✅ 44 tests passing

---

### Task R-002: Add Pre-Commit Hook Configuration ✅
**Status**: ✅ **COMPLETED** | **Effort**: 30 minutes
**Commit**: `6d14fb5` - Add pre-commit hooks for black, isort, and pylint

**Completed**:
- ✅ Created `.pre-commit-config.yaml` with black, isort, pylint
- ✅ Added tool configurations to `pyproject.toml`
- ✅ Pre-commit hooks installed and validated

---

### Task R-003: Add Unit Tests for PathManager ✅
**Status**: ✅ **COMPLETED** | **Effort**: 1 hour
**Commit**: `2ba19b8` - Add comprehensive unit tests with 100% coverage

**Completed**:
- ✅ Created `tests/test_path_manager.py` with 41 test cases
- ✅ **100% code coverage** for PathManager (exceeded 95% target)
- ✅ All directory types and edge cases covered

---

### Task R-004: Replace Bare Except Clauses ✅
**Status**: ✅ **COMPLETED** | **Effort**: 30 minutes
**Commit**: `ae984c9` - Replace bare except clause with specific exception types

**Completed**:
- ✅ Replaced all bare `except:` statements with specific types
- ✅ Added proper error logging
- ✅ All existing tests still passing

---

### Task R-005: Replace Print Statements with Logging ✅
**Status**: ✅ **COMPLETED** | **Effort**: 1 hour
**Commit**: `e2a161d` - Replace print statements with structured logging

**Completed**:
- ✅ Replaced all `print()` statements with `logger` calls
- ✅ Updated progress indicators to use structured logging
- ✅ Improved debugging capability

---

### Task R-006b: Repository Layer Implementation 🚧
**Status**: 🚧 **75% COMPLETE** | **Effort**: 5/6.5 hours
**Commit**: `0137ff4` - Implement repository layer pattern (phases 1-3)

**Why this replaces R-006**: Better architectural approach - abstracts data persistence to enable future SQLite/PostgreSQL migration.

**Completed** ✅:
- ✅ Created repository abstractions (`podcast_repository.py`, 184 lines)
- ✅ Implemented `JsonPodcastRepository` (362 lines, full CRUD)
- ✅ Added 38 comprehensive unit tests (100% pass rate)
- ✅ Refactored FeedManager to use dependency injection

**Pending** ⚠️:
- ⚠️ Update PodcastService constructor (BLOCKS other tasks)
- ⚠️ Update CLI to instantiate repository
- ⚠️ Update StatsService if needed
- ⚠️ Run full integration tests

**Files changed**: 1,192 lines across 5 files

**Next steps**: Complete phases 4-5 to fix integration (1.5 hours estimated)

See [REPOSITORY_LAYER_PLAN.md](REPOSITORY_LAYER_PLAN.md) for details.

---

### Task R-007: Create Custom Exception Classes ✅
**Status**: ✅ **COMPLETED** | **Effort**: 30 minutes
**Commit**: `c5507a3` - Add custom exception class for domain errors

**Completed**:
- ✅ Created `utils/exceptions.py` with `ThestillError` base class
- ✅ Ready for specific subclasses as needed

---

### Task R-008: Add Unit Tests for PodcastService ⏳
**Status**: ⏳ **PENDING** (blocked by R-006b phases 4-5)
**Effort**: 1.5 hours estimated

**Blocked by**: Must complete R-006b integration first

**Planned**:
- Update PodcastService to accept repository parameter
- Create `tests/test_podcast_service.py`
- Add fixtures and mock repository
- Test all methods with 80%+ coverage

---

## Week 2: Service Layer & CLI Refactoring

**Goal**: Extract business logic from CLI, improve separation of concerns

### Task R-009: Add PathManager to PodcastService
**Priority**: High
**Effort**: 30 minutes
**Scope**: `services/podcast_service.py`

**Steps**:
1. Add `path_manager` parameter to `PodcastService.__init__()`
2. Replace direct path construction (lines 320-322) with PathManager calls
3. Update all callers to pass PathManager instance
4. Update tests

**Example**:
```python
# Before
transcript_available=bool(episode.raw_transcript_path and
    (self.storage_path / "raw_transcripts" / episode.raw_transcript_path).exists())

# After
transcript_available=bool(episode.raw_transcript_path and
    self.path_manager.raw_transcript_file(episode.raw_transcript_path).exists())
```

**Safety**: Run tests after change
**Risk**: Low
**Commit**: `refactor(services): use PathManager in PodcastService for all path operations`

---

### Task R-010: Remove Path Attributes from Config
**Priority**: Medium
**Effort**: 1 hour
**Scope**: `utils/config.py`, all consumers

**Steps**:
1. Remove `audio_path`, `downsampled_audio_path`, etc. from Config
2. Keep only `storage_path` and `path_manager`
3. Update all consumers to use `config.path_manager.*_dir()` instead
4. Update `load_config()` to stop setting these attributes
5. Update tests

**Safety**: Grep for all uses of removed attributes, update one by one
**Risk**: Medium (many consumers)
**Commit**: `refactor(config): remove redundant path attributes, use PathManager exclusively`

---

### Task R-011: Extract CLI Formatter Class
**Priority**: Medium
**Effort**: 1 hour
**Scope**: `cli.py`, new `cli_formatter.py`

**Steps**:
1. Create `utils/cli_formatter.py`
2. Extract output formatting methods:
   - `format_podcast_list(podcasts) -> str`
   - `format_episode_list(episodes) -> str`
   - `format_stats(stats) -> str`
   - `format_error(message) -> str`
   - `format_success(message) -> str`
3. Replace inline formatting in cli.py with formatter calls
4. Add tests for formatter

**Example**:
```python
class CLIFormatter:
    @staticmethod
    def format_podcast_list(podcasts: List[PodcastWithIndex]) -> str:
        lines = [f"\n📻 Tracked Podcasts ({len(podcasts)}):"]
        lines.append("─" * 50)
        for podcast in podcasts:
            lines.append(f"{podcast.index}. {podcast.title}")
            lines.append(f"   RSS: {podcast.rss_url}")
            if podcast.last_processed:
                lines.append(f"   Last processed: {podcast.last_processed.strftime('%Y-%m-%d %H:%M')}")
            lines.append(f"   Episodes: {podcast.episodes_processed}/{podcast.episodes_count} processed")
            lines.append("")
        return "\n".join(lines)
```

**Safety**: Compare output before and after
**Risk**: Low
**Commit**: `refactor(cli): extract output formatting into CLIFormatter class`

---

### Task R-012: Use Click Context for Service Injection
**Priority**: Medium
**Effort**: 45 minutes
**Scope**: `cli.py`

**Steps**:
1. In `main()`, instantiate services once and store in `ctx.obj`:
   - `podcast_service`
   - `stats_service`
   - `feed_manager`
2. Update all commands to use `ctx.obj['podcast_service']` instead of creating new instances
3. Remove duplicate service instantiations

**Example**:
```python
@click.group()
@click.pass_context
def main(ctx, config):
    ctx.ensure_object(dict)
    ctx.obj['config'] = load_config(config)
    ctx.obj['podcast_service'] = PodcastService(str(ctx.obj['config'].storage_path))
    ctx.obj['stats_service'] = StatsService(str(ctx.obj['config'].storage_path))
    # ... etc

@main.command()
@click.pass_context
def list(ctx):
    """List all tracked podcasts"""
    podcast_service = ctx.obj['podcast_service']  # Reuse
    podcasts = podcast_service.list_podcasts()
    # ...
```

**Safety**: Test all commands
**Risk**: Low
**Commit**: `refactor(cli): use Click context for service dependency injection`

---

### Task R-013: Create RefreshService
**Priority**: Medium
**Effort**: 1 hour
**Scope**: `services/refresh_service.py` (new), `cli.py`

**Steps**:
1. Create `services/refresh_service.py`
2. Extract refresh logic from `cli.py:131-192` into `RefreshService.refresh()`
3. Move episode limiting and filtering logic into service
4. CLI becomes thin wrapper: parse args → call service → format output
5. Add tests for RefreshService

**Example**:
```python
class RefreshService:
    def __init__(self, feed_manager: PodcastFeedManager, podcast_service: PodcastService):
        self.feed_manager = feed_manager
        self.podcast_service = podcast_service

    def refresh(
        self,
        podcast_id: Optional[Union[str, int]] = None,
        max_episodes: Optional[int] = None,
        dry_run: bool = False
    ) -> RefreshResult:
        """Refresh feeds and discover new episodes"""
        # Business logic here
```

**Safety**: Test CLI still works
**Risk**: Medium
**Commit**: `refactor(services): extract refresh logic into RefreshService`

---

### Task R-014: Add Retry Logic for Downloads
**Priority**: Medium
**Effort**: 45 minutes
**Scope**: `audio_downloader.py`, `pyproject.toml`

**Steps**:
1. Add `tenacity` to dependencies
2. Decorate `download_episode()` with `@retry` decorator
3. Configure exponential backoff (initial=1s, max=60s, max_attempts=3)
4. Add logging for retry attempts
5. Add config for max retries (DOWNLOAD_MAX_RETRIES in .env)

**Example**:
```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=60),
    reraise=True
)
def download_episode(self, episode: Episode, podcast_title: str) -> Optional[str]:
    logger.info(f"Downloading episode: {episode.title}")
    # existing logic
```

**Safety**: Test with failing URLs
**Risk**: Low
**Commit**: `feat(core): add retry logic with exponential backoff for downloads`

---

### Task R-015: Add Progress Bars for Batch Operations
**Priority**: Low
**Effort**: 45 minutes
**Scope**: `cli.py`

**Steps**:
1. Wrap multi-episode loops with `click.progressbar()`
2. Update download, downsample, transcribe commands
3. Show "X of Y" counters
4. Preserve detailed output with `--verbose` flag

**Example**:
```python
with click.progressbar(
    episodes_to_download,
    label="Downloading episodes",
    item_show_func=lambda ep: ep.title if ep else ""
) as bar:
    for podcast, episodes in bar:
        # processing logic
```

**Safety**: Manual testing
**Risk**: Low
**Commit**: `feat(cli): add progress bars for batch operations`

---

### Task R-016: Extract Magic Numbers to Constants
**Priority**: Low
**Effort**: 30 minutes
**Scope**: `audio_downloader.py`, `feed_manager.py`, `cli.py`

**Steps**:
1. Add module-level constants:
   ```python
   # audio_downloader.py
   DEFAULT_TIMEOUT_SECONDS = 30
   DEFAULT_CHUNK_SIZE_BYTES = 8192

   # feed_manager.py
   MIN_PROCESSED_EPISODES_THRESHOLD = 3  # Assume most feeds have >3 episodes

   # cli.py
   DEFAULT_MAX_EPISODES_FOR_CLEANING = 5
   ```
2. Replace all magic numbers with named constants
3. Add docstrings explaining reasoning

**Safety**: Behavior unchanged
**Risk**: Low
**Commit**: `refactor(core): extract magic numbers to named constants with documentation`

---

## Week 3: Testing & Type Coverage

**Goal**: Increase test coverage to 70%+, add type hints

### Task R-017: Add Unit Tests for AudioDownloader
**Priority**: High
**Effort**: 1 hour
**Scope**: `tests/test_audio_downloader.py`

**Steps**:
1. Create `tests/test_audio_downloader.py`
2. Mock `requests.get()` for download tests
3. Test successful download
4. Test failed download (network error, timeout)
5. Test YouTube URL delegation
6. Test filename sanitization
7. Aim for 80%+ coverage

**Safety**: Tests only
**Risk**: Low
**Commit**: `test(core): add unit tests for AudioDownloader`

---

### Task R-018: Add Unit Tests for FeedManager
**Priority**: High
**Effort**: 1.5 hours
**Scope**: `tests/test_feed_manager.py`

**Steps**:
1. Create `tests/test_feed_manager.py`
2. Mock feedparser for RSS tests
3. Test `add_podcast()`, `remove_podcast()`, `list_podcasts()`
4. Test `get_new_episodes()` with max_episodes_per_podcast limit
5. Test episode state transitions (mark_episode_downloaded, etc.)
6. Test YouTube URL handling
7. Aim for 80%+ coverage

**Safety**: Tests only
**Risk**: Low
**Commit**: `test(core): add comprehensive unit tests for FeedManager`

---

### Task R-019: Add Type Hints to Core Modules
**Priority**: Medium
**Effort**: 1.5 hours
**Scope**: `audio_downloader.py`, `feed_manager.py`, `youtube_downloader.py`

**Steps**:
1. Add type hints to all public methods
2. Add type hints to private methods (optional)
3. Run `mypy thestill/core/` and fix errors
4. Add `# type: ignore` comments where necessary with explanations
5. Target 90%+ type coverage for core modules

**Example**:
```python
def download_episode(
    self,
    episode: Episode,
    podcast_title: str
) -> Optional[str]:
    """Download episode audio file"""
```

**Safety**: Run mypy after each file
**Risk**: Low (type hints don't affect runtime)
**Commit**: `refactor(core): add comprehensive type hints to core modules`

---

### Task R-020: Add Integration Tests for Full Pipeline
**Priority**: High
**Effort**: 2 hours
**Scope**: `tests/test_integration_pipeline.py`

**Steps**:
1. Create `tests/test_integration_pipeline.py`
2. Test full workflow: add podcast → refresh → download → downsample → transcribe → clean
3. Use test fixtures with small sample audio files
4. Mock LLM calls to avoid costs
5. Verify file artifacts at each stage
6. Test error recovery (e.g., resume after failed download)

**Safety**: Isolated test environment
**Risk**: Medium (integration tests can be flaky)
**Commit**: `test(integration): add end-to-end pipeline integration tests`

---

### Task R-021: Add Type Hints to Service Layer
**Priority**: Medium
**Effort**: 1 hour
**Scope**: `services/podcast_service.py`, `services/stats_service.py`

**Steps**:
1. Add type hints to all public methods
2. Run `mypy thestill/services/` and fix errors
3. Ensure consistent type usage with core modules

**Safety**: Run mypy
**Risk**: Low
**Commit**: `refactor(services): add comprehensive type hints to service layer`

---

### Task R-022: Create EpisodeState Enum
**Priority**: Low
**Effort**: 1 hour
**Scope**: `models/podcast.py`, `core/feed_manager.py`

**Steps**:
1. Add `EpisodeState` enum to `models/podcast.py`:
   ```python
   class EpisodeState(str, Enum):
       DISCOVERED = "discovered"
       DOWNLOADED = "downloaded"
       DOWNSAMPLED = "downsampled"
       TRANSCRIBED = "transcribed"
       CLEANED = "cleaned"
   ```
2. Add `state` property to Episode model
3. Add validation for state transitions in FeedManager
4. Update tests

**Example**:
```python
@property
def state(self) -> EpisodeState:
    """Compute current episode state from file paths"""
    if self.clean_transcript_path:
        return EpisodeState.CLEANED
    if self.raw_transcript_path:
        return EpisodeState.TRANSCRIBED
    if self.downsampled_audio_path:
        return EpisodeState.DOWNSAMPLED
    if self.audio_path:
        return EpisodeState.DOWNLOADED
    return EpisodeState.DISCOVERED
```

**Safety**: Add tests for state transitions
**Risk**: Low
**Commit**: `feat(models): add EpisodeState enum and state property to Episode`

---

### Task R-023: Add Contract Tests for Service Boundaries
**Priority**: Medium
**Effort**: 1 hour
**Scope**: `tests/test_contracts.py`

**Steps**:
1. Create `tests/test_contracts.py`
2. Test contracts between:
   - CLI → Services (input validation, output format)
   - Services → Core (method signatures, return types)
   - Core → Models (data validation)
3. Use property-based testing (hypothesis) for edge cases (optional)

**Example**:
```python
def test_podcast_service_get_podcast_contract():
    """Test that get_podcast accepts both int and str IDs"""
    service = PodcastService("./test_data")

    # Should accept int
    podcast = service.get_podcast(1)
    assert podcast is None or isinstance(podcast, Podcast)

    # Should accept string
    podcast = service.get_podcast("https://example.com/feed.xml")
    assert podcast is None or isinstance(podcast, Podcast)

    # Should handle invalid ID gracefully
    podcast = service.get_podcast(-1)
    assert podcast is None
```

**Safety**: Tests only
**Risk**: Low
**Commit**: `test(contracts): add contract tests for service boundaries`

---

## Week 4: Polish & Documentation

**Goal**: Final cleanup, documentation, and validation

### Task R-024: Complete cleanup_old_files Implementation
**Priority**: Low
**Effort**: 30 minutes
**Scope**: `audio_downloader.py`

**Steps**:
1. Complete `cleanup_old_files()` implementation
2. Add logic to find files older than `days` parameter
3. Add dry-run mode
4. Add logging for deleted files
5. Add test

**Example**:
```python
def cleanup_old_files(self, days: int = 30, dry_run: bool = False):
    """Remove audio files older than specified days"""
    import time
    cutoff_time = time.time() - (days * 86400)

    for file_path in self.storage_path.glob("*"):
        if file_path.is_file() and file_path.stat().st_mtime < cutoff_time:
            if dry_run:
                logger.info(f"Would delete: {file_path.name}")
            else:
                file_path.unlink()
                logger.info(f"Deleted old file: {file_path.name}")
```

**Safety**: Test with dry_run first
**Risk**: Medium (file deletion)
**Commit**: `feat(core): complete implementation of cleanup_old_files with dry-run support`

---

### Task R-025: Audit Transcript Cleaning Pipeline Performance
**Priority**: Medium
**Effort**: 1 hour
**Scope**: `transcript_cleaning_processor.py`

**Steps**:
1. Add timing metrics for each phase
2. Log phase durations
3. Benchmark: Can phases be combined?
4. Document findings in code comments
5. Add config flag to skip Phase 2 if speakers already known

**Safety**: Observation only, no changes yet
**Risk**: Low
**Commit**: `perf(core): add timing metrics to transcript cleaning pipeline phases`

---

### Task R-026: Add LLM Provider Display Name Method
**Priority**: Low
**Effort**: 30 minutes
**Scope**: `core/llm_provider.py`, `cli.py`

**Steps**:
1. Add `get_model_display_name()` to LLMProvider abstract class
2. Implement in all providers (OpenAI, Ollama, Gemini, Anthropic)
3. Replace provider-specific logic in cli.py status command
4. Test status command with each provider

**Example**:
```python
class LLMProvider(ABC):
    @abstractmethod
    def get_model_display_name(self) -> str:
        """Get human-readable model name for display"""

class OpenAIProvider(LLMProvider):
    def get_model_display_name(self) -> str:
        return f"OpenAI {self.model}"

# cli.py
click.echo(f"  LLM model: {llm_provider.get_model_display_name()}")
```

**Safety**: Test status command
**Risk**: Low
**Commit**: `refactor(llm): add get_model_display_name to LLM provider abstraction`

---

### Task R-027: Add PathManager require_file_exists Helper
**Priority**: Low
**Effort**: 30 minutes
**Scope**: `utils/path_manager.py`

**Steps**:
1. Add `require_file_exists(file_path, error_msg)` method to PathManager
2. Replace repeated existence checks in CLI and services
3. Centralize error messages
4. Add test

**Example**:
```python
def require_file_exists(self, file_path: Path, error_message: str) -> Path:
    """
    Check if file exists and raise FileNotFoundError if not.

    Args:
        file_path: Path to check
        error_message: Custom error message

    Returns:
        The same path if it exists

    Raises:
        FileNotFoundError: If file does not exist
    """
    if not file_path.exists():
        raise FileNotFoundError(f"{error_message}: {file_path}")
    return file_path
```

**Safety**: Add tests
**Risk**: Low
**Commit**: `feat(utils): add require_file_exists helper to PathManager`

---

### Task R-028: Add FeedManager Transaction Context Manager
**Priority**: Medium
**Effort**: 45 minutes
**Scope**: `core/feed_manager.py`

**Steps**:
1. Add `@contextmanager` for batch updates:
   ```python
   @contextmanager
   def transaction(self):
       """Context manager for batch updates with auto-save"""
       try:
           yield self
       finally:
           self._save_podcasts()
   ```
2. Update code that does multiple mutations to use transaction
3. Document when to use transaction vs single mutation
4. Add test

**Example Usage**:
```python
with feed_manager.transaction():
    feed_manager.mark_episode_downloaded(...)
    feed_manager.mark_episode_downsampled(...)
    feed_manager.mark_episode_processed(...)
# Auto-saves once at end
```

**Safety**: Test rollback behavior
**Risk**: Medium
**Commit**: `feat(core): add transaction context manager to FeedManager for batch updates`

---

### Task R-029: Create YouTube Source Strategy Pattern
**Priority**: Low
**Effort**: 1 hour
**Scope**: `core/media_source.py` (new), `core/feed_manager.py`, `core/audio_downloader.py`

**Steps**:
1. Create abstract `MediaSource` interface
2. Implement `RSSMediaSource` and `YouTubeMediaSource`
3. Add `MediaSourceDetector` to identify source type
4. Refactor FeedManager and AudioDownloader to use strategy pattern
5. Add tests

**Example**:
```python
class MediaSource(ABC):
    @abstractmethod
    def is_valid_url(self, url: str) -> bool:
        """Check if URL matches this source"""

    @abstractmethod
    def fetch_episodes(self, url: str) -> List[Episode]:
        """Fetch episodes from this source"""

class YouTubeMediaSource(MediaSource):
    def is_valid_url(self, url: str) -> bool:
        return "youtube.com" in url or "youtu.be" in url

    def fetch_episodes(self, url: str) -> List[Episode]:
        # YouTube-specific logic
```

**Safety**: Add tests for each source
**Risk**: Medium (significant refactor)
**Commit**: `refactor(core): introduce MediaSource strategy pattern for RSS and YouTube`

---

### Task R-030: Update CLAUDE.md with Refactoring Notes
**Priority**: Low
**Effort**: 30 minutes
**Scope**: `CLAUDE.md`

**Steps**:
1. Add section on testing strategy
2. Document type coverage expectations
3. Update architecture section with service layer details
4. Add notes on error handling patterns
5. Link to CODE_GUIDELINES.md

**Safety**: Documentation only
**Risk**: None
**Commit**: `docs(claude): update CLAUDE.md with refactoring notes and testing strategy`

---

### Task R-031: Add Makefile for Common Commands
**Priority**: Low
**Effort**: 20 minutes
**Scope**: `Makefile` (new)

**Steps**:
1. Create `Makefile` with common commands:
   - `make test` - Run tests with coverage
   - `make lint` - Run black, isort, pylint, mypy
   - `make format` - Run black and isort
   - `make install` - Install in dev mode
   - `make clean` - Remove generated files
2. Update README with Makefile usage

**Safety**: Just convenience, doesn't change code
**Risk**: None
**Commit**: `chore(tooling): add Makefile for common development commands`

---

### Task R-032: Add GitHub Actions CI Workflow
**Priority**: Medium
**Effort**: 45 minutes
**Scope**: `.github/workflows/ci.yml` (new)

**Steps**:
1. Create GitHub Actions workflow for CI
2. Run tests on push and PR
3. Run linters (black, isort, pylint, mypy)
4. Check test coverage and fail if < 70%
5. Matrix test on Python 3.9, 3.10, 3.11, 3.12
6. Add status badge to README

**Safety**: CI only, doesn't affect code
**Risk**: Low
**Commit**: `ci(github): add GitHub Actions workflow for tests and linting`

---

### Task R-033: Simplify CLI Import Pattern
**Priority**: Low
**Effort**: 30 minutes
**Scope**: `cli.py`

**Steps**:
1. Remove try/except import block (lines 19-42)
2. Use consistent relative imports
3. Add `if __name__ == '__main__'` guard at bottom for standalone execution
4. Document when standalone execution is needed vs package execution
5. Test both `python -m thestill.cli` and `thestill` entry point

**Safety**: Test both execution modes
**Risk**: Medium (could break entry point)
**Commit**: `refactor(cli): simplify import pattern and document execution modes`

---

### Task R-034: Add Episode GUID Uniqueness Validation
**Priority**: Low
**Effort**: 30 minutes
**Scope**: `models/podcast.py`, `core/feed_manager.py`

**Steps**:
1. Add Pydantic validator to Podcast model:
   ```python
   @model_validator(mode='after')
   def validate_unique_guids(self) -> 'Podcast':
       guids = [ep.guid for ep in self.episodes]
       if len(guids) != len(set(guids)):
           duplicates = [g for g in guids if guids.count(g) > 1]
           logger.warning(f"Duplicate GUIDs found: {duplicates}")
       return self
   ```
2. Add test for duplicate GUID handling
3. Log warning but don't fail (some feeds may have bad data)

**Safety**: Add test first
**Risk**: Low
**Commit**: `feat(models): add validation for unique episode GUIDs in Podcast`

---

### Task R-035: Review and Update README
**Priority**: Low
**Effort**: 30 minutes
**Scope**: `README.md`

**Steps**:
1. Update installation instructions
2. Add section on running tests
3. Add section on code quality tools
4. Link to CODE_GUIDELINES.md
5. Add contributing section
6. Update examples with latest CLI usage

**Safety**: Documentation only
**Risk**: None
**Commit**: `docs(readme): update README with testing, linting, and contributing guidelines`

---

## Pull Request Strategy

### PR Batching

Group tasks into small PRs by theme:

**PR #1: Testing Infrastructure** (Week 1)
- Tasks: R-001, R-002, R-003, R-008
- Lines changed: ~200
- Risk: Low

**PR #2: Error Handling & Logging** (Week 1)
- Tasks: R-004, R-005, R-007
- Lines changed: ~150
- Risk: Medium

**PR #3: CLI Refactoring - Part 1** (Week 1-2)
- Tasks: R-006, R-011, R-012
- Lines changed: ~250
- Risk: Medium

**PR #4: Service Layer Extraction** (Week 2)
- Tasks: R-013, R-009, R-010
- Lines changed: ~300
- Risk: High

**PR #5: Resilience & UX** (Week 2)
- Tasks: R-014, R-015, R-016
- Lines changed: ~200
- Risk: Low

**PR #6: Test Coverage - Core Modules** (Week 3)
- Tasks: R-017, R-018, R-020
- Lines changed: ~400 (tests)
- Risk: Low

**PR #7: Type Hints** (Week 3)
- Tasks: R-019, R-021
- Lines changed: ~150
- Risk: Low

**PR #8: Models & Contracts** (Week 3)
- Tasks: R-022, R-023, R-034
- Lines changed: ~200
- Risk: Medium

**PR #9: Utilities & Helpers** (Week 4)
- Tasks: R-024, R-026, R-027, R-028
- Lines changed: ~200
- Risk: Low

**PR #10: Architecture - Media Sources** (Week 4)
- Tasks: R-029
- Lines changed: ~300
- Risk: High

**PR #11: CI/CD & Tooling** (Week 4)
- Tasks: R-025, R-031, R-032, R-033
- Lines changed: ~150
- Risk: Low

**PR #12: Documentation** (Week 4)
- Tasks: R-030, R-035
- Lines changed: ~100
- Risk: None

---

## Sample Commit Messages

### Good Examples

```
feat(core): add retry logic with exponential backoff for downloads

Add tenacity library for robust download retry behavior. Downloads now
retry up to 3 times with exponential backoff (1s, 2s, 4s) on network
failures. Configurable via DOWNLOAD_MAX_RETRIES in .env.

Closes #42
```

```
refactor(cli): extract duplicate podcast filtering logic into helper

Move podcast_id filtering from 4 separate command functions into
_filter_by_podcast() helper. Reduces duplication from 60 lines to 15.
No behavior change.
```

```
test(services): add comprehensive unit tests for PodcastService

Add 12 test cases covering:
- add/remove/list podcasts
- get_podcast with int and string IDs
- get_episode with various ID formats
- list_episodes with filtering

Coverage: 85% for PodcastService
```

```
refactor(config): remove redundant path attributes, use PathManager exclusively

Remove audio_path, downsampled_audio_path, raw_transcripts_path, etc.
from Config class. All path operations now go through config.path_manager.
This establishes PathManager as single source of truth for file paths.

Breaking change: Callers must update from config.audio_path to
config.path_manager.original_audio_dir()
```

---

## Risk Mitigation

### High-Risk Tasks

**R-010: Remove Path Attributes from Config**
- **Risk**: Many consumers could break
- **Mitigation**:
  1. Grep for all uses of removed attributes
  2. Update and test each consumer one by one
  3. Run full test suite after each change

**R-013: Create RefreshService**
- **Risk**: Complex extraction, many edge cases
- **Mitigation**:
  1. Write tests for RefreshService first (TDD)
  2. Extract logic incrementally, not all at once
  3. Keep old CLI logic commented out until service is proven

**R-029: Media Source Strategy Pattern**
- **Risk**: Large architectural change
- **Mitigation**:
  1. Introduce interfaces without changing behavior
  2. Refactor one source type at a time (RSS first, then YouTube)
  3. Keep old code until new code is fully tested

---

## Success Metrics

### Week 1 Targets
- Test coverage: 30%+ (baseline + PathManager + PodcastService tests)
- Lint errors: Reduced by 50%
- Duplicate code: Reduced by 20%

### Week 2 Targets
- Test coverage: 50%+
- CLI complexity: Reduced by 30% (lines of code in cli.py)
- Service layer: 3+ new service classes

### Week 3 Targets
- Test coverage: 70%+
- Type coverage: 80%+
- Integration tests: 5+ scenarios

### Week 4 Targets
- Test coverage: 75%+
- Type coverage: 90%+
- All PRs merged
- Documentation complete
- CI/CD pipeline green

---

## Rollback Plan

If a refactor causes issues:

1. **Immediate**: Revert the commit/PR
2. **Investigate**: Understand what broke and why
3. **Fix Forward**: If fix is small (< 30 min), fix and re-commit
4. **Redesign**: If fix is large, mark task as blocked and redesign approach
5. **Document**: Add notes to this plan about what went wrong

---

---

## Progress Summary (Updated 2025-10-13)

### Completed Tasks ✅ (7/35 = 20%)

| Task | Commit | Time | Status |
|------|--------|------|--------|
| R-001 | `c6d5418` | 1h | ✅ Testing infrastructure |
| R-002 | `6d14fb5` | 30m | ✅ Pre-commit hooks |
| R-003 | `2ba19b8` | 1h | ✅ PathManager tests (100% coverage) |
| R-004 | `ae984c9` | 30m | ✅ Replace bare except clauses |
| R-005 | `e2a161d` | 1h | ✅ Replace print with logging |
| R-007 | `c5507a3` | 30m | ✅ Custom exception classes |
| R-006b (partial) | `0137ff4` | 5h | 🚧 Repository layer (75% done) |

**Total time invested: 10.5 hours**

### Next 5 Priority Tasks

1. **R-006b (phases 4-5)** - Complete repository integration (1.5h) 🔥 CRITICAL
2. **R-008** - Unit tests for PodcastService (1.5h)
3. **R-009** - Add PathManager to PodcastService (30m)
4. **R-018** - Unit tests for FeedManager (1.5h)
5. **R-017** - Unit tests for AudioDownloader (1h)

**Estimated effort for next 5: 6 hours**

### Key Metrics

- **Test coverage**: 18% → Target 70%+ by Week 3
- **Tests passing**: 44/44 (100%)
- **Repository layer**: 75% complete (1,192 lines changed)
- **Commits this week**: 7 atomic commits
- **Files changed**: 12 files (5 new, 7 modified)

### Critical Path Forward

**Week 1 completion (1.5 hours remaining)**:
- Fix R-006b integration (PodcastService + CLI)
- Run full integration tests
- Validate all 44 tests still pass

**Week 2 focus**:
- Service layer testing (R-008, R-018, R-017)
- CLI refactoring (R-009, R-010, R-011, R-012)
- Target: 50% test coverage by end of week

---

## Notes

- All tasks assume existing tests pass before starting
- Run tests after EVERY commit (use pre-commit hook)
- If a task takes > 1 hour, split it into smaller tasks
- Prefer many small commits over large commits
- Keep main branch green at all times
- Use feature branches for risky changes

---

## Appendix: Quick Reference

### Pre-Commit Checklist
```bash
# Before every commit:
black thestill/
isort thestill/
pylint thestill/
mypy thestill/
pytest
```

### Testing Commands
```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=thestill --cov-report=html

# Run specific test file
pytest tests/test_path_manager.py

# Run specific test
pytest tests/test_path_manager.py::test_original_audio_dir
```

### Useful Git Commands
```bash
# Create feature branch
git checkout -b refactor/task-r-001

# Interactive rebase (clean up commits)
git rebase -i HEAD~3

# Amend last commit
git commit --amend --no-edit
```
