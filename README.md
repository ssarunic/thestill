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
- **Flexible LLM Backend**: Choose between OpenAI (cloud) or Ollama (local) for post-processing
- **AI-Powered Analysis**: Cleans transcripts, generates summaries, and extracts insights
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

Create a `.env` file with your configuration. You can choose between OpenAI (cloud) or Ollama (local) for LLM processing.

#### Option 1: Using OpenAI (Cloud)

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=your_openai_api_key_here
LLM_MODEL=gpt-4o
```

#### Option 2: Using Ollama (Local)

```env
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2
```

See the [Ollama Setup](#ollama-setup) section below for installation instructions.

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
# LLM Provider (openai or ollama)
LLM_PROVIDER=openai

# OpenAI Configuration (when using LLM_PROVIDER=openai)
OPENAI_API_KEY=your_api_key_here
LLM_MODEL=gpt-4o

# Ollama Configuration (when using LLM_PROVIDER=ollama)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2  # or gemma2, mistral, etc.

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
TRANSCRIPTION_MODEL=whisper  # Options: whisper, parakeet
WHISPER_MODEL=base  # Options: tiny, base, small, medium, large
```

## Ollama Setup

Ollama allows you to run LLM models locally, providing privacy and eliminating API costs for post-processing.

### Installation

**macOS:**
```bash
brew install ollama
```

**Linux:**
```bash
curl -fsSL https://ollama.com/install.sh | sh
```

**Windows:**
Download from [ollama.com](https://ollama.com/download)

### Running Ollama

1. Start the Ollama server:
```bash
ollama serve
```

2. Pull a model (first time only):
```bash
# Recommended models for podcast processing:
ollama pull llama3.2        # Fast, good quality (3B parameters)
ollama pull gemma2:9b       # Better quality, slower (9B parameters)
ollama pull mistral         # Balanced option (7B parameters)
```

3. Update your `.env` file:
```env
LLM_PROVIDER=ollama
OLLAMA_MODEL=llama3.2
```

### Model Recommendations

- **llama3.2 (3B)**: Fast, good for most podcasts, ~2GB RAM
- **gemma2:9b**: Higher quality, better analysis, ~6GB RAM
- **mistral (7B)**: Balanced speed/quality, ~4GB RAM

### Performance Notes

Local inference is slower than OpenAI API but provides:
- Complete privacy (no data leaves your machine)
- No API costs
- No rate limits
- Works offline (after model download)

Expect processing times of 3-10 minutes per podcast depending on model and hardware.

## System Requirements

### For OpenAI Provider
- Python 3.9 or higher (3.10+ recommended)
- OpenAI API key
- 2GB+ RAM
- Internet connection

### For Ollama Provider
- Python 3.9 or higher (3.10+ recommended)
- 4GB+ RAM (8GB+ recommended for larger models)
- Ollama installed and running
- Internet connection for downloads

### Common Requirements
- FFmpeg (required for YouTube audio extraction)
- 2GB+ disk space for audio files

## Cost Estimation

### OpenAI Provider
Processing costs depend on episode length and model choices:
- Whisper: Free (runs locally)
- GPT-4o: ~$0.01-0.05 per episode (varies by length)
- 60-minute episode: approximately $0.02-0.08

### Ollama Provider
- Whisper: Free (runs locally)
- Ollama: Free (runs locally)
- 60-minute episode: $0.00 (only electricity costs)

## Troubleshooting

### OpenAI Provider Issues

**API Key Issues:**
```bash
export OPENAI_API_KEY="your-key-here"
```

**API Quota Errors:**
- Check OpenAI API quotas and billing
- Consider switching to Ollama for unlimited processing

### Ollama Provider Issues

**Connection Refused:**
- Ensure Ollama is running: `ollama serve`
- Check Ollama is accessible at `http://localhost:11434`
- Verify firewall settings

**Model Not Found:**
```bash
# List available models
ollama list

# Pull the required model
ollama pull llama3.2
```

**Slow Processing:**
- Use smaller models (llama3.2 instead of gemma2:9b)
- Ensure sufficient RAM is available
- Close other applications to free up resources
- Consider using OpenAI API for faster processing

**Out of Memory:**
- Use a smaller model (llama3.2 uses ~2GB)
- Increase system swap space
- Close other applications
- Process fewer episodes concurrently (reduce MAX_WORKERS)

### Common Issues

**Download Failures:**
- Check internet connection
- Verify RSS feed URLs are accessible
- Some feeds may require specific user agents
- For YouTube: Ensure FFmpeg is installed (`brew install ffmpeg` on macOS)

**Processing Errors:**
- Ensure sufficient disk space for audio files
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