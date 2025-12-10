# thestill.ai

An automated pipeline that converts podcasts into readable, summarized content to help you consume more content in less time.

## 📚 Documentation

- **[CLAUDE.md](CLAUDE.md)** - Project overview, architecture, and development commands
- **[CODE_GUIDELINES.md](docs/CODE_GUIDELINES.md)** - Coding standards, best practices, and development workflow
- **[MCP_USAGE.md](docs/MCP_USAGE.md)** - MCP server setup for Claude Desktop, ChatGPT Desktop, and other clients
- **[TRANSCRIPT_CLEANING.md](docs/TRANSCRIPT_CLEANING.md)** - Transcript cleaning configuration and usage

## Overview

thestill.ai is a production-ready podcast transcription and analysis pipeline that converts audio podcasts into clean, readable transcripts with speaker identification. Built with Python and designed for both local and cloud processing.

**Key Capabilities:**

- **Transcription**: OpenAI Whisper (local) or Google Cloud Speech-to-Text (cloud)
- **Speaker Diarization**: Automatic speaker identification in multi-person conversations
- **Transcript Cleaning**: LLM-powered correction of errors, removal of filler words and ads
- **Multiple Sources**: RSS feeds, Apple Podcasts, YouTube channels/playlists
- **Flexible LLM Backend**: OpenAI, Ollama (local), Google Gemini, or Anthropic Claude
- **SQLite Storage**: Fast indexed queries with ACID transactions
- **MCP Server**: Integrate with Claude Desktop and other MCP-compatible clients

**Processing Pipeline:**

```text
Refresh → Download → Downsample → Transcribe → Clean → Summarize
```

Each step is atomic and can be run independently for horizontal scaling.

## Features

- **Multiple Podcast Sources**: RSS feeds, Apple Podcasts, YouTube channels/playlists
- **Dual Transcription Modes**:
  - Local Whisper with pyannote.audio diarization (free, private)
  - Google Cloud Speech-to-Text with built-in diarization (fast, accurate)
- **LLM-Powered Cleaning**: Fix transcription errors, remove filler words, identify speakers
- **Facts-Based Processing**: Extract and manage podcast/episode facts for better cleaning
- **Comprehensive Summarization**: Generate executive summaries, quotes, and content analysis
- **Quality Evaluation**: Assess transcript and post-processing quality
- **Multiple LLM Providers**: OpenAI GPT-4, Ollama (local), Google Gemini, Anthropic Claude
- **SQLite Database**: Indexed queries (O(log n)), row-level locking, ACID transactions
- **Atomic Pipeline**: Each processing step is independent and idempotent
- **CLI Interface**: Simple command-line tool with comprehensive options
- **MCP Server**: Natural language interface via Claude Desktop
- **Episode Management**: Configurable limits per podcast to keep data manageable
- **Robust Error Handling**: Retry logic with exponential backoff, structured logging

## Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/ssarunic/thestill.git
cd thestill

# Install dependencies
pip install -e .

# Set up configuration
cp .env.example .env
# Edit .env and add your OpenAI API key
```

### Configuration

Create a `.env` file with your configuration. Choose from multiple LLM providers and transcription services.

#### Transcription Configuration

##### Local Whisper (Free, Private)

```env
TRANSCRIPTION_PROVIDER=whisper
WHISPER_MODEL=base  # Options: tiny, base, small, medium, large
ENABLE_DIARIZATION=true  # Optional: Enable speaker identification
HUGGINGFACE_TOKEN=your_token  # Required for diarization
```

##### Google Cloud Speech-to-Text (Fast, Accurate)

```env
TRANSCRIPTION_PROVIDER=google
GOOGLE_APP_CREDENTIALS=/path/to/service-account-key.json
GOOGLE_CLOUD_PROJECT_ID=your-project-id
ENABLE_DIARIZATION=true  # Built-in, no additional setup needed
```

#### LLM Provider Configuration

##### OpenAI (Cloud)

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_MODEL=gpt-4o
```

##### Ollama (Local, Free)

```env
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=gemma3:4b
```

##### Google Gemini (Fast, Cost-Effective)

```env
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.0-flash-exp
```

##### Anthropic Claude (High Quality)

```env
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=your_anthropic_api_key_here
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022
```

See [.env.example](.env.example) for all available configuration options.

### Basic Usage

The processing pipeline consists of six atomic steps:

```bash
# 1. Add a podcast from various sources
thestill add "https://example.com/podcast/rss"                    # RSS feed
thestill add "https://podcasts.apple.com/us/podcast/id123456"    # Apple Podcasts
thestill add "https://www.youtube.com/@channelname"              # YouTube channel
thestill add "https://www.youtube.com/playlist?list=..."         # YouTube playlist

# 2. Refresh feeds to discover new episodes
thestill refresh
thestill refresh --podcast-id 1            # Refresh specific podcast
thestill refresh --max-episodes 3          # Limit episodes per podcast
thestill refresh --dry-run                 # Preview without changes

# 3. Download audio files
thestill download
thestill download --podcast-id 1           # Download from specific podcast
thestill download --max-episodes 3         # Limit downloads

# 4. Downsample audio (required for Whisper transcription)
thestill downsample
thestill downsample --podcast-id 1         # Downsample specific podcast

# 5. Transcribe audio to text
thestill transcribe
thestill transcribe --podcast-id 1         # Transcribe specific podcast
thestill transcribe --podcast-id 1 --episode-id latest  # Transcribe specific episode
thestill transcribe --dry-run              # Preview without transcribing

# 6. Clean transcripts with LLM (optional)
thestill clean-transcript
thestill clean-transcript --dry-run        # Preview without changes
thestill clean-transcript --max-episodes 5 # Limit cleaning

# 7. Summarize cleaned transcripts (optional)
thestill summarize
thestill summarize --dry-run               # Preview without changes
thestill summarize --max-episodes 3        # Limit summaries

# Management commands
thestill list                              # List tracked podcasts
thestill status                            # Show system status and statistics
thestill cleanup                           # Remove old files

# Start MCP server (for Claude Desktop integration)
thestill-mcp
```

#### Why Separate Steps?

Each step is atomic and idempotent, allowing you to:

- Resume from failures without re-downloading/re-transcribing
- Scale horizontally (e.g., multiple workers per step)
- Mix local and cloud processing (Whisper + Google, OpenAI + Ollama)
- Process episodes incrementally as they arrive

## MCP Server (AI Desktop Integration)

thestill.ai includes an MCP (Model Context Protocol) server that lets you interact with your podcast library through Claude Desktop, ChatGPT Desktop, or other MCP-compatible clients using natural language.

**Supported Clients:**

- Claude Desktop (Anthropic)
- ChatGPT Desktop (OpenAI)
- Any MCP-compatible client

**Example Commands:**

- "What podcasts am I tracking?"
- "Show me the latest episode from The Rest is Politics"
- "Process and summarize the latest episode"
- "Add the Lex Fridman podcast"

For setup instructions and detailed usage, see **[MCP Server Guide](docs/MCP_USAGE.md)**.

## Commands Reference

### Feed Management

- `thestill add <url>` - Add a podcast from RSS, Apple Podcasts, or YouTube
- `thestill remove <podcast_id>` - Remove a podcast by index or URL
- `thestill list` - Show all tracked podcasts with indices

### Processing Pipeline

- `thestill refresh [--podcast-id ID] [--max-episodes N] [--dry-run]` - Discover new episodes
- `thestill download [--podcast-id ID] [--max-episodes N] [--dry-run]` - Download audio files
- `thestill downsample [--podcast-id ID] [--max-episodes N] [--dry-run]` - Convert to 16kHz WAV
- `thestill transcribe [--podcast-id ID] [--episode-id ID] [--max-episodes N] [--dry-run]` - Transcribe audio
- `thestill clean-transcript [--max-episodes N] [--dry-run] [--force]` - Clean with LLM
- `thestill summarize [--max-episodes N] [--dry-run] [--force]` - Generate comprehensive summaries

### Facts Management

- `thestill facts list` - List all facts files (podcast and episode)
- `thestill facts show <podcast_id> [--episode-id ID]` - Show facts for a podcast or episode
- `thestill facts edit <podcast_id> [--episode-id ID]` - Open facts file in $EDITOR
- `thestill facts extract <podcast_id> [--episode-id ID] [--force]` - Extract facts from transcript

### Quality Evaluation

- `thestill evaluate-transcript <transcript_path>` - Evaluate raw transcript quality
- `thestill evaluate-postprocess <processed_path> [--original PATH]` - Evaluate post-processed transcript

### System Management

- `thestill status` - Display system status and episode statistics
- `thestill cleanup [--dry-run]` - Remove old audio files (configurable retention)
- `thestill-mcp` - Start MCP server for Claude Desktop integration

### Supported URL Types

- **RSS Feeds**: Direct podcast RSS feed URLs
- **Apple Podcasts**: `https://podcasts.apple.com/...` or `https://itunes.apple.com/...`
- **YouTube**: Channels (`@username`), playlists, or individual videos

### Common Workflows

#### Process a single podcast end-to-end

```bash
thestill add "https://example.com/podcast/rss"
thestill refresh --podcast-id 1
thestill download --podcast-id 1
thestill downsample --podcast-id 1
thestill transcribe --podcast-id 1
thestill clean-transcript --podcast-id 1  # Optional: clean transcripts
thestill summarize --podcast-id 1         # Optional: generate summaries
```

#### Process all new episodes from all podcasts

```bash
thestill refresh
thestill download
thestill downsample
thestill transcribe
thestill clean-transcript  # Optional
thestill summarize         # Optional
```

#### Preview changes before committing

```bash
thestill refresh --dry-run
thestill download --dry-run
```

## Output Structure

Processed content is saved in organized directories:

```text
data/
├── original_audio/        # Downloaded audio files (MP3, M4A, etc.)
├── downsampled_audio/     # 16kHz WAV files for transcription
├── raw_transcripts/       # JSON transcripts with timestamps and speaker labels
├── clean_transcripts/     # Cleaned Markdown transcripts
├── summaries/             # Episode summaries and analysis
├── podcast_facts/         # Podcast-level facts (hosts, format, etc.)
├── episode_facts/         # Episode-specific facts for cleaning
└── podcasts.db            # SQLite database (metadata and state)
```

### Episode States

Episodes progress through states tracked in `podcasts.db`:

1. **discovered** - Found in RSS feed, has `audio_url`
2. **downloaded** - Audio file downloaded, has `audio_path`
3. **downsampled** - Converted to WAV, has `downsampled_audio_path`
4. **transcribed** - Transcription complete, has `raw_transcript_path`
5. **cleaned** - Transcript cleaned (optional), has `clean_transcript_path`
6. **summarized** - Summary generated (optional), has `summary_path`

### Transcript Format

#### Raw Transcript (JSON)

```json
{
  "segments": [
    {
      "start": 15.2,
      "end": 18.5,
      "text": "Welcome to the podcast.",
      "speaker": "SPEAKER_01"
    }
  ]
}
```

#### Cleaned Transcript (Markdown)

```markdown
[00:15] [Host] Welcome to the podcast.
[00:18] [Guest] Thanks for having me on.
```

## Configuration Options

Key environment variables (see [.env.example](.env.example) for complete list):

### Transcription Settings

```env
TRANSCRIPTION_PROVIDER=whisper     # whisper or google
WHISPER_MODEL=base                 # tiny, base, small, medium, large
ENABLE_DIARIZATION=true            # Enable speaker identification
HUGGINGFACE_TOKEN=your_token       # Required for Whisper diarization
```

### LLM Provider Settings

```env
LLM_PROVIDER=openai                # openai, ollama, gemini, anthropic
OPENAI_MODEL=gpt-4o
OLLAMA_MODEL=gemma3:4b
GEMINI_MODEL=gemini-2.0-flash-exp
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022
```

### Episode Management Settings

```env
MAX_EPISODES_PER_PODCAST=50        # Limit episodes per podcast (optional)
CLEANUP_DAYS=30                    # Delete audio files after N days
```

### Storage Settings

```env
STORAGE_PATH=./data
```

## Ollama Setup

Ollama allows you to run LLM models locally, providing privacy and eliminating API costs for post-processing.

### Ollama Installation

#### macOS

```bash
brew install ollama
```

#### Linux

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

#### Windows

Download from [ollama.com](https://ollama.com/download)

### Running Ollama

1. Start the Ollama server:

   ```bash
   ollama serve
   ```

1. Pull a model (first time only):

   ```bash
   # Recommended models for podcast processing:
   ollama pull gemma3:4b       # Best balance of speed/quality (4B parameters) - recommended
   ollama pull gemma3:27b      # Highest quality (27B parameters)
   ollama pull llama3.2        # Fast, good quality (3B parameters)
   ollama pull mistral         # Alternative balanced option (7B parameters)
   ```

1. Update your `.env` file:

   ```env
   LLM_PROVIDER=ollama
   OLLAMA_MODEL=gemma3:4b
   ```

### Model Recommendations

- **gemma3:4b (4B)**: Best balance of speed/quality, ~3GB RAM (recommended)
- **gemma3:27b (27B)**: Highest quality for complex analysis, ~17GB RAM
- **mistral (7B)**: Alternative balanced option, ~4GB RAM
- **llama3.2 (3B)**: Fastest, good for most podcasts, ~2GB RAM

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

### OpenAI Provider Costs

Processing costs depend on episode length and model choices:

- Whisper: Free (runs locally)
- GPT-4o: ~$0.01-0.05 per episode (varies by length)
- 60-minute episode: approximately $0.02-0.08

### Ollama Provider Costs

- Whisper: Free (runs locally)
- Ollama: Free (runs locally)
- 60-minute episode: $0.00 (only electricity costs)

## Troubleshooting

### OpenAI Provider Issues

#### API Key Issues

```bash
export OPENAI_API_KEY="your-key-here"
```

#### API Quota Errors

- Check OpenAI API quotas and billing
- Consider switching to Ollama for unlimited processing

### Ollama Provider Issues

#### Connection Refused

- Ensure Ollama is running: `ollama serve`
- Check Ollama is accessible at `http://localhost:11434`
- Verify firewall settings

#### Model Not Found

```bash
# List available models
ollama list

# Pull the required model
ollama pull llama3.2
```

#### Slow Processing

- Use smaller models (llama3.2 or gemma3:4b instead of gemma3:27b)
- Ensure sufficient RAM is available
- Close other applications to free up resources
- Consider using OpenAI API for faster processing

#### Out of Memory

- Use a smaller model (llama3.2 ~2GB, gemma3:4b ~3GB, mistral ~4GB, gemma3:27b ~17GB)
- Increase system swap space
- Close other applications
- Process fewer episodes concurrently (reduce MAX_WORKERS)

### Common Issues

#### Download Failures

- Check internet connection
- Verify RSS feed URLs are accessible
- Some feeds may require specific user agents
- For YouTube: Ensure FFmpeg is installed (`brew install ffmpeg` on macOS)

#### Processing Errors

- Ensure sufficient disk space for audio files
- Monitor system resources during Whisper processing

## Development

### Development Setup

```bash
# Clone the repository
git clone https://github.com/ssarunic/thestill.git
cd thestill

# Install with development dependencies
pip install -e ".[dev]"

# Install pre-commit hooks
pre-commit install
```

### Quick Commands (using Makefile)

```bash
make help              # Show all available commands
make install-dev       # Install with dev dependencies + pre-commit hooks
make test              # Run tests with coverage report
make test-fast         # Run tests without coverage (faster)
make lint              # Run pylint + mypy
make format            # Format code with black + isort
make typecheck         # Run mypy type checking
make check             # Run ALL checks (format + lint + typecheck + test)
make clean             # Clean generated files (.pypy_cache, .coverage, etc.)
```

### Testing

#### Run Tests

```bash
# All tests with coverage
pytest --cov=thestill --cov-report=html

# Specific test file
pytest tests/test_path_manager.py -v

# Tests matching pattern
pytest -k "test_download" -v

# Fast run (no coverage)
pytest -v
```

#### Test Structure

```text
tests/
├── test_path_manager.py          # Utils layer (100% coverage)
├── test_podcast_service.py       # Service layer (92% coverage)
├── test_audio_downloader.py      # Core layer (99% coverage)
├── test_feed_manager.py          # Core layer (26% coverage)
├── test_media_source.py          # Core layer (strategy pattern)
├── test_integration_pipeline.py  # End-to-end workflows
└── test_service_contracts.py     # API stability tests
```

**Coverage Target**: 70%+ overall, 90%+ for core modules

### Code Quality

**Pre-commit Hooks** (run automatically on commit):

- `black` - Code formatting
- `isort` - Import sorting
- `pylint` - Linting
- `mypy` - Type checking
- `pytest` - Run test suite

#### Manual Quality Checks

```bash
# Format code
black thestill/ tests/
isort thestill/ tests/

# Run linters
pylint thestill/
mypy thestill/

# Run all checks before pushing
make check
```

### Type Hints

This project maintains 100% type coverage for core and service layers:

```python
from typing import List, Optional

def download_episode(
    self,
    episode: Episode,
    podcast_title: str
) -> Optional[str]:
    """Download audio file for episode"""
    pass
```

#### Run Type Checking

```bash
mypy thestill/          # Check all modules
make typecheck          # Using Makefile
```

### Architecture

The project uses a **layered architecture** with dependency injection:

```text
CLI Layer (cli.py)
  ↓ (dependency injection)
Service Layer (services/)
  ├── PodcastService
  ├── RefreshService
  └── StatsService
  ↓ (orchestration)
Core Layer (core/)
  ├── FeedManager (with transactions)
  ├── MediaSource Strategy (RSS/YouTube)
  ├── AudioDownloader (with retry)
  └── Transcriber (Whisper/Google)
  ↓ (persistence)
Repository Layer (repositories/)
  └── SqlitePodcastRepository (indexed queries, ACID transactions)
  ↓ (data models)
Model Layer (models/podcast.py)
  └── Pydantic models with validation
```

**Key Design Patterns:**

- **Repository Pattern**: Abstract data persistence for easy database migration
- **Strategy Pattern**: MediaSource abstraction for multiple podcast sources
- **Dependency Injection**: Services receive dependencies in constructor
- **Context Manager**: Transaction support for batch operations
- **Single Responsibility**: Each module has one clear purpose

For detailed development guidelines, see [docs/CODE_GUIDELINES.md](docs/CODE_GUIDELINES.md).

## Future Roadmap (v2.0)

- Blog post generation from processed content
- Social media post creation
- Web interface
- Multi-language support
- Direct publishing integrations
- User authentication system

## License

Apache License 2.0
