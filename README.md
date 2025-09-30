# thestill.ai

An automated pipeline that converts podcasts into readable, summarized content to help you consume more content in less time.

## Overview

thestill.ai solves the time-consuming nature of audio content consumption by:
- Transcribing podcast episodes using OpenAI Whisper
- Cleaning transcripts and detecting advertisements
- Generating comprehensive summaries
- Extracting notable quotes with analysis
- Processing a 60-minute podcast in under 15 minutes

## Features

- **Multiple Source Support**: Add podcasts from RSS feeds, Apple Podcasts, and YouTube playlists/channels
- **Automated Processing**: Automatically detect and process new episodes
- **High-Quality Transcription**: Uses OpenAI Whisper for accurate speech-to-text
- **AI-Powered Analysis**: GPT cleans transcripts, generates summaries, and extracts insights
- **CLI Interface**: Simple command-line tool for easy automation
- **Speaker Identification**: Distinguishes between different speakers in conversations
- **Ad Detection**: Identifies and tags advertisement segments

## Quick Start

### Installation

```bash
# Clone the repository
git clone <repository-url>
cd thestill

# Install dependencies
pip install -e .

# Set up configuration
cp .env.example .env
# Edit .env and add your OpenAI API key
```

### Configuration

Create a `.env` file with your OpenAI API key:

```env
OPENAI_API_KEY=your_openai_api_key_here
```

### Basic Usage

```bash
# Add a podcast from various sources
thestill add "https://example.com/podcast/rss"                    # RSS feed
thestill add "https://podcasts.apple.com/us/podcast/id123456"    # Apple Podcasts
thestill add "https://www.youtube.com/@channelname"              # YouTube channel
thestill add "https://www.youtube.com/playlist?list=..."         # YouTube playlist

# List tracked podcasts
thestill list

# Process new episodes
thestill process

# Check status
thestill status

# Clean up old files
thestill cleanup
```

## Commands

- `thestill add <url>` - Add a podcast from RSS feed, Apple Podcasts, or YouTube
- `thestill remove <url>` - Remove a podcast feed
- `thestill list` - Show all tracked podcasts
- `thestill process` - Check for and process new episodes
- `thestill process --dry-run` - Show what would be processed
- `thestill status` - Display system status and statistics
- `thestill cleanup` - Remove old audio files

### Supported URL Types

- **RSS Feeds**: Direct podcast RSS feed URLs
- **Apple Podcasts**: `https://podcasts.apple.com/...` or `https://itunes.apple.com/...`
- **YouTube**: Channels (`@username`), playlists, or individual videos

## Output Structure

Processed content is saved in organized directories:

```
data/
├── audio/           # Downloaded podcast audio files
├── transcripts/     # JSON files with full transcripts
├── summaries/       # JSON files with processed content
└── feeds.json       # Tracked podcast feeds
```

Each processed episode generates:
- **Transcript**: Full text with timestamps and speaker identification
- **Summary**: Comprehensive overview of main topics and insights
- **Quotes**: 3-5 notable quotes with significance explanations
- **Ad Detection**: Tagged advertisement segments

## Configuration Options

Environment variables you can customize:

```env
# Storage paths
STORAGE_PATH=./data
AUDIO_PATH=./data/audio
TRANSCRIPTS_PATH=./data/transcripts
SUMMARIES_PATH=./data/summaries

# Processing settings
MAX_WORKERS=3
CHUNK_DURATION_MINUTES=30
CLEANUP_DAYS=30

# Model settings
WHISPER_MODEL=base  # Options: tiny, base, small, medium, large
LLM_MODEL=gpt-4o
```

## System Requirements

- Python 3.9 or higher (3.10+ recommended)
- OpenAI API key
- 4GB+ RAM recommended for Whisper processing
- Internet connection for downloads and API calls
- FFmpeg (required for YouTube audio extraction)

## Cost Estimation

Processing costs depend on episode length and model choices:
- Whisper: Free (runs locally)
- GPT-4: ~$0.01-0.05 per episode (varies by length)
- 60-minute episode: approximately $0.02-0.08

## Troubleshooting

**API Key Issues:**
```bash
export OPENAI_API_KEY="your-key-here"
```

**Download Failures:**
- Check internet connection
- Verify RSS feed URLs are accessible
- Some feeds may require specific user agents
- For YouTube: Ensure FFmpeg is installed (`brew install ffmpeg` on macOS)

**Processing Errors:**
- Ensure sufficient disk space for audio files
- Check OpenAI API quotas and billing
- Monitor system resources during Whisper processing

## Development

```bash
# Install in development mode
pip install -e ".[dev]"

# Run tests
pytest

# Code formatting
black thestill/
isort thestill/
```

## Future Roadmap (v2.0)

- Blog post generation from processed content
- Social media post creation
- Web interface
- Multi-language support
- Direct publishing integrations
- User authentication system

## License

MIT License - See LICENSE file for details.