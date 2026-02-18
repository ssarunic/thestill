# thestill

Turn podcasts into readable content. Automatically.

## The Problem

You subscribe to 20 podcasts. Each episode is 60-90 minutes. That's 30+ hours of content per week. You'll never catch up by listening.

## The Solution

thestill downloads your podcasts, transcribes them with speaker identification, cleans up the messy speech-to-text output, and generates summaries. You get a morning briefing of everything that dropped overnight.

```bash
# Add some podcasts
thestill add "https://lexfridman.com/feed/podcast/"
thestill add "https://www.youtube.com/@hubermanlab"

# Generate your morning briefing
thestill digest

# Or run the web UI
thestill server
```

That's it. New episodes are discovered, downloaded, transcribed, cleaned, and summarized. You read instead of listen.

Even better - talk to your podcasts through Claude Desktop:

- *"Generate 3 LinkedIn posts from the latest Lenny's Podcast episode"*
- *"What themes keep coming up in the last 10 Prof G episodes?"*
- *"Find every mention of 'product-market fit' across all my podcasts"*

Your podcasts become a searchable, queryable knowledge base.

![Dashboard](docs/images/dashboard.jpeg)

## How It Works

**Six atomic steps, mix and match as you like:**

```
Discover → Download → Downsample → Transcribe → Clean → Summarize
```

Each step is independent. Failed at transcription? Fix it and continue - no need to re-download. Want cloud transcription but local LLM? Go for it.

**Transcription** - Pick your engine:

- Whisper (local, free, private)
- Google Cloud Speech-to-Text (fast, accurate)
- ElevenLabs Scribe (great quality)
- Dalston (self-hosted)

**LLM Processing** - Pick your brain:

- OpenAI GPT
- Anthropic Claude
- Google Gemini
- Mistral
- Ollama (local, free)

**Sources** - Works with:

- RSS feeds
- Apple Podcasts
- YouTube channels & playlists

## Quick Start

```bash
# Install
git clone https://github.com/ssarunic/thestill.git
cd thestill
pip install -e .

# Configure (edit .env with your API keys)
cp .env.example .env

# Add a podcast and process it
thestill add "https://example.com/podcast/rss"
thestill digest
```

## Features

- **Web UI** - React dashboard for managing podcasts, viewing transcripts, monitoring queue
- **Speaker Diarization** - Know who said what in multi-person conversations
- **Morning Briefing** - Daily digest of new episodes with summaries
- **MCP Server** - Natural language access to your podcast library via Claude Desktop
- **Multi-user Auth** - Google OAuth for hosted deployments
- **Failure Handling** - Automatic retries, dead letter queue for manual review

## Documentation

| Guide | Description |
|-------|-------------|
| [Configuration](docs/configuration.md) | Environment variables and settings |
| [Transcription Providers](docs/transcription-providers.md) | Setup guides for each provider |
| [Web Server](docs/web-server.md) | API endpoints and webhooks |
| [MCP Usage](docs/mcp-usage.md) | Claude Desktop integration |
| [Transcript Cleaning](docs/transcript-cleaning.md) | LLM-based cleanup options |
| [Logging](docs/logging-configuration.md) | Structured logging setup |

## CLI Reference

```bash
# Podcast management
thestill add <url>          # Add podcast (RSS, Apple, YouTube)
thestill list               # List podcasts
thestill remove <id>        # Remove podcast
thestill status             # System stats

# Processing pipeline
thestill refresh            # Discover new episodes
thestill download           # Download audio
thestill downsample         # Convert to 16kHz WAV
thestill transcribe         # Transcribe to JSON
thestill clean-transcript   # Clean with LLM
thestill summarize          # Generate summaries

# Batch processing
thestill digest             # Full pipeline + morning briefing
thestill digest --dry-run   # Preview what would be processed

# Web server
thestill server             # Start on localhost:8000
```

All commands support `--podcast-id`, `--max-episodes`, and `--dry-run`.

## Output

```
data/
├── original_audio/      # Downloaded MP3/M4A files
├── downsampled_audio/   # 16kHz WAV for transcription
├── raw_transcripts/     # JSON with timestamps & speakers
├── clean_transcripts/   # Cleaned Markdown
├── summaries/           # Episode analysis
├── digests/             # Morning briefings
└── podcasts.db          # SQLite database
```

## Development

```bash
pip install -e ".[dev]"   # Install dev dependencies
make test                  # Run tests
make check                 # Lint + typecheck + test
```

See [CLAUDE.md](CLAUDE.md) for architecture details and [docs/code-guidelines.md](docs/code-guidelines.md) for contribution guidelines.

## License

Apache 2.0
