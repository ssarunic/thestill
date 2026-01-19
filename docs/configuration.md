# Configuration

All configuration is done via environment variables, typically stored in a `.env` file.

## Setup

```bash
cp .env.example .env
# Edit .env with your settings
```

## Core Settings

| Variable | Description | Default |
|----------|-------------|---------|
| `STORAGE_PATH` | Base directory for all data | `./data` |
| `DATABASE_PATH` | SQLite database location | `{STORAGE_PATH}/podcasts.db` |

## Transcription Provider

| Variable | Description | Default |
|----------|-------------|---------|
| `TRANSCRIPTION_PROVIDER` | Provider to use: `whisper`, `parakeet`, `google`, `elevenlabs` | `whisper` |
| `WHISPER_MODEL` | Whisper model size: `tiny`, `base`, `small`, `medium`, `large` | `base` |
| `WHISPER_DEVICE` | Device for inference: `auto`, `cpu`, `cuda` | `auto` |
| `ENABLE_DIARIZATION` | Enable speaker identification | `false` |
| `HUGGINGFACE_TOKEN` | Token for pyannote.audio (Whisper diarization) | - |
| `MIN_SPEAKERS` | Minimum speakers (leave empty for auto) | - |
| `MAX_SPEAKERS` | Maximum speakers (leave empty for auto) | - |

See [transcription-providers.md](transcription-providers.md) for provider-specific setup.

## Google Cloud (for Google transcription)

| Variable | Description | Default |
|----------|-------------|---------|
| `GOOGLE_APP_CREDENTIALS` | Path to service account JSON key | - |
| `GOOGLE_CLOUD_PROJECT_ID` | GCP project ID | - |
| `GOOGLE_STORAGE_BUCKET` | GCS bucket for large files (>10MB) | - |

## ElevenLabs

| Variable | Description | Default |
|----------|-------------|---------|
| `ELEVENLABS_API_KEY` | ElevenLabs API key | - |
| `ELEVENLABS_MODEL` | Model: `scribe_v1`, `scribe_v1_experimental` | `scribe_v1` |
| `ELEVENLABS_WEBHOOK_SECRET` | Webhook signature verification | - |
| `ELEVENLABS_WEBHOOK_REQUIRE_METADATA` | Require episode_id in webhook | `true` |

## LLM Providers (for cleaning/summarization)

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENAI_API_KEY` | OpenAI API key | - |
| `ANTHROPIC_API_KEY` | Anthropic API key | - |
| `GOOGLE_API_KEY` | Google Gemini API key | - |
| `MISTRAL_API_KEY` | Mistral AI API key | - |
| `OLLAMA_BASE_URL` | Ollama server URL | `http://localhost:11434` |

## Episode Management

| Variable | Description | Default |
|----------|-------------|---------|
| `MAX_EPISODES_PER_PODCAST` | Limit episodes tracked per podcast | - (no limit) |
| `DELETE_AUDIO_AFTER_PROCESSING` | Delete audio after each stage | `false` |

### MAX_EPISODES_PER_PODCAST

Prevents database from becoming unmanageable for podcasts with hundreds of episodes:

- Only the N most recent episodes (by `pub_date`) are kept per podcast
- Already-processed episodes are never removed, even if total exceeds limit
- New unprocessed episodes fill available slots up to the limit
- Applied during `thestill refresh` command
- Override per-run: `thestill refresh --max-episodes 10`

**Example**: With limit of 50 and podcast has 200 episodes:

- First refresh: Discovers 50 most recent episodes
- After processing 10: Next refresh keeps those 10 processed + 40 most recent unprocessed
- Result: Always stays at ≤50 total episodes per podcast

### DELETE_AUDIO_AFTER_PROCESSING

Saves disk space by removing intermediate audio files:

- After successful **downsampling**: Deletes the original audio file (MP3/M4A)
- After successful **transcription**: Deletes the downsampled audio file (WAV)
- Database path fields are cleared to indicate files no longer exist
- Episode state is preserved (determined by furthest completed stage)

**Important**: Once deleted, audio files must be re-downloaded to re-process.

## Transcript Cleaning

| Variable | Description | Default |
|----------|-------------|---------|
| `ENABLE_TRANSCRIPT_CLEANING` | Enable LLM cleaning | `true` |
| `CLEANING_PROVIDER` | Provider: `ollama`, `openai`, etc. | `ollama` |
| `CLEANING_MODEL` | Model to use | `gemma3:4b` |
| `CLEANING_CHUNK_SIZE` | Max tokens per chunk | `20000` |
| `CLEANING_OVERLAP_PCT` | Overlap between chunks | `0.15` |
| `CLEANING_EXTRACT_ENTITIES` | Extract names/terms for consistency | `true` |

See [transcript-cleaning.md](transcript-cleaning.md) for details.

## Web Server

| Variable | Description | Default |
|----------|-------------|---------|
| `HOST` | Server bind address | `127.0.0.1` |
| `PORT` | Server port | `8000` |

## MCP Server

| Variable | Description | Default |
|----------|-------------|---------|
| `MCP_LOG_LEVEL` | Logging level | `INFO` |

## Configuration Hierarchy

1. Environment variables (highest priority)
2. `.env` file
3. Code defaults (lowest priority)

## Example .env

```bash
# Storage
STORAGE_PATH=./data

# Transcription
TRANSCRIPTION_PROVIDER=google
GOOGLE_APP_CREDENTIALS=/path/to/credentials.json
GOOGLE_CLOUD_PROJECT_ID=my-project
ENABLE_DIARIZATION=true

# LLM (for cleaning/summarization)
OPENAI_API_KEY=sk-...

# Episode management
MAX_EPISODES_PER_PODCAST=50
DELETE_AUDIO_AFTER_PROCESSING=false

# Cleaning
ENABLE_TRANSCRIPT_CLEANING=true
CLEANING_PROVIDER=openai
CLEANING_MODEL=gpt-4o-mini
```
