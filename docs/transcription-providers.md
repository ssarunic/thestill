# Transcription Providers

This guide covers setup and configuration for all supported transcription providers.

## Provider Comparison

| Feature | Whisper (Local) | Parakeet (Local) | Google Cloud | ElevenLabs |
|---------|----------------|------------------|--------------|------------|
| **Cost** | Free (local CPU/GPU) | Free (local CPU/GPU) | ~$0.024-0.048/min | Per audio hour |
| **Privacy** | Fully local | Fully local | Audio sent to Google | Audio sent to ElevenLabs |
| **Speed** | Depends on hardware | Fast on GPU | Fast (cloud) | Fast (cloud) |
| **Diarization** | Requires HuggingFace + pyannote | Not supported | Built-in | Built-in |
| **Accuracy** | Good | Good | Excellent | Excellent |
| **Languages** | Multi-language | English only | Multi-language | Multi-language |
| **Max Speakers** | Depends on model | N/A | Varies | 32 |
| **Max File Size** | Memory limited | Memory limited | Chunked via GCS | 2GB |
| **Network** | Not required | Not required | Required | Required |

## Whisper (Local Transcription)

### Basic Setup

```bash
# Install dependencies
pip install -e .

# Configure .env
TRANSCRIPTION_PROVIDER=whisper
WHISPER_MODEL=base  # Options: tiny, base, small, medium, large
```

### With Speaker Diarization

1. **Get HuggingFace Token**:
   - Create account at <https://huggingface.co>
   - Get token from <https://huggingface.co/settings/tokens>
   - Accept model license at <https://huggingface.co/pyannote/speaker-diarization-3.1>

2. **Configure .env**:

   ```bash
   ENABLE_DIARIZATION=true
   HUGGINGFACE_TOKEN=your_token_here
   MIN_SPEAKERS=  # Optional: minimum speakers (auto-detect if empty)
   MAX_SPEAKERS=  # Optional: maximum speakers (auto-detect if empty)
   ```

### How it works

WhisperXTranscriber with diarization enabled:

1. Transcribes audio with WhisperX (improved alignment)
2. Aligns output for accurate word-level timestamps
3. Runs pyannote.audio speaker diarization
4. Assigns speaker labels to segments
5. Falls back to standard Whisper if diarization fails

## NVIDIA Parakeet (Local Transcription)

### Setup

```bash
# Install dependencies
pip install -e .
pip install transformers librosa

# Configure .env
TRANSCRIPTION_PROVIDER=parakeet
WHISPER_DEVICE=auto  # Options: auto, cpu, cuda
```

### How it works

- Uses NVIDIA's Parakeet TDT 1.1B model via HuggingFace Transformers
- English-only (language parameter is ignored)
- No speaker diarization support
- No word-level timestamps
- Processes audio in 30-second chunks for long files

### Limitations

- English only
- No speaker diarization
- No custom prompts (API parameters accepted for compatibility but ignored)
- No word-level timestamps

## Google Cloud Speech-to-Text (Cloud Transcription)

### Setup

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

### How it works

- Files <10MB: Synchronous transcription (fast)
- Files >10MB: Async transcription via Google Cloud Storage
- Language automatically detected from podcast RSS metadata
- Built-in speaker diarization (no additional setup required)

### Pricing

- Standard recognition: ~$0.024/minute
- With speaker diarization: ~$0.048/minute
- See: <https://cloud.google.com/speech-to-text/pricing>

## ElevenLabs Speech-to-Text (Cloud Transcription)

### Setup

1. Create ElevenLabs account at <https://elevenlabs.io>
2. Get API key from <https://elevenlabs.io/app/settings/api-keys>
3. Configure .env:

   ```bash
   TRANSCRIPTION_PROVIDER=elevenlabs
   ELEVENLABS_API_KEY=your-api-key-here
   ELEVENLABS_MODEL=scribe_v1  # Options: scribe_v1, scribe_v1_experimental
   ENABLE_DIARIZATION=true  # Built-in diarization (up to 32 speakers)
   ```

### How it works

- Uses Scribe v1 model for high-accuracy transcription
- Supports files up to 2GB
- Built-in speaker diarization (up to 32 speakers)
- Word-level timestamps
- Language auto-detection (or specify with language code)
- Optional audio event detection (laughter, applause, etc.)

### Pricing

- See: <https://elevenlabs.io/pricing>
- Billed per audio hour

## Common Configuration

### Transcript Output Format (all providers)

```
[00:15] [SPEAKER_01] Welcome to the podcast.
[00:18] [SPEAKER_02] Thanks for having me.
```

### Configuration Options (all providers)

- `ENABLE_DIARIZATION`: Enable/disable speaker identification
- `MIN_SPEAKERS`: Minimum speakers (leave empty for auto-detect)
- `MAX_SPEAKERS`: Maximum speakers (leave empty for auto-detect)

### Recommendation

Leave `MIN_SPEAKERS` and `MAX_SPEAKERS` empty for most podcasts. Both Google and Whisper/pyannote have sensible internal defaults and auto-detection works well. Only set explicit values if you know the exact speaker count (e.g., solo show, fixed two-host format).
