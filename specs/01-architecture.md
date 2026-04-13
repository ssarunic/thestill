# Architecture

This document describes the internal architecture, design patterns, and data flow of thestill.

## Layered Architecture

The project uses a layered architecture with dependency injection for testability and separation of concerns.

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
Repository Layer (repositories/)
  └── SqlitePodcastRepository (cache-friendly, indexed queries)
  ↓ (data models)
Model Layer (models/podcast.py)
  └── Pydantic models with validation
```

**Dependency Flow**:

```
CLI → Services → Core → Repository → Models
     ↓
   Utils (Config, PathManager, Logger)
```

## Layer Responsibilities

### 1. CLI Layer (`cli.py`)

- User interface, command parsing, output formatting
- Instantiates services once and passes via Click context (`CLIContext`)
- Uses `CLIFormatter` for consistent output
- **Thin layer**: Delegates all business logic to services

### 2. Service Layer (`services/`)

- **PodcastService**: Podcast CRUD operations, episode filtering
- **RefreshService**: Feed refresh business logic, episode discovery
- **StatsService**: Statistics and reporting
- Pattern: Services orchestrate core modules and manage transactions
- All services accept dependencies via constructor (dependency injection)

### 3. Core Layer (`core/`)

- **Atomic processors**: Each module has single responsibility
- **FeedManager**: RSS/YouTube feed parsing with transaction context manager
- **AudioDownloader**: Downloads audio with retry logic (exponential backoff)
- **AudioPreprocessor**: Downsamples audio to 16kHz WAV
- **Transcriber**: Whisper/Google transcription with diarization
- **MediaSource Strategy Pattern**: Abstracts RSS vs YouTube (easy to add Spotify, etc.)

### 4. Repository Layer (`repositories/`)

- Abstract interface for podcast/episode persistence
- `SqlitePodcastRepository`: SQLite implementation with indexed queries
- Cache-friendly design (explicit timestamps, no DB-level cascades)
- Pattern: Repository pattern with CRUD operations and transaction support

### 5. Model Layer (`models/podcast.py`)

- Pydantic models for type safety and validation
- `Episode`, `Podcast`, `EpisodeState` enum, `CleanedTranscript`
- Immutable data structures with computed properties

## Design Patterns

### Repository Pattern

Abstract data persistence with SQLite:

- Migrated from JSON to SQLite (O(log n) indexed queries)
- Migration script with validation and backup support

### Strategy Pattern (MediaSource)

Clean separation of RSS vs YouTube logic:

- `MediaSource` ABC defines interface for URL validation, episode fetching, downloading
- `RSSMediaSource`: Handles RSS feeds and Apple Podcasts URL resolution
- `YouTubeMediaSource`: Wraps YouTube downloader for playlists/channels
- `MediaSourceFactory`: Auto-detects source type from URL
- Extensible: Add Spotify, SoundCloud with one new class

### Dependency Injection

Services receive dependencies in constructor:

- Testable with mocks
- Single instantiation in CLI context

### Context Manager (Transactions)

Batch updates with single save operation:

- `FeedManager.transaction()` for batch operations
- Reduces file I/O by ~80% for bulk operations

### Single Responsibility Principle

Each class/function has one clear purpose:

- Atomic pipeline steps (refresh → download → downsample → transcribe → clean)

## Data Flow

### Six-Step Atomic Workflow

Each step is an atomic operation that can be run independently and scaled horizontally:

1. **Refresh** (`thestill refresh`): Fetches RSS feeds and discovers episodes
2. **Download** (`thestill download`): Downloads audio files
3. **Downsample** (`thestill downsample`): Converts to 16kHz WAV
4. **Transcribe** (`thestill transcribe`): Creates raw transcripts
5. **Clean** (`thestill clean-transcript`): LLM-based transcript cleaning
6. **Summarize** (`thestill summarize`): Comprehensive analysis

### Episode State Progression

```
discovered → downloaded → downsampled → transcribed → cleaned → summarized
                                                                    ↓
                                                                 (failed)
```

- `discovered` → new episode found in feed (has `audio_url`)
- `downloaded` → `audio_path` set
- `downsampled` → `downsampled_audio_path` set
- `transcribed` → `raw_transcript_path` set
- `cleaned` → `clean_transcript_path` set
- `summarized` → `summary_path` set (final state)
- `failed` → processing failed at some stage (has `failed_at_stage` set)

### Pipeline Design

- Each command can be run independently
- Commands only process what's needed (idempotent)
- **Separation of concerns**: Refresh (network I/O) vs Download (file I/O) vs Processing (CPU/GPU)

## Identifier System

### Internal Identifiers (UUIDs)

- Every `Podcast` and `Episode` has an auto-generated `id` field (UUID v4)
- Immutable, generated when records are first created
- Used for internal references and database operations

### External Identifiers

- `Podcast.rss_url`: The RSS feed URL
- `Episode.external_id`: The GUID/ID from the RSS feed
- Can change if publishers modify their feeds

### Repository Methods

- `get(podcast_id: str)`: Get podcast by internal UUID
- `get_by_index(index: int)`: Get podcast by 1-based index (CLI convenience)
- `get_by_url(url: str)`: Get podcast by RSS URL
- `get_episode(episode_id: str)`: Get episode by internal UUID
- `get_episode_by_external_id(podcast_url, episode_external_id)`: Get by external ID

## Task Queue System

### QueueManager (`queue_manager.py`)

SQLite-backed task queue for processing jobs:

- Task states: `pending`, `processing`, `completed`, `retry_scheduled`, `failed`, `dead`
- Task metadata for pipeline chaining (`run_full_pipeline` flag)
- Retry scheduling with `next_retry_at` timestamp

### TaskWorker (`task_worker.py`)

Background worker that processes tasks:

- Picks up pending/retry-ready tasks
- Executes stage-specific handlers
- Handles error classification and retry logic
- Chain-enqueues next stage for full pipeline runs

### Task Handlers (`task_handlers.py`)

- `handle_download()`, `handle_downsample()`, `handle_transcribe()`
- `handle_clean()`, `handle_summarize()`
- Each handler raises `TransientError` or `FatalError` for classification

### Exponential Backoff

- Retry delays: ~5s → ~30s → ~3min → give up
- Jitter (±20%) to prevent thundering herd
- Max 3 retries before marking as `failed` (transient) or `dead` (fatal)

## Core Components

### Media Source Strategy Pattern (`core/media_source.py`)

Abstracts different podcast sources:

- **MediaSource ABC**: Interface for URL validation, episode fetching, downloading
- **RSSMediaSource**: RSS feeds and Apple Podcasts URL resolution
- **YouTubeMediaSource**: Wraps YouTube downloader for playlists/channels
- **MediaSourceFactory**: Auto-detects source type from URL

### Feed Manager (`core/feed_manager.py`)

- Coordinates podcast feed management across all sources
- Uses MediaSourceFactory for source-specific operations
- Transaction context manager for batch updates

### Audio Downloader (`core/audio_downloader.py`)

- Downloads podcast audio files to `data/original_audio/`
- Retry logic with exponential backoff for network errors
- **Atomic operation**: Only downloads, does not process

### Audio Preprocessor (`core/audio_preprocessor.py`)

- Downsamples audio to 16kHz, 16-bit, mono WAV format
- Saves to `data/downsampled_audio/`
- Solves pyannote.audio M4A/MP3 compatibility issues

### Transcriber (`core/transcriber.py`, `core/google_transcriber.py`)

- **WhisperTranscriber**: Standard OpenAI Whisper
- **WhisperXTranscriber**: Enhanced with speaker diarization
- **GoogleCloudTranscriber**: Cloud-based with built-in diarization
- Configurable via `TRANSCRIPTION_PROVIDER`

### Facts System (`core/facts_extractor.py`, `core/facts_manager.py`)

Two-level facts architecture for transcript cleaning:

- **PodcastFacts**: Recurring knowledge (hosts, sponsors, keywords)
- **EpisodeFacts**: Episode-specific knowledge (guests, speaker mapping)

### Transcript Cleaner (`core/transcript_cleaner.py`)

Two-pass cleaning pipeline:

- Stage 2a: Deterministic speaker name substitution (no LLM)
- Stage 2b: LLM cleans spelling, grammar, detects ads

### Post Processor (`core/post_processor.py`)

Comprehensive analysis of cleaned transcripts:

- Executive summary, notable quotes, content angles
- Social snippets, resource check, critical analysis

### Path Manager (`utils/path_manager.py`)

- Centralized path management for all file artifacts
- Single source of truth for directory and file paths
- Methods for all artifact types (audio, transcripts, summaries)
