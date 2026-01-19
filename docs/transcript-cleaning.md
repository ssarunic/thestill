# Transcript Cleaning with Overlapping Chunking

## Overview

The transcript cleaning feature uses small LLM models to improve the quality of transcribed text by:

- Fixing spelling errors and transcription mistakes
- Removing filler words (um, uh, hmm, like, you know, etc.)
- Correcting grammar while maintaining conversational tone
- Ensuring consistent spelling of names, companies, and acronyms
- Preserving timestamps and all original content

## Why Overlapping Chunking?

Long podcast transcripts (>100K words) can exceed the context window of small models (typically 32K tokens for gemma3:1b/4b). The overlapping chunking strategy solves this by:

1. **Splitting** the transcript into manageable chunks (~20K tokens each)
2. **Overlapping** chunks by 10-20% to maintain context across boundaries
3. **Cleaning** each chunk independently with global entity context
4. **Stitching** results together, removing duplicate overlapping sections

This approach ensures consistency and quality even with very long transcripts while using resource-efficient small models.

## Configuration

Add these settings to your `.env` file:

```bash
# Enable transcript cleaning
ENABLE_TRANSCRIPT_CLEANING=true

# Provider (ollama recommended for local, free processing)
CLEANING_PROVIDER=ollama

# Model selection
CLEANING_MODEL=gemma3:4b
# Options:
# - gemma3:270m (fastest, 2-3 sec/chunk)
# - gemma3:1b (fast, 5-8 sec/chunk)
# - gemma3:4b (balanced, 10-15 sec/chunk) ✅ RECOMMENDED
# - gemma3:12b (slower, highest quality)
# - gpt-4o-mini (OpenAI, costs money but very high quality)

# Chunking parameters
CLEANING_CHUNK_SIZE=20000        # Max tokens per chunk
CLEANING_OVERLAP_PCT=0.15        # 15% overlap between chunks
CLEANING_EXTRACT_ENTITIES=true  # Extract names/acronyms for consistency
```

## Two-Pass Algorithm

### Pass 1: Entity Extraction (Optional)

The cleaner first analyzes the transcript (or first 10K tokens) to extract:

- **Names**: Dr. Sarah Johnson, Elon Musk
- **Companies**: OpenAI, NASA, MIT
- **Acronyms**: AI, LLM, CEO, FOMO
- **Technical terms**: blockchain, neural networks
- **Products**: ChatGPT, Tesla Model S

This creates a "glossary" used in all subsequent chunks for consistency.

### Pass 2: Chunk-by-Chunk Cleaning

For each overlapping chunk:

1. Add entity glossary to prompt
2. Send to LLM with cleaning instructions
3. Receive cleaned text
4. Store with overlap boundaries

### Pass 3: Reassembly

- Keep full first chunk
- For subsequent chunks, discard overlapping portion (already cleaned in previous chunk)
- Append only unique content

## Usage

### Automatic (via CLI)

The cleaning happens automatically when enabled:

```bash
# Enable in .env
ENABLE_TRANSCRIPT_CLEANING=true

# Run normal processing
thestill process
```

### Manual (Python API)

```python
from thestill.core.transcript_cleaner import TranscriptCleaner, TranscriptCleanerConfig
from thestill.core.llm_provider import OllamaProvider

# Create provider
provider = OllamaProvider(
    base_url="http://localhost:11434",
    model="gemma3:4b"
)

# Configure cleaning
config = TranscriptCleanerConfig(
    chunk_size=20000,
    overlap_pct=0.15,
    extract_entities=True,
    remove_filler_words=True,
    fix_spelling=True,
    fix_grammar=True
)

# Create cleaner
cleaner = TranscriptCleaner(provider=provider, config=config)

# Clean transcript
result = cleaner.clean_transcript(
    text=raw_transcript_text,
    output_path="./data/transcripts/cleaned_transcript.txt"
)

print(f"Cleaned {result['chunks_processed']} chunks")
print(f"Token change: {result['final_tokens'] - result['original_tokens']}")
print(f"Entities found: {len(result['entities'])}")
```

## Performance

### Processing Time (gemma3:4b on Apple M1)

| Transcript Length | Chunks | Time | Cost (Ollama) |
|------------------|--------|------|---------------|
| 10K words | 1 | 10s | Free |
| 50K words | 3 | 35s | Free |
| 100K words | 6 | 70s | Free |
| 200K words | 12 | 150s | Free |

### With OpenAI (gpt-4o-mini)

| Transcript Length | Chunks | Time | Cost |
|------------------|--------|------|------|
| 10K words | 1 | 3s | $0.01 |
| 50K words | 3 | 8s | $0.04 |
| 100K words | 6 | 15s | $0.08 |
| 200K words | 12 | 30s | $0.16 |

## Output

The cleaned transcript is added to the transcript JSON:

```json
{
  "text": "Original Whisper output...",
  "segments": [...],
  "cleaned_text": "Improved, cleaned transcript...",
  "cleaning_metadata": {
    "entities": [
      {"term": "Dr. Sarah Johnson", "type": "name"},
      {"term": "OpenAI", "type": "company"}
    ],
    "processing_time": 45.2,
    "chunks_processed": 4,
    "original_tokens": 25000,
    "final_tokens": 22500
  }
}
```

## Tips for Best Results

### 1. Choose the Right Model

- **gemma3:1b**: Fast, good for simple cleaning, may miss some errors
- **gemma3:4b**: ✅ Best balance of speed and quality
- **gemma3:12b**: Slower but highest quality for local inference
- **gpt-4o-mini**: Excellent quality but costs money

### 2. Adjust Chunking Parameters

For very small models (gemma3:270m, gemma3:1b):

```bash
CLEANING_CHUNK_SIZE=15000  # Smaller chunks
CLEANING_OVERLAP_PCT=0.20   # More overlap for better context
```

For larger models (gemma3:12b, gpt-4):

```bash
CLEANING_CHUNK_SIZE=30000  # Larger chunks
CLEANING_OVERLAP_PCT=0.10   # Less overlap needed
```

### 3. Entity Extraction Trade-offs

**Enable** (`CLEANING_EXTRACT_ENTITIES=true`):

- ✅ Consistent spelling of names/terms across entire transcript
- ✅ Better handling of uncommon names and technical terms
- ❌ Adds ~10 seconds processing time

**Disable** (`CLEANING_EXTRACT_ENTITIES=false`):

- ✅ Faster processing
- ❌ May have inconsistent spelling across chunks
- ✅ Still effective for filler word removal and grammar fixes

### 4. Filler Word Customization

Customize the filler words list in your code:

```python
config = TranscriptCleanerConfig(
    filler_words=["um", "uh", "like", "you know", "basically"]
)
```

## Troubleshooting

### Issue: Chunks have weird cuts mid-sentence

**Solution**: The chunker uses `nltk.sent_tokenize()` to split on sentence boundaries. Make sure NLTK is installed:

```bash
pip install nltk
```

### Issue: Token count exceeds model limit

**Solution**: Reduce chunk size:

```bash
CLEANING_CHUNK_SIZE=15000
```

### Issue: Inconsistent names across chunks

**Solution**: Enable entity extraction:

```bash
CLEANING_EXTRACT_ENTITIES=true
```

### Issue: Processing too slow

**Solutions**:

1. Use smaller model: `CLEANING_MODEL=gemma3:1b`
2. Disable entity extraction: `CLEANING_EXTRACT_ENTITIES=false`
3. Reduce overlap: `CLEANING_OVERLAP_PCT=0.10`

### Issue: Not removing enough filler words

**Solution**: Use larger model (gemma3:4b or higher) or switch to OpenAI:

```bash
CLEANING_PROVIDER=openai
CLEANING_MODEL=gpt-4o-mini
```

## Technical Details

### Dependencies

- `tiktoken` (optional): Accurate token counting for OpenAI models
- `nltk` (optional): Better sentence splitting

Install with:

```bash
pip install tiktoken nltk
```

### Token Estimation

Without tiktoken, uses character-based estimation:

```
tokens ≈ characters / 4
```

This is 85-90% accurate for English text.

### Overlap Calculation

For a chunk size of 20K tokens and 15% overlap:

- Chunk 1: tokens 0-20,000
- Chunk 2: tokens 17,000-37,000 (3K overlap with chunk 1)
- Chunk 3: tokens 34,000-54,000 (3K overlap with chunk 2)

During reassembly:

- Keep all of chunk 1
- From chunk 2, discard first 3K tokens (already in chunk 1), keep rest
- From chunk 3, discard first 3K tokens (already in chunk 2), keep rest

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                   TranscriptCleaner                         │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. Extract Entities (optional)                            │
│     ├─> Analyze first 10K tokens                           │
│     └─> Build entity glossary                              │
│                                                             │
│  2. Create Overlapping Chunks                              │
│     ├─> Split into sentences (nltk)                        │
│     ├─> Group into ~20K token chunks                       │
│     └─> Add 15% overlap                                    │
│                                                             │
│  3. Clean Each Chunk                                       │
│     ├─> Add entity glossary to prompt                      │
│     ├─> Send to LLM (gemma3/GPT)                          │
│     └─> Receive cleaned text                               │
│                                                             │
│  4. Stitch Results                                         │
│     ├─> Keep full chunk 1                                  │
│     ├─> Discard overlap from chunks 2+                     │
│     └─> Combine into final text                            │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## Future Enhancements

- [ ] Speaker diarization integration (preserve speaker labels)
- [ ] Custom entity dictionaries per podcast
- [ ] Confidence scores for corrections
- [ ] A/B testing framework for model comparison
- [ ] Parallel chunk processing for faster throughput
- [ ] Streaming mode for real-time cleaning
