# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

thestill.me is an automated podcast transcription and summarization pipeline built with Python. It converts audio podcasts into readable, summarized content using either local Whisper transcription or cloud-based Google Speech-to-Text, and supports multiple LLM providers (OpenAI GPT-4, Ollama, Google Gemini, Anthropic Claude) for analysis.

## Development Commands

### Virtual Environment

**IMPORTANT**: This project uses a virtual environment. Always use `./venv/bin/python` or `./venv/bin/pytest` instead of bare `python` or `pytest` commands.

```bash
# Run Python
./venv/bin/python script.py

# Run pytest
./venv/bin/python -m pytest tests/

# Run the CLI
./venv/bin/thestill <command>
```

### Installation and Setup

```bash
# Install in development mode
./venv/bin/pip install -e .

# Install with development dependencies
./venv/bin/pip install -e ".[dev]"

# Set up environment
cp .env.example .env
# Edit .env with API key (OpenAI, Gemini, or use Ollama locally)
```

### Main CLI Commands

```bash
# Add podcast feed (supports RSS, Apple Podcasts, YouTube)
thestill add "https://example.com/rss"
thestill add "https://podcasts.apple.com/us/podcast/id123456"
thestill add "https://www.youtube.com/@channelname"
thestill add "https://www.youtube.com/playlist?list=..."

# Refresh feeds and discover new episodes (step 1)
thestill refresh                           # Refresh all podcast feeds
thestill refresh --podcast-id 1            # Refresh specific podcast (by index)
thestill refresh --podcast-id "https://..." # Refresh specific podcast (by URL)
thestill refresh --max-episodes 3          # Limit episodes to discover per podcast (overrides MAX_EPISODES_PER_PODCAST)
thestill refresh --dry-run                 # Preview what would be discovered

# Download audio files for discovered episodes (step 2)
thestill download                          # Download from all podcasts
thestill download --podcast-id 1           # Download from specific podcast (by index)
thestill download --podcast-id "https://..." # Download from specific podcast (by URL)
thestill download --max-episodes 3         # Limit downloads per podcast
thestill download --dry-run                # Preview what would be downloaded

# Downsample audio to 16kHz WAV (step 3)
thestill downsample                        # Downsample all downloaded audio
thestill downsample --podcast-id 1         # Downsample specific podcast
thestill downsample --max-episodes 3       # Limit downsampling
thestill downsample --dry-run              # Preview what would be downsampled

# Transcribe downsampled audio to JSON (step 4)
thestill transcribe                        # Transcribe all downsampled audio
thestill transcribe --podcast-id 1         # Transcribe from specific podcast
thestill transcribe --podcast-id 1 --episode-id latest  # Transcribe specific episode
thestill transcribe --max-episodes 3       # Limit transcriptions
thestill transcribe --dry-run              # Preview what would be transcribed

# Clean existing transcripts with LLM (step 5)
thestill clean-transcript                  # Clean all transcripts needing cleaning
thestill clean-transcript --dry-run        # Preview what would be cleaned
thestill clean-transcript --max-episodes 5 # Limit episodes to clean
thestill clean-transcript --force          # Re-clean even if clean transcript exists
thestill clean-transcript --stream         # Stream LLM output in real-time

# Summarize cleaned transcripts (step 6)
thestill summarize                         # Summarize next cleaned transcript(s)
thestill summarize path/to/transcript.md   # Summarize specific file
thestill summarize --dry-run               # Preview what would be summarized
thestill summarize --max-episodes 3        # Limit summaries
thestill summarize --force                 # Re-summarize even if summary exists

# Manage podcast/episode facts for transcript cleaning
thestill facts list                        # List all facts files
thestill facts show <podcast-id>           # Show facts for a podcast
thestill facts edit <podcast-id>           # Open facts file in $EDITOR
thestill facts extract <episode-id>        # Extract facts from transcript

# Evaluate transcript quality
thestill evaluate-transcript               # Evaluate raw transcript quality
thestill evaluate-postprocess              # Evaluate post-processed transcript

# Manage podcasts
thestill list                              # List tracked podcasts
thestill remove <podcast-id>               # Remove podcast by URL or index
thestill status                            # Show system status and statistics
thestill cleanup                           # Clean up old audio files

# Start web server (for webhooks and future API/UI)
thestill server                            # Start on localhost:8000
thestill server --port 8080                # Custom port
thestill server --host 0.0.0.0             # Bind to all interfaces (for external access)
thestill server --reload                   # Auto-reload for development
thestill server --workers 4                # Multiple worker processes
```

### Testing and Code Quality

**Quick Commands (using Makefile):**

```bash
# Show all available commands
make help

# Install with dev dependencies
make install-dev

# Run tests with coverage
make test

# Run tests without coverage (faster)
make test-fast

# Format code (black + isort)
make format

# Run linters (pylint + mypy)
make lint

# Run type checking only
make typecheck

# Run ALL checks before committing
make check

# Clean generated files
make clean
```

**Direct Commands:**

```bash
# Run tests
pytest

# Format code
black thestill/
isort thestill/

# Type checking
mypy thestill/
```

## Architecture

### Core Components

1. **Media Source Strategy Pattern** (`thestill/core/media_source.py`) **NEW**
   - Abstracts different podcast sources (RSS, YouTube, etc.)
   - **MediaSource ABC**: Defines interface for URL validation, episode fetching, downloading
   - **RSSMediaSource**: Handles RSS feeds and Apple Podcasts URL resolution
   - **YouTubeMediaSource**: Wraps YouTube downloader for playlists/channels
   - **MediaSourceFactory**: Auto-detects source type from URL
   - **Benefits**: Clean separation, easy extensibility (Spotify, SoundCloud), better testability

2. **Feed Manager** (`thestill/core/feed_manager.py`)
   - Coordinates podcast feed management across all sources
   - Uses MediaSourceFactory for source-specific operations
   - Stores podcast metadata and processing status
   - Identifies new episodes since last run
   - Transaction context manager for batch updates

3. **Audio Downloader** (`thestill/core/audio_downloader.py`)
   - Downloads podcast audio files from all sources to `data/original_audio/`
   - Uses MediaSourceFactory to delegate source-specific downloads
   - Handles various audio formats (MP3, M4A, etc.)
   - Manages file cleanup and storage
   - Retry logic with exponential backoff for network errors
   - **Atomic operation**: Only downloads, does not process audio

4. **Audio Preprocessor** (`thestill/core/audio_preprocessor.py`)
   - Downsamples audio to 16kHz, 16-bit, mono WAV format
   - Saves downsampled WAV files to `data/downsampled_audio/`
   - **Important**: Solves pyannote.audio M4A/MP3 compatibility issues
   - No audio enhancement (preserves original quality)
   - **Atomic operation**: Only downsamples, does not download or transcribe

5. **YouTube Downloader** (`thestill/core/youtube_downloader.py`)
   - Uses yt-dlp for robust YouTube video/audio extraction
   - Handles dynamic URLs and format selection
   - Extracts playlist and channel metadata
   - Downloads best quality audio and converts to M4A
   - **Note**: Now wrapped by YouTubeMediaSource for clean abstraction

6. **Transcriber** (`thestill/core/transcriber.py` and `thestill/core/google_transcriber.py`)
   - **WhisperTranscriber**: Standard OpenAI Whisper for local speech-to-text
   - **WhisperXTranscriber**: Enhanced local transcription with speaker diarization
   - **GoogleCloudTranscriber**: Cloud-based Google Speech-to-Text API (NEW)
     - Built-in speaker diarization (no pyannote.audio setup needed)
     - Automatic file size handling (sync <10MB, async GCS for larger files)
     - Language detection from podcast RSS metadata
     - Cloud-based: No GPU/CPU requirements, pay-per-use pricing
   - Supports word-level timestamps and speaker identification
   - Configurable via `TRANSCRIPTION_PROVIDER` (whisper or google)
   - Automatic fallback to standard Whisper if diarization fails (WhisperX only)

7. **Facts System** (`thestill/core/facts_extractor.py`, `thestill/core/facts_manager.py`)
   - **Two-level facts architecture** for transcript cleaning:
     - **PodcastFacts**: Recurring knowledge (hosts, sponsors, keywords) - stable, user-editable
     - **EpisodeFacts**: Episode-specific knowledge (guests, speaker mapping, topics)
   - **FactsExtractor**: Pass 1 of cleaning - analyzes transcript to extract speaker mapping and facts
   - **FactsManager**: Loads/saves facts as human-editable Markdown files
   - Facts stored in `data/podcast_facts/` and `data/episode_facts/`

8. **Transcript Cleaner** (`thestill/core/transcript_cleaner.py`)
   - **Pass 2 of two-pass cleaning pipeline**:
     - Stage 2a: Deterministic speaker name substitution (no LLM)
     - Stage 2b: LLM cleans spelling, grammar, detects ads, formats output
   - Uses pre-formatted markdown (from TranscriptFormatter) to reduce token usage
   - Supports streaming output for real-time feedback

9. **Post Processor / Summarizer** (`thestill/core/post_processor.py`)
   - Produces comprehensive analysis of cleaned transcripts:
     - Executive summary
     - Notable quotes
     - Content angles
     - Social snippets
     - Resource check
     - Critical analysis
   - Model-aware rate limits and context window management
   - Supports multiple LLM providers (OpenAI, Anthropic, Google, Ollama)

10. **Evaluator** (`thestill/core/evaluator.py`)
    - Quality evaluation for raw and post-processed transcripts
    - Scoring and feedback for transcript accuracy

11. **Transcript Formatter** (`thestill/core/transcript_formatter.py`)
    - Converts raw JSON transcripts to markdown format
    - Prepares transcripts for LLM processing

12. **Transcript Cleaning Processor** (`thestill/core/transcript_cleaning_processor.py`) (Legacy)
    - Original three-phase LLM cleaning pipeline
    - Being replaced by the two-pass facts-based approach

13. **Models** (`thestill/models/podcast.py`, `thestill/models/facts.py`)
    - Pydantic models for type safety
    - Episode, Podcast, Quote, ProcessedContent, CleanedTranscript, PodcastFacts, EpisodeFacts schemas

14. **Path Manager** (`thestill/utils/path_manager.py`)
    - **Centralized path management** for all file artifacts
    - Single source of truth for directory and file paths
    - Prevents scattered path logic across codebase
    - Methods for all artifact types (audio, transcripts, summaries, etc.)
    - Integrated into Config and FeedManager
    - **Reduces errors** when directory structures change

### MCP Server (`thestill/mcp/`)

The project includes an MCP (Model Context Protocol) server for integration with AI assistants:

- **server.py**: MCP server setup and configuration
- **tools.py**: Action handlers for podcast management operations
  - `add_podcast`: Add new podcast feeds
  - `refresh_feeds`: Refresh all/specific podcast feeds
  - `download_audio`: Download audio for episodes
  - `downsample_audio`: Downsample audio files
  - `transcribe_audio`: Transcribe episodes
  - `list_podcasts`: List tracked podcasts
  - `get_podcast_status`: Get status information
- **resources.py**: MCP resources for exposing data
- **utils.py**: MCP utility functions

### Web Server (`thestill/web/`)

FastAPI-based web server for webhooks, REST API, and future web UI:

```
thestill/web/
├── __init__.py              # Package init with create_app export
├── app.py                   # FastAPI application factory
├── dependencies.py          # Dependency injection (AppState, get_app_state)
└── routes/
    ├── __init__.py
    ├── health.py            # Health check and status endpoints
    └── webhooks.py          # ElevenLabs webhook handlers
```

**Key Components:**

- **app.py**: Application factory with lifespan management
  - Initializes services once at startup (same pattern as CLI)
  - Stores `AppState` in `app.state` for route access
  - Registers route modules

- **dependencies.py**: FastAPI dependency injection
  - `AppState`: Dataclass mirroring `CLIContext` from CLI
  - `get_app_state()`: Dependency function for routes

- **routes/health.py**: Health and status endpoints
  - `GET /` - Service identification
  - `GET /health` - Health check for load balancers
  - `GET /status` - Detailed system stats (like CLI `status` command)

- **routes/webhooks.py**: ElevenLabs webhook handlers
  - `POST /webhook/elevenlabs/speech-to-text` - Receive transcription callbacks
  - `GET /webhook/elevenlabs/results` - List received webhooks
  - `GET /webhook/elevenlabs/results/{id}` - Get specific result
  - `DELETE /webhook/elevenlabs/results/{id}` - Delete result

**Architecture:**

```
CLI (cli.py)                    Web (web/app.py)
     |                               |
     v                               v
  CLIContext                     AppState
     |                               |
     +--------> Services <-----------+
                   |
          PodcastService
          StatsService
          Repository
          PathManager
```

**Webhook Security (Dual-Layer):**

1. **HMAC Signature Verification** (Layer 1):
   - Validates `ElevenLabs-Signature` header
   - Uses `ELEVENLABS_WEBHOOK_SECRET` from config
   - Proves request actually came from ElevenLabs

2. **Metadata Validation** (Layer 2):
   - Requires `episode_id` in `webhook_metadata`
   - Verifies episode exists in database
   - Prevents processing webhooks from other apps sharing the same ElevenLabs account

**Configuration:**

```bash
# .env
ELEVENLABS_WEBHOOK_SECRET=your_secret_from_elevenlabs_dashboard
ELEVENLABS_WEBHOOK_REQUIRE_METADATA=true  # default: true
```

**Starting the Server:**

```bash
thestill server                    # Start on localhost:8000
thestill server --host 0.0.0.0     # Expose to network
thestill server --port 8080        # Custom port
thestill server --reload           # Development mode with auto-reload
```

**Endpoints:**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Service identification |
| `/health` | GET | Health check |
| `/status` | GET | System statistics |
| `/docs` | GET | OpenAPI documentation |
| `/webhook/elevenlabs/speech-to-text` | POST | Receive transcription callback |
| `/webhook/elevenlabs/results` | GET | List webhook results |
| `/webhook/elevenlabs/results/{id}` | GET | Get specific result |
| `/webhook/elevenlabs/results/{id}` | DELETE | Delete result |

### Identifier System: Internal UUIDs vs External IDs

**NEW (as of 2025-10-14)**: The system now uses both internal and external identifiers for stability and traceability:

**Internal Identifiers (UUIDs)**:

- Every `Podcast` and `Episode` has an auto-generated `id` field (UUID v4)
- These are **internal, immutable identifiers** generated when records are first created
- Format: `"id": "550e8400-e29b-41d4-a716-446655440000"`
- Used for internal references and future database migrations
- Auto-generated via Pydantic's `default_factory=lambda: str(uuid.uuid4())`

**External Identifiers**:

- `Podcast.rss_url`: The RSS feed URL (external identifier from publisher)
- `Episode.external_id`: The GUID/ID from the RSS feed (replaces old `guid` field)
- These come from external sources (RSS feeds, YouTube, etc.)
- Can change if publishers modify their feeds (rare but possible)

**Timestamps**:

- `Podcast.created_at`: When the podcast was first added to the database
- `Episode.created_at`: When the episode was first discovered and added
- Auto-generated via `default_factory=datetime.utcnow`

**Repository Methods**:

- `get(podcast_id: str)`: Get podcast by internal UUID (primary key)
- `get_by_index(index: int)`: Get podcast by 1-based index (for CLI convenience)
- `get_by_url(url: str)`: Get podcast by RSS URL (external identifier)
- `get_episode(episode_id: str)`: Get episode by internal UUID
- `get_episode_by_external_id(podcast_url, episode_external_id)`: Get episode by external ID

**Why This Design?**:

- **Stability**: Internal UUIDs never change, even if external RSS URLs or GUIDs change
- **Traceability**: Timestamps track when records were added to the system
- **Performance**: SQLite with indexed queries (O(log n) vs O(n) for JSON scans)
- **Backward compatible**: Migration script preserves all existing data

### Configuration System

- Environment-based configuration with `.env` support
- Configurable storage paths, model choices, and processing parameters
- **PathManager integration**: `config.path_manager` provides centralized path access
- See `thestill/utils/config.py` for all options

#### Episode Management

**MAX_EPISODES_PER_PODCAST**: Limit the number of episodes tracked per podcast

- **Purpose**: Prevents database from becoming unmanageable for podcasts with hundreds of episodes
- **Behavior**:
  - Only the N most recent episodes (by `pub_date`) are kept per podcast
  - Already-processed episodes are never removed, even if total exceeds limit
  - New unprocessed episodes fill available slots up to the limit
  - Applied during `thestill refresh` command
- **Configuration**:
  - Set in `.env`: `MAX_EPISODES_PER_PODCAST=50`
  - Override per-run: `thestill refresh --max-episodes 10`
  - Leave empty for no limit (default)
- **Example**: With limit of 50 and podcast has 200 episodes
  - First refresh: Discovers 50 most recent episodes
  - After processing 10: Next refresh keeps those 10 processed + 40 most recent unprocessed
  - Result: Always stays at ≤50 total episodes per podcast

### Data Flow

**Six-Step Atomic Workflow (Refresh → Download → Downsample → Transcribe → Clean → Summarize):**

Each step is an atomic operation that can be run independently and scaled horizontally:

1. **Refresh** (`thestill refresh`):
   - **Atomic operation**: Only fetches RSS feeds and discovers episodes
   - Checks all tracked podcast feeds for new episodes
   - Parses RSS/YouTube feeds and discovers new episodes
   - Adds new episodes to SQLite database with `audio_url` set
   - Updates podcast metadata (title, description, etc.)
   - **Episode limiting**: Respects `MAX_EPISODES_PER_PODCAST` env var to keep database manageable
     - If set, only tracks the N most recent episodes per podcast
     - Protects processed episodes from removal
     - Can be overridden with `--max-episodes` CLI flag
   - **No side effects**: Does not download audio files

2. **Download** (`thestill download`):
   - **Atomic operation**: Only downloads audio
   - Finds all discovered episodes without `audio_path` set
   - Downloads original audio files to `data/original_audio/`
   - Updates episode `audio_path` in SQLite database
   - Skips already-downloaded episodes
   - **No side effects**: Does not fetch feeds or process audio

3. **Downsample** (`thestill downsample`):
   - **Atomic operation**: Only downsamples audio
   - Finds all downloaded episodes without downsampled versions
   - Converts to 16kHz, 16-bit, mono WAV format
   - Saves to `data/downsampled_audio/`
   - Updates episode `downsampled_audio_path` in database
   - **Why?** pyannote.audio only supports WAV, not M4A/MP3
   - **No side effects**: Does not download or transcribe

4. **Transcription** (`thestill transcribe`):
   - **Atomic operation**: Only transcribes audio
   - Finds all downsampled episodes without transcripts
   - **Requires downsampled audio**: Will fail if `downsampled_audio_path` is not set
   - Uses downsampled WAV files for optimal Whisper and pyannote compatibility
   - Whisper/WhisperX transcribes to structured JSON with speaker labels
   - Saves to `data/raw_transcripts/`
   - Updates episode `raw_transcript_path` in database
   - **No side effects**: Does not download or clean

5. **Cleaning** (`thestill clean-transcript`):
   - **Atomic operation**: Only cleans transcripts using two-pass facts-based approach
   - **Pass 1 (Facts Extraction)**: LLM analyzes transcript to extract:
     - Speaker mapping (SPEAKER_XX → actual names)
     - Episode facts (guests, topics, ad sponsors)
     - Podcast facts (hosts, recurring roles, keywords)
   - Facts saved to `data/episode_facts/` and `data/podcast_facts/`
   - **Pass 2 (Transcript Cleaning)**:
     - Stage 2a: Deterministic speaker name substitution (no LLM)
     - Stage 2b: LLM cleans spelling, grammar, detects ads, formats output
   - Saves cleaned Markdown to `data/clean_transcripts/`
   - Updates episode `clean_transcript_path` in database
   - **No side effects**: Does not download or transcribe

6. **Summarize** (`thestill summarize`):
   - **Atomic operation**: Only summarizes cleaned transcripts
   - Produces comprehensive analysis:
     - Executive summary
     - Notable quotes
     - Content angles for repurposing
     - Social media snippets
     - Resource/fact check
     - Critical analysis
   - Saves summaries to `data/summaries/`
   - Updates episode `summary_path` in database
   - **No side effects**: Does not clean or transcribe

**Episode State Progression:**

- `discovered` → new episode found in feed (has `audio_url`)
- `downloaded` → audio_path set
- `downsampled` → downsampled_audio_path set
- `transcribed` → raw_transcript_path set
- `cleaned` → clean_transcript_path set
- `summarized` → summary_path set (final state)

**Pipeline Design:**

- Each command can be run independently
- Commands only process what's needed (idempotent)
- **Separation of concerns**: Refresh (network I/O) vs Download (file I/O) vs Processing (CPU/GPU)
- Future: Add message queues between steps for distributed processing
- Future: Scale horizontally by running multiple workers per step

## File Structure

```text
thestill/
├── cli.py                 # Command-line interface
├── core/                  # Core processing modules
│   ├── feed_manager.py           # RSS/YouTube feed parsing
│   ├── audio_downloader.py       # Audio file downloading
│   ├── audio_preprocessor.py     # Audio downsampling to WAV
│   ├── youtube_downloader.py     # YouTube audio extraction
│   ├── transcriber.py            # Whisper/WhisperX transcription
│   ├── google_transcriber.py     # Google Cloud Speech-to-Text
│   ├── facts_extractor.py        # Pass 1: Extract speaker/episode facts
│   ├── facts_manager.py          # Load/save facts as Markdown
│   ├── transcript_cleaner.py     # Pass 2: Clean transcripts with LLM
│   ├── transcript_formatter.py   # JSON to Markdown conversion
│   ├── post_processor.py         # Summarization and analysis
│   ├── evaluator.py              # Transcript quality evaluation
│   ├── llm_provider.py           # Multi-provider LLM abstraction
│   ├── media_source.py           # Strategy pattern for RSS/YouTube
│   └── transcript_cleaning_processor.py  # Legacy three-phase cleaner
├── models/                # Pydantic data models
│   ├── podcast.py                # Episode, Podcast, CleanedTranscript
│   └── facts.py                  # PodcastFacts, EpisodeFacts
├── repositories/          # Data persistence layer
│   └── sqlite_podcast_repository.py
├── services/              # Business logic layer
│   ├── podcast_service.py
│   ├── refresh_service.py
│   └── stats_service.py
├── mcp/                   # MCP server for AI integration
│   ├── server.py
│   ├── tools.py
│   ├── resources.py
│   └── utils.py
├── web/                   # FastAPI web server
│   ├── app.py                   # Application factory
│   ├── dependencies.py          # DI (AppState, get_app_state)
│   └── routes/
│       ├── health.py            # Health/status endpoints
│       └── webhooks.py          # ElevenLabs webhook handlers
└── utils/                 # Utilities and configuration
    ├── config.py
    ├── path_manager.py
    └── logger.py

data/                      # Generated data directory
├── podcasts.db            # SQLite database (podcast/episode metadata and state)
├── original_audio/        # Original downloaded audio files (MP3, M4A, etc.)
├── downsampled_audio/     # Downsampled WAV files (16kHz, 16-bit, mono)
├── raw_transcripts/       # Raw Whisper JSON transcripts with speaker labels
├── clean_transcripts/     # Cleaned Markdown transcripts (corrected, formatted)
├── summaries/             # Episode summaries and analysis
├── podcast_facts/         # Podcast-level facts (hosts, sponsors, keywords)
├── episode_facts/         # Episode-level facts (guests, speaker mapping)
├── webhook_data/          # Received webhook payloads (ElevenLabs callbacks)
├── debug_feeds/           # Debug RSS feed snapshots
└── evaluations/           # Transcript evaluation results
```

## Key Technologies

### Transcription Providers

- **OpenAI Whisper**: Local speech-to-text transcription (CPU/GPU)
- **WhisperX**: Enhanced Whisper with speaker diarization and improved alignment
- **pyannote.audio**: State-of-the-art speaker diarization (for Whisper)
- **Google Cloud Speech-to-Text**: Cloud-based transcription with built-in diarization

### LLM Providers

- **OpenAI GPT-4**: Text processing, summarization, and analysis
- **Ollama**: Local LLM models for cost-effective processing
- **Google Gemini**: Fast and cost-effective cloud models (Flash variants)
- **Anthropic Claude**: High-quality text processing with Claude 3.5 Sonnet and Haiku

### Data Storage

- **SQLite**: Relational database for podcast/episode metadata
  - O(log n) indexed queries vs O(n) linear JSON scans
  - Row-level locking for concurrent operations
  - ACID transactions with referential integrity
  - Cache-friendly design (no triggers/cascades)
  - WAL mode for better read concurrency

### Other Dependencies

- **yt-dlp**: YouTube video/audio extraction with dynamic URL handling
- **Pydantic**: Data validation and settings management
- **Click**: Command-line interface framework
- **feedparser**: RSS/Atom feed parsing
- **pydub**: Audio file manipulation

## Development Guidelines

For complete development standards, see [docs/CODE_GUIDELINES.md](docs/CODE_GUIDELINES.md).

### Testing Strategy

**Coverage Targets**:

- **Overall**: 70%+ (current: 41.05%)
- **Core modules**: 90%+ (feed_manager, audio_downloader, transcriber)
- **Models**: 100% with branch coverage (✅ achieved: podcast.py)
- **Focus areas**: Public APIs, error paths, edge cases

**Test Types**:

1. **Unit Tests**: Test individual functions in isolation with mocked dependencies
   - Example: `test_transcript_parser.py` (47 tests)
   - Pattern: Mock external dependencies (requests, feedparser)
   - Fast execution, isolated failures

2. **Feature Tests**: Test complete feature modules
   - Example: `test_external_transcript_downloader.py` (19 tests)
   - Pattern: Use real PathManager, mock external APIs only

**Test Organization**:

```
tests/
├── test_transcript_parser.py              # Transcript parsing (47 tests)
└── test_external_transcript_downloader.py # External transcript downloads (19 tests)
```

**Running Tests**:

```bash
# Run all tests with coverage
pytest --cov=thestill --cov-report=html

# Run specific test file
pytest tests/test_transcript_parser.py -v

# Run tests matching pattern
pytest -k "test_download" -v
```

**Test Fixtures and Mocking**:

- Use `@pytest.fixture` for reusable test data
- Mock external APIs (requests, feedparser, LLM providers)
- Use `tmp_path` fixture for file system tests
- Never mock the code under test (only dependencies)

### Type Coverage and Type Hints

**Type Checking**: This project uses `mypy` for static type analysis

**Current Status**: ✅ 100% core and service layers type-hinted (Tasks R-019, R-021 complete)

**Type Hint Standards**:

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

**Running Type Checks**:

```bash
# Check all files
mypy thestill/

# Check specific module
mypy thestill/core/feed_manager.py

# Configuration in pyproject.toml
[tool.mypy]
python_version = "3.9"
warn_return_any = true
warn_unused_configs = true
check_untyped_defs = true
```

**Type Hints Coverage Target**: 90%+ (measured by mypy)

### Service Layer Architecture

The project uses a **layered architecture** with dependency injection for better testability and separation of concerns:

**1. CLI Layer** ([cli.py](thestill/cli.py)):

- User interface, command parsing, output formatting
- Instantiates services once and passes via Click context (`CLIContext`)
- Uses `CLIFormatter` for consistent output
- **Thin layer**: Delegates all business logic to services

**2. Service Layer** ([services/](thestill/services/)):

- **PodcastService**: Podcast CRUD operations, episode filtering
- **RefreshService**: Feed refresh business logic, episode discovery
- **StatsService**: Statistics and reporting
- Pattern: Services orchestrate core modules and manage transactions
- All services accept dependencies via constructor (dependency injection)

**3. Core Layer** ([core/](thestill/core/)):

- **Atomic processors**: Each module has single responsibility
- **FeedManager**: RSS/YouTube feed parsing with transaction context manager
- **AudioDownloader**: Downloads audio with retry logic (exponential backoff)
- **AudioPreprocessor**: Downsamples audio to 16kHz WAV
- **Transcriber**: Whisper/Google transcription with diarization
- **MediaSource Strategy Pattern**: Abstracts RSS vs YouTube (easy to add Spotify, etc.)

**4. Repository Layer** ([repositories/](thestill/repositories/)):

- Abstract interface for podcast/episode persistence
- `SqlitePodcastRepository`: SQLite implementation with indexed queries
- Cache-friendly design (explicit timestamps, no DB-level cascades)
- Pattern: Repository pattern with CRUD operations and transaction support

**5. Model Layer** ([models/podcast.py](thestill/models/podcast.py)):

- Pydantic models for type safety and validation
- `Episode`, `Podcast`, `EpisodeState` enum, `CleanedTranscript`
- Immutable data structures with computed properties

**Dependency Flow**:

```
CLI → Services → Core → Repository → Models
     ↓
   Utils (Config, PathManager, Logger)
```

**Key Design Patterns**:

- **Dependency Injection**: Services receive dependencies in constructor
- **Strategy Pattern**: MediaSource abstraction for multiple podcast sources
- **Repository Pattern**: Abstract data persistence (SQLite with migration script from JSON)
- **Context Manager**: Transaction support for batch operations (FeedManager, SqliteRepository)
- **Single Responsibility**: Each class/function has one clear purpose

### Error Handling Patterns

**1. Custom Exception Hierarchy** ([utils/exceptions.py](thestill/utils/exceptions.py)):

```python
class ThestillError(Exception):
    """Base exception for all domain errors"""

class FeedParseError(ThestillError):
    """Raised when RSS/YouTube feed parsing fails"""

class TranscriptionError(ThestillError):
    """Raised when audio transcription fails"""
```

**2. Fail Fast with Validation**:

```python
def require_file_exists(self, file_path: Path, error_message: str) -> Path:
    """Validate file exists or raise FileNotFoundError"""
    if not file_path.exists():
        raise FileNotFoundError(error_message)
    return file_path
```

**3. Structured Logging** (replaced all `print()` statements):

```python
import logging
logger = logging.getLogger(__name__)

# Log levels by severity
logger.debug("Detailed diagnostic info")
logger.info("Episode downloaded successfully")
logger.warning("Retry attempt 2/3 after network timeout")
logger.error("Download failed for episode XYZ")
logger.critical("Cannot load configuration file")
```

**4. Retry Logic with Exponential Backoff**:

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

**5. Error Handling Guidelines**:

- **Never catch bare `except:`** - always specify exception types
- **Never silently fail** - always log errors before handling
- **Early returns** - use guard clauses to reduce nesting
- **Context in logs** - include episode GUID, podcast URL, file paths
- **User-friendly CLI errors** - catch and format for end users

**6. Error Recovery**:

- **Idempotent operations**: All pipeline steps can be safely re-run
- **State tracking**: Episodes track progress through pipeline (EpisodeState enum)
- **Partial failures**: One episode failure doesn't stop batch processing
- **Transaction support**: Batch updates with rollback on error (FeedManager.transaction())

### Performance Considerations

- Whisper processing is CPU-intensive; consider model size vs. accuracy tradeoffs
- LLM API calls can be expensive; estimate costs before processing
- Audio files can be large; implement cleanup strategies

### Configuration Patterns

- Use environment variables for sensitive data (API keys)
- Provide sensible defaults for all configuration options
- Validate configuration at startup

## Transcription Provider Setup

### Whisper (Local Transcription)

**Basic Setup:**

```bash
# Install dependencies
pip install -e .

# Configure .env
TRANSCRIPTION_PROVIDER=whisper
WHISPER_MODEL=base  # Options: tiny, base, small, medium, large
```

**With Speaker Diarization:**

1. Get HuggingFace Token:
   - Create account at <https://huggingface.co>
   - Get token from <https://huggingface.co/settings/tokens>
   - Accept model license at <https://huggingface.co/pyannote/speaker-diarization-3.1>

2. Configure .env:

   ```bash
   ENABLE_DIARIZATION=true
   HUGGINGFACE_TOKEN=your_token_here
   MIN_SPEAKERS=  # Optional: minimum speakers (auto-detect if empty)
   MAX_SPEAKERS=  # Optional: maximum speakers (auto-detect if empty)
   ```

**How it works:**

- WhisperXTranscriber with diarization enabled:
  1. Transcribes audio with WhisperX (improved alignment)
  2. Aligns output for accurate word-level timestamps
  3. Runs pyannote.audio speaker diarization
  4. Assigns speaker labels to segments
  5. Falls back to standard Whisper if diarization fails

### Google Cloud Speech-to-Text (Cloud Transcription)

**Setup:**

1. Create Google Cloud project at <https://console.cloud.google.com/>
2. Enable Speech-to-Text API
3. Create service account and download JSON key:
   - Go to <https://console.cloud.google.com/apis/credentials>
   - Create service account → Download JSON key
4. Configure .env:

   ```bash
   TRANSCRIPTION_PROVIDER=google
   GOOGLE_APP_CREDENTIALS=/path/to/service-account-key.json
   GOOGLE_CLOUD_PROJECT_ID=your-project-id
   GOOGLE_STORAGE_BUCKET=  # Optional: for files >10MB (auto-created if empty)
   ENABLE_DIARIZATION=true  # Built-in diarization (no HuggingFace token needed)
   ```

**How it works:**

- Files <10MB: Synchronous transcription (fast)
- Files >10MB: Async transcription via Google Cloud Storage
- Language automatically detected from podcast RSS metadata
- Built-in speaker diarization (no additional setup required)

**Pricing:**

- Standard recognition: ~$0.024/minute
- With speaker diarization: ~$0.048/minute
- See: <https://cloud.google.com/speech-to-text/pricing>

### ElevenLabs Speech-to-Text (Cloud Transcription)

**Setup:**

1. Create ElevenLabs account at <https://elevenlabs.io>
2. Get API key from <https://elevenlabs.io/app/settings/api-keys>
3. Configure .env:

   ```bash
   TRANSCRIPTION_PROVIDER=elevenlabs
   ELEVENLABS_API_KEY=your-api-key-here
   ELEVENLABS_MODEL=scribe_v1  # Options: scribe_v1, scribe_v1_experimental
   ENABLE_DIARIZATION=true  # Built-in diarization (up to 32 speakers)
   ```

**How it works:**

- Uses Scribe v1 model for high-accuracy transcription
- Supports files up to 2GB
- Built-in speaker diarization (up to 32 speakers)
- Word-level timestamps
- Language auto-detection (or specify with language code)
- Optional audio event detection (laughter, applause, etc.)

**Pricing:**

- See: <https://elevenlabs.io/pricing>
- Billed per audio hour

### Comparison: Whisper vs Google vs ElevenLabs

| Feature | Whisper (Local) | Google Cloud | ElevenLabs |
|---------|----------------|--------------|------------|
| **Cost** | Free (uses local CPU/GPU) | ~$0.024-0.048/min | Per audio hour |
| **Privacy** | Fully local | Audio sent to Google | Audio sent to ElevenLabs |
| **Speed** | Depends on hardware | Fast (cloud) | Fast (cloud) |
| **Diarization Setup** | Requires HuggingFace token + pyannote.audio | Built-in | Built-in |
| **Accuracy** | Good | Excellent | Excellent |
| **Max Speakers** | Depends on model | Varies | 32 |
| **Max File Size** | Memory limited | Chunked via GCS | 2GB |
| **Network** | Not required | Required | Required |

**Transcript Output Format (all providers):**

```
[00:15] [SPEAKER_01] Welcome to the podcast.
[00:18] [SPEAKER_02] Thanks for having me.
```

**Configuration Options (all providers):**

- `ENABLE_DIARIZATION`: Enable/disable speaker identification
- `MIN_SPEAKERS`: Minimum speakers (leave empty to use provider's internal defaults for best results)
- `MAX_SPEAKERS`: Maximum speakers (leave empty to use provider's internal defaults for best results)

**Recommendation:** Leave `MIN_SPEAKERS` and `MAX_SPEAKERS` empty for most podcasts. Both Google and Whisper/pyannote have sensible internal defaults and auto-detection works well. Only set explicit values if you know the exact speaker count (e.g., solo show, fixed two-host format).

---

## Refactoring Progress

This project underwent a comprehensive refactoring from October 2024 to present, transforming from a monolithic script into a well-architected, testable system. See [docs/REFACTORING_PLAN.md](docs/REFACTORING_PLAN.md) for the complete plan.

### Current Status (as of 2025-12-02)

**Progress**: Refactoring complete ✅
**Storage**: SQLite database
**New Features Added Post-Refactoring**:

- Facts-based two-pass transcript cleaning pipeline
- Comprehensive summarization with post_processor.py
- Transcript quality evaluation
- MCP server for AI assistant integration
- Streaming LLM output support

### Key Achievements

**Week 1: Foundation & Testing Infrastructure** ✅ (100% complete)

- ✅ Repository layer abstraction (enables future database migration)
- ✅ PathManager centralization (single source of truth for file paths)
- ✅ Pre-commit hooks (black, isort, pylint, mypy)
- ✅ Custom exception hierarchy
- ✅ Replaced all `print()` with structured logging

**Week 2: Service Layer & CLI Refactoring** ✅ (100% complete)

- ✅ CLI context dependency injection (services instantiated once)
- ✅ RefreshService extraction (business logic out of CLI)
- ✅ CLIFormatter for consistent output
- ✅ Retry logic with exponential backoff
- ✅ Progress bars for batch operations (MCP-compatible)
- ✅ Magic numbers extracted to constants

**Week 3: Testing & Type Coverage** ✅ (100% complete)

- ✅ Type hints for all core and service modules (100% mypy clean)
- ✅ Integration tests for full pipeline (9 end-to-end scenarios)
- ✅ Contract tests for service boundaries (32 tests prevent API breakage)
- ✅ EpisodeState enum for type-safe state management
- ✅ Comprehensive unit tests (AudioDownloader 99%, PathManager 100%, PodcastService 92%)

**Week 4: Architecture & Polish** ✅ (100% complete)

- ✅ MediaSource strategy pattern (RSS + YouTube abstraction, enables Spotify/SoundCloud)
- ✅ FeedManager transaction context manager (batch operations)
- ✅ PathManager require_file_exists helper (centralized validation)
- ✅ Performance metrics tracking (TranscriptCleaningMetrics)
- ✅ LLM provider display names (abstraction improvement)
- ✅ Documentation updated

**Week 5: SQLite Migration** ✅ (100% complete)

- ✅ SQLite repository implementation (540 lines, 34 tests)
- ✅ API refactoring (get vs find naming, method collision fix)
- ✅ Migration script with dry-run and backup support
- ✅ CLI integration (config, database_path setting)
- ✅ Removed JSON repository (1000+ lines deleted)
- ✅ All tests passing with SQLite (265/265)

### Architecture Improvements

**Before Refactoring**:

```
cli.py (1000+ lines)
├── Inline path construction
├── Inline feed parsing
├── Inline error handling
├── Scattered print() statements
└── No tests
```

**After Refactoring**:

```
CLI Layer (cli.py)
  ↓ (dependency injection)
Service Layer (services/)
  ├── PodcastService (CRUD operations)
  ├── RefreshService (feed discovery)
  └── StatsService (reporting)
  ↓ (orchestration)
Core Layer (core/)
  ├── FeedManager (with transactions)
  ├── MediaSource Strategy (RSS/YouTube)
  ├── AudioDownloader (with retry)
  └── Transcriber (Whisper/Google)
  ↓ (persistence)
Repository Layer (podcast_repository.py)
  └── SqlitePodcastRepository (cache-friendly, indexed queries)
  ↓ (data models)
Model Layer (models/podcast.py)
  └── Pydantic models with validation
```

### Design Patterns Implemented

1. **Repository Pattern**: Abstract data persistence with SQLite
   - Migrated from JSON to SQLite (O(log n) indexed queries)
   - Migration script with validation and backup support
   - 34 SQLite repository tests, 100% passing

2. **Strategy Pattern** (R-029): Media source abstraction
   - Clean separation of RSS vs YouTube logic
   - Extensible: Add Spotify, SoundCloud with one new class
   - 34 unit tests for all source types

3. **Dependency Injection** (R-012): Service composition
   - Services receive dependencies in constructor
   - Testable with mocks
   - Single instantiation in CLI context

4. **Context Manager** (R-028): Transaction support
   - Batch updates with single save operation
   - Reduces file I/O by ~80% for bulk operations

5. **Single Responsibility Principle**: Throughout codebase
   - Each class/function has one clear purpose
   - Atomic pipeline steps (refresh → download → downsample → transcribe → clean)

### Testing Infrastructure

**Current Test Suite**: 66 tests across 2 test files

- `test_transcript_parser.py`: 47 tests (transcript format parsing)
- `test_external_transcript_downloader.py`: 19 tests (external transcript downloads)

**Testing Best Practices**:

- Mock external dependencies (requests, feedparser, LLM APIs)
- Use `tmp_path` for file system tests
- Arrange-Act-Assert pattern
- Descriptive test names (`test_download_retries_on_network_error`)

### Remaining Work (6 tasks)

**High Priority**:

- R-032: GitHub Actions CI workflow (45 min)
- R-031: Makefile for common commands (20 min)

**Low Priority**:

- R-033: Simplify CLI import pattern (30 min)
- R-034: Episode GUID uniqueness validation (30 min)
- R-035: Update README with new features (30 min)

**Estimated completion**: 2-3 hours remaining

### Lessons Learned

**What Worked Well**:

- ✅ Small atomic commits (1 task = 1 commit = <1 hour)
- ✅ Tests green at all times (no breaking changes)
- ✅ Repository pattern (easiest architectural change)
- ✅ Dependency injection (improved testability dramatically)
- ✅ Type hints (caught 2 bugs during R-021)

**What Was Challenging**:

- ⚠️ Increasing test coverage for complex modules (FeedManager at 26%)
- ⚠️ Mocking LLM API calls (async, streaming responses)
- ⚠️ Balancing refactoring vs new features (discipline required)

**Key Takeaways**:

1. **Start with data layer**: Repository pattern was the best first move
2. **Type hints reveal bugs**: Found 2 production bugs during type annotation
3. **Contract tests prevent breakage**: Critical for refactoring service boundaries
4. **Progress tracking matters**: Detailed plan kept momentum over 4 weeks
5. **Coverage isn't everything**: Focus on critical paths and error handling

### Future Improvements

**Architecture** (Post-Refactoring):

- Add SQLite/PostgreSQL repository implementation
- Add message queue for distributed processing (RabbitMQ/Redis)
- Extract LLM prompts to configuration files
- Add caching layer for expensive operations

**Features** (Separate from Refactoring):

- Add Spotify podcast support (via MediaSource)
- Add SoundCloud support (via MediaSource)
- Web UI for podcast management
- Email notifications for new episodes
- RSS feed output for processed transcripts

**Operations**:

- Add Docker Compose for deployment
- Add monitoring and alerting (Sentry, DataDog)
- Add rate limiting for LLM APIs
- Add cost tracking dashboard

### Contributing

When adding new features or making changes:

1. **Read the guidelines**: [docs/CODE_GUIDELINES.md](docs/CODE_GUIDELINES.md)
2. **Follow the architecture**: Use existing patterns (Repository, Strategy, DI)
3. **Write tests first**: Aim for 80%+ coverage on new code
4. **Add type hints**: All new code must be type-hinted
5. **Small PRs**: Keep changes under 300 lines
6. **Update docs**: Keep CLAUDE.md and README.md current
7. **Run pre-commit**: `pre-commit run --all-files` before pushing

**Questions?** Open an issue or start a discussion in the repository.

@AGENTS.md
