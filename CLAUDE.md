# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

thestill.ai is an automated podcast transcription and summarization pipeline built with Python. It converts audio podcasts into readable, summarized content using OpenAI Whisper for transcription and GPT-4 for analysis.

## Development Commands

### Installation and Setup
```bash
# Install in development mode
pip install -e .

# Install with development dependencies
pip install -e ".[dev]"

# Set up environment
cp .env.example .env
# Edit .env with OpenAI API key
```

### Main CLI Commands
```bash
# Add podcast feed (supports RSS, Apple Podcasts, YouTube)
thestill add "https://example.com/rss"
thestill add "https://podcasts.apple.com/us/podcast/id123456"
thestill add "https://www.youtube.com/@channelname"
thestill add "https://www.youtube.com/playlist?list=..."

# Process new episodes
thestill process

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
   - Downloads podcast audio files from RSS feeds and YouTube
   - Handles various audio formats (MP3, M4A, etc.)
   - Manages file cleanup and storage
   - Routes YouTube URLs to YouTubeDownloader

3. **YouTube Downloader** (`thestill/core/youtube_downloader.py`)
   - Uses yt-dlp for robust YouTube video/audio extraction
   - Handles dynamic URLs and format selection
   - Extracts playlist and channel metadata
   - Downloads best quality audio and converts to M4A

4. **Transcriber** (`thestill/core/transcriber.py`)
   - **WhisperTranscriber**: Standard OpenAI Whisper for speech-to-text
   - **WhisperXTranscriber**: Enhanced transcription with speaker diarization
   - Supports word-level timestamps and speaker identification
   - Configurable model sizes (tiny, base, small, medium, large)
   - Automatic fallback to standard Whisper if diarization fails

5. **LLM Processor** (`thestill/core/llm_processor.py`)
   - Three-step LLM pipeline:
     - Cleans transcripts and detects ads
     - Generates comprehensive summaries
     - Extracts notable quotes with analysis

6. **Models** (`thestill/models/podcast.py`)
   - Pydantic models for type safety
   - Episode, Podcast, Quote, and ProcessedContent schemas

### Configuration System

- Environment-based configuration with `.env` support
- Configurable storage paths, model choices, and processing parameters
- See `thestill/utils/config.py` for all options

### Data Flow

1. Feeds are checked for new episodes (RSS, Apple Podcasts resolved to RSS, or YouTube)
2. Audio files are downloaded to `data/audio/` (via direct download or yt-dlp)
3. Whisper transcribes audio to structured JSON in `data/transcripts/`
4. LLM processes transcripts and saves summaries to `data/summaries/`
5. Episode status is updated in `data/feeds.json`

## File Structure

```
thestill/
├── core/              # Core processing modules
│   ├── feed_manager.py
│   ├── audio_downloader.py
│   ├── youtube_downloader.py
│   ├── transcriber.py
│   └── llm_processor.py
├── models/            # Pydantic data models
│   └── podcast.py
├── utils/             # Utilities and configuration
│   ├── config.py
│   └── logger.py
└── cli.py            # Command-line interface

data/                 # Generated data directory
├── audio/           # Downloaded audio files
├── transcripts/     # Whisper transcription results
├── summaries/       # LLM-processed content
└── feeds.json      # Podcast feed tracking
```

## Key Technologies

- **OpenAI Whisper**: Local speech-to-text transcription
- **WhisperX**: Enhanced Whisper with speaker diarization and improved alignment
- **pyannote.audio**: State-of-the-art speaker diarization
- **OpenAI GPT-4**: Text processing, summarization, and analysis
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

## Speaker Diarization (User Story 1.4)

### Setup Requirements

1. **Install Dependencies**
   ```bash
   pip install -e .
   ```

2. **Get HuggingFace Token** (for speaker diarization)
   - Create account at https://huggingface.co
   - Get token from https://huggingface.co/settings/tokens
   - Accept model license at https://huggingface.co/pyannote/speaker-diarization-3.1

3. **Configure Environment**
   ```bash
   # Add to .env
   ENABLE_DIARIZATION=true
   HUGGINGFACE_TOKEN=your_token_here
   ```

### Usage

**WhisperXTranscriber** automatically:
1. Transcribes audio with WhisperX (improved alignment)
2. Aligns output for accurate word-level timestamps
3. Runs speaker diarization if enabled
4. Assigns speaker labels to segments
5. Falls back to standard Whisper if diarization fails

**Output Format:**
```
[00:15] [SPEAKER_00] Welcome to the podcast.
[00:18] [SPEAKER_01] Thanks for having me.
```

**Configuration Options:**
- `ENABLE_DIARIZATION`: Enable/disable speaker identification
- `MIN_SPEAKERS`: Minimum speakers (empty = auto-detect)
- `MAX_SPEAKERS`: Maximum speakers (empty = auto-detect)
- `DIARIZATION_MODEL`: pyannote model name

**Error Handling:**
- Missing HuggingFace token → Diarization disabled, standard transcription
- Poor audio quality → Diarization may fail, continues without speakers
- Model download failure → Falls back to standard Whisper
- Any error → Graceful degradation to WhisperTranscriber