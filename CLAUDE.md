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
# Add podcast feed
thestill add "https://example.com/rss"

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
   - Stores podcast metadata and processing status
   - Identifies new episodes since last run

2. **Audio Downloader** (`thestill/core/audio_downloader.py`)
   - Downloads podcast audio files from RSS feeds
   - Handles various audio formats (MP3, M4A, etc.)
   - Manages file cleanup and storage

3. **Transcriber** (`thestill/core/transcriber.py`)
   - Uses OpenAI Whisper for speech-to-text
   - Supports speaker diarization and timestamps
   - Configurable model sizes (tiny, base, small, medium, large)

4. **LLM Processor** (`thestill/core/llm_processor.py`)
   - Three-step LLM pipeline:
     - Cleans transcripts and detects ads
     - Generates comprehensive summaries
     - Extracts notable quotes with analysis

5. **Models** (`thestill/models/podcast.py`)
   - Pydantic models for type safety
   - Episode, Podcast, Quote, and ProcessedContent schemas

### Configuration System

- Environment-based configuration with `.env` support
- Configurable storage paths, model choices, and processing parameters
- See `thestill/utils/config.py` for all options

### Data Flow

1. RSS feeds are checked for new episodes
2. Audio files are downloaded to `data/audio/`
3. Whisper transcribes audio to structured JSON in `data/transcripts/`
4. LLM processes transcripts and saves summaries to `data/summaries/`
5. Episode status is updated in `data/feeds.json`

## File Structure

```
thestill/
├── core/              # Core processing modules
│   ├── feed_manager.py
│   ├── audio_downloader.py
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
- **OpenAI GPT-4**: Text processing, summarization, and analysis
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