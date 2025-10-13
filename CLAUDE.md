# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

thestill.ai is an automated podcast transcription and summarization pipeline built with Python. It converts audio podcasts into readable, summarized content using either local Whisper transcription or cloud-based Google Speech-to-Text, and supports multiple LLM providers (OpenAI GPT-4, Ollama, Google Gemini, Anthropic Claude) for analysis.

## Development Commands

### Installation and Setup
```bash
# Install in development mode
pip install -e .

# Install with development dependencies
pip install -e ".[dev]"

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

# Clean existing transcripts with LLM (step 5)
thestill clean-transcript [--dry-run] [--max-episodes 5]

# List tracked podcasts
thestill list

# Check system status
thestill status

# Clean up old files
thestill cleanup
```

### Testing and Code Quality
```bash
# Run tests (when implemented)
pytest

# Format code
black thestill/
isort thestill/

# Type checking
mypy thestill/
```

## Architecture

### Core Components

1. **Feed Manager** (`thestill/core/feed_manager.py`)
   - Handles RSS feed parsing and episode tracking
   - Supports Apple Podcasts URL resolution via iTunes API
   - Integrates YouTube playlist/channel support
   - Stores podcast metadata and processing status
   - Identifies new episodes since last run

2. **Audio Downloader** (`thestill/core/audio_downloader.py`)
   - Downloads podcast audio files from RSS feeds and YouTube to `data/original_audio/`
   - Handles various audio formats (MP3, M4A, etc.)
   - Manages file cleanup and storage
   - Routes YouTube URLs to YouTubeDownloader
   - **Atomic operation**: Only downloads, does not process audio

3. **Audio Preprocessor** (`thestill/core/audio_preprocessor.py`)
   - Downsamples audio to 16kHz, 16-bit, mono WAV format
   - Saves downsampled WAV files to `data/downsampled_audio/`
   - **Important**: Solves pyannote.audio M4A/MP3 compatibility issues
   - No audio enhancement (preserves original quality)
   - **Atomic operation**: Only downsamples, does not download or transcribe

4. **YouTube Downloader** (`thestill/core/youtube_downloader.py`)
   - Uses yt-dlp for robust YouTube video/audio extraction
   - Handles dynamic URLs and format selection
   - Extracts playlist and channel metadata
   - Downloads best quality audio and converts to M4A

5. **Transcriber** (`thestill/core/transcriber.py` and `thestill/core/google_transcriber.py`)
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

6. **Transcript Cleaning Processor** (`thestill/core/transcript_cleaning_processor.py`)
   - **NEW**: Three-phase LLM cleaning pipeline focused on accuracy:
     - Phase 1: Analyze and identify corrections (spelling, grammar, filler words, ads)
     - Phase 2: Identify speakers from context and self-introductions
     - Phase 3: Generate final cleaned Markdown transcript
   - Uses episode/podcast metadata for better context
   - Saves corrections list for debugging
   - British English output

7. **Models** (`thestill/models/podcast.py`)
   - Pydantic models for type safety
   - Episode, Podcast, Quote, ProcessedContent, and CleanedTranscript schemas

8. **Path Manager** (`thestill/utils/path_manager.py`)
   - **Centralized path management** for all file artifacts
   - Single source of truth for directory and file paths
   - Prevents scattered path logic across codebase
   - Methods for all artifact types (audio, transcripts, summaries, etc.)
   - Integrated into Config and FeedManager
   - **Reduces errors** when directory structures change

### Configuration System

- Environment-based configuration with `.env` support
- Configurable storage paths, model choices, and processing parameters
- **PathManager integration**: `config.path_manager` provides centralized path access
- See `thestill/utils/config.py` for all options

#### Episode Management

**MAX_EPISODES_PER_PODCAST**: Limit the number of episodes tracked per podcast
- **Purpose**: Prevents `feeds.json` from becoming unmanageable for podcasts with hundreds of episodes
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

**Five-Step Atomic Workflow (Refresh → Download → Downsample → Transcribe → Clean):**

Each step is an atomic operation that can be run independently and scaled horizontally:

1. **Refresh** (`thestill refresh`):
   - **Atomic operation**: Only fetches RSS feeds and discovers episodes
   - Checks all tracked podcast feeds for new episodes
   - Parses RSS/YouTube feeds and discovers new episodes
   - Adds new episodes to `data/feeds.json` with `audio_url` set
   - Updates podcast metadata (title, description, etc.)
   - **Episode limiting**: Respects `MAX_EPISODES_PER_PODCAST` env var to keep feeds.json manageable
     - If set, only tracks the N most recent episodes per podcast
     - Protects processed episodes from removal
     - Can be overridden with `--max-episodes` CLI flag
   - **No side effects**: Does not download audio files

2. **Download** (`thestill download`):
   - **Atomic operation**: Only downloads audio
   - Finds all discovered episodes without `audio_path` set
   - Downloads original audio files to `data/original_audio/`
   - Updates episode `audio_path` in `data/feeds.json`
   - Skips already-downloaded episodes
   - **No side effects**: Does not fetch feeds or process audio

3. **Downsample** (`thestill downsample`):
   - **Atomic operation**: Only downsamples audio
   - Finds all downloaded episodes without downsampled versions
   - Converts to 16kHz, 16-bit, mono WAV format
   - Saves to `data/downsampled_audio/`
   - Updates episode `downsampled_audio_path` in `data/feeds.json`
   - **Why?** pyannote.audio only supports WAV, not M4A/MP3
   - **No side effects**: Does not download or transcribe

4. **Transcription** (`thestill transcribe`):
   - **Atomic operation**: Only transcribes audio
   - Finds all downsampled episodes without transcripts
   - **Requires downsampled audio**: Will fail if `downsampled_audio_path` is not set
   - Uses downsampled WAV files for optimal Whisper and pyannote compatibility
   - Whisper/WhisperX transcribes to structured JSON with speaker labels
   - Saves to `data/raw_transcripts/`
   - Updates episode `raw_transcript_path` in `data/feeds.json`
   - **No side effects**: Does not download or clean

5. **Cleaning** (`thestill clean-transcript`):
   - **Atomic operation**: Only cleans transcripts
   - Loads existing transcript JSON files
   - Phase 1: LLM analyzes for corrections (spelling, grammar, fillers, ads)
   - Phase 2: LLM identifies speakers using episode/podcast context
   - Phase 3: LLM generates clean Markdown transcript
   - Saves cleaned Markdown to `data/clean_transcripts/`
   - Optionally saves corrections and speaker mapping for debugging
   - Updates episode `clean_transcript_path` in `data/feeds.json`
   - **No side effects**: Does not download or transcribe

**Episode State Progression:**
- `discovered` → new episode found in feed (has `audio_url`)
- `downloaded` → audio_path set
- `downsampled` → downsampled_audio_path set
- `transcribed` → raw_transcript_path set
- `cleaned` → clean_transcript_path set (final state)

**Pipeline Design:**
- Each command can be run independently
- Commands only process what's needed (idempotent)
- **Separation of concerns**: Refresh (network I/O) vs Download (file I/O) vs Processing (CPU/GPU)
- Future: Add message queues between steps for distributed processing
- Future: Scale horizontally by running multiple workers per step

## File Structure

```
thestill/
├── core/              # Core processing modules
│   ├── feed_manager.py
│   ├── audio_downloader.py
│   ├── youtube_downloader.py
│   ├── transcriber.py
│   ├── transcript_cleaning_processor.py  # NEW: Copywriting-focused cleaner
│   └── llm_processor.py  # LEGACY: Old summarization pipeline
├── models/            # Pydantic data models
│   └── podcast.py
├── utils/             # Utilities and configuration
│   ├── config.py
│   └── logger.py
└── cli.py            # Command-line interface

data/                      # Generated data directory
├── original_audio/        # Original downloaded audio files (MP3, M4A, etc.)
├── downsampled_audio/     # Downsampled WAV files (16kHz, 16-bit, mono) for transcription
├── raw_transcripts/       # Raw Whisper JSON transcripts with speaker labels
├── clean_transcripts/     # Cleaned Markdown transcripts (corrected, formatted)
├── summaries/             # Episode summaries (future use)
└── feeds.json            # Podcast feed tracking with episode state
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

### Other Dependencies
- **yt-dlp**: YouTube video/audio extraction with dynamic URL handling
- **Pydantic**: Data validation and settings management
- **Click**: Command-line interface framework
- **feedparser**: RSS/Atom feed parsing
- **pydub**: Audio file manipulation

## Development Guidelines

### Error Handling
- All external API calls should have proper exception handling
- File operations should handle missing directories gracefully
- Network requests should include timeouts and retries

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
   - Create account at https://huggingface.co
   - Get token from https://huggingface.co/settings/tokens
   - Accept model license at https://huggingface.co/pyannote/speaker-diarization-3.1

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
1. Create Google Cloud project at https://console.cloud.google.com/
2. Enable Speech-to-Text API
3. Create service account and download JSON key:
   - Go to https://console.cloud.google.com/apis/credentials
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
- See: https://cloud.google.com/speech-to-text/pricing

### Comparison: Whisper vs Google

| Feature | Whisper (Local) | Google Cloud |
|---------|----------------|--------------|
| **Cost** | Free (uses local CPU/GPU) | ~$0.024-0.048/min |
| **Privacy** | Fully local | Audio sent to Google |
| **Speed** | Depends on hardware | Fast (cloud processing) |
| **Diarization Setup** | Requires HuggingFace token + pyannote.audio | Built-in, no setup |
| **Accuracy** | Good | Excellent (especially accents) |
| **Large Files** | Memory intensive | Handles via GCS |
| **Network** | Not required | Required |

**Transcript Output Format (both providers):**
```
[00:15] [SPEAKER_01] Welcome to the podcast.
[00:18] [SPEAKER_02] Thanks for having me.
```

**Configuration Options (both providers):**
- `ENABLE_DIARIZATION`: Enable/disable speaker identification
- `MIN_SPEAKERS`: Minimum speakers (leave empty to use provider's internal defaults for best results)
- `MAX_SPEAKERS`: Maximum speakers (leave empty to use provider's internal defaults for best results)

**Recommendation:** Leave `MIN_SPEAKERS` and `MAX_SPEAKERS` empty for most podcasts. Both Google and Whisper/pyannote have sensible internal defaults and auto-detection works well. Only set explicit values if you know the exact speaker count (e.g., solo show, fixed two-host format).
