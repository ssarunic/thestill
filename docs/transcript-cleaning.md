# Transcript Cleaning

## Overview

The transcript cleaning stage uses an LLM to turn raw diarised transcripts into readable, searchable text by:

- Fixing spelling errors and transcription mistakes (homophones, misheard proper nouns, garbled words)
- Replacing `SPEAKER_NN` labels with real speaker names
- Tagging ad breaks with sponsor names (tagging, not redaction — the full ad text is preserved)
- Flagging filler segments (um, uh, you know) so they can be dropped from rendered output
- Tagging intros, outros, and music spans so the UI can toggle their visibility
- Ensuring consistent spelling of names, companies, and acronyms via per-podcast facts
- Preserving timestamps, speaker structure, and all original content

Cleaning is deliberately conservative: the LLM is instructed to keep output 95%+ identical to the input and never paraphrase or "improve" eloquence.

## Two-Pass Facts-Based Pipeline

The entry point is `TranscriptCleaningProcessor` (`thestill/core/transcript_cleaning_processor.py`), which orchestrates two passes.

### Pass 1: Facts Extraction

Before cleaning, the pipeline extracts structured facts from the transcript:

- **Speaker mapping**: `SPEAKER_00` → "Scott Galloway"
- **Guests**: who appears in this episode
- **Keywords / mishearings**: proper nouns and terms the transcriber tends to mangle
- **Ad sponsors**: sponsor names for ad tagging

Facts are stored as human-editable Markdown files:

- Podcast facts: `data/podcast_facts/{podcast_slug}.facts.md`
- Episode facts: `data/episode_facts/{podcast_slug}/{episode_slug}.facts.md`

If the facts files already exist they are reused, so you can correct a wrong speaker mapping by editing the file and re-running with `--force`. Unmapped `SPEAKER_NN` labels that survive to the output are a visible canary that facts extraction missed a speaker.

### Pass 2: Segmented Cleaning

The segmented cleaner (`TranscriptSegmenter` + `SegmentedTranscriptCleaner`) preserves transcript structure instead of rewriting free text:

1. The segmenter converts the raw transcript into a deterministic `AnnotatedTranscript` — a grid of per-speaker segments with stable ids and timing.
2. The cleaner walks the segment list in batches. Each batch is sized greedily up to a character budget (`batch_char_budget`, default 4000 characters; automatically widened 3x for providers without prompt caching).
3. Each LLM call sees a JSON payload with three sections: `k_prev` already-cleaned preceding segments (default 2) for tone and speaker continuity, the target batch to patch, and `k_next` upcoming raw segments (default 2) as read-only forward context.
4. The LLM returns one patch per target segment: corrected text, a segment `kind`, and an optional sponsor name. The patch schema deliberately omits the source anchors (`source_segment_ids`, `source_word_span`), so the LLM physically cannot rewrite them.
5. After all patches apply, segment ids are reassigned positionally (0..N-1) so downstream consumers get a stable grid.

The cacheable system prompt (cleaning rules + facts + speaker mapping + sponsors) is identical across every batch call for an episode, which makes provider prompt caching effective (Anthropic explicit caching, OpenAI/Gemini automatic caching).

### Segment Kinds

Each segment is tagged with a kind; rendering policy lives at the consumer layer:

- `content` — the default narrative bucket
- `filler` — um/uh/you-know noise; dropped from rendered output
- `ad_break` — sponsor reads, tagged with the sponsor name; full ad text is kept
- `music` — theme or interstitial music the transcriber produced text for
- `intro` / `outro` — pre-roll openings and post-roll sign-offs

### Degenerate Transcripts

Transcripts that cannot feed the segmented cleaner (raw JSON that fails schema validation, or transcripts without real per-segment timing) raise `DegenerateTranscriptError` instead of silently degrading. The fix is upstream: re-transcribe with a provider that produces proper per-segment timing.

## Configuration

The `thestill clean-transcript` stage uses the main LLM configuration:

```bash
# Provider for the cleaning stage (openai, ollama, gemini, or anthropic; Mistral also available)
LLM_PROVIDER=gemini

# Model comes from the provider-specific setting, e.g.
GEMINI_MODEL=gemini-3-pro-preview
OLLAMA_MODEL=gemma3:4b
```

A separate set of `CLEANING_*` variables configures the optional inline cleaning that runs during the transcription step (`ENABLE_TRANSCRIPT_CLEANING=true`):

```bash
CLEANING_PROVIDER=gemini                  # Default: gemini
CLEANING_MODEL=gemini-3-flash-preview     # Default: fast and cost-effective
```

### Context Sizing

Chunk size for facts extraction is auto-set from the provider's context window — chunking exists for budget and quality control, not because of tiny context windows:

- Gemini Flash/Pro: 900K characters (~225K tokens from a 1M-token context)
- Claude: 180K characters (~45K tokens from a 200K-token context)
- GPT-4/GPT-5: 100K characters (~25K tokens from a 128K-token context)
- Ollama and others: 30K characters (conservative default)

### Active Tuning Knobs

The segmented cleaner's knobs are code-level defaults on `SegmentedTranscriptCleaner`, not environment variables:

- `k_prev` (default 2): preceding already-cleaned segments included as context
- `k_next` (default 2): upcoming raw segments included as forward context
- `batch_char_budget` (default 4000): target character budget per LLM call, widened 3x for providers without prompt caching

### Legacy Settings

These apply only to the legacy inline-cleaning path during the transcription step, not to `thestill clean-transcript`:

```bash
CLEANING_CHUNK_SIZE=20000        # Legacy: max tokens per chunk
CLEANING_OVERLAP_PCT=0.15        # Legacy: overlap between chunks
CLEANING_EXTRACT_ENTITIES=true   # Legacy: entity extraction for consistency
```

## Usage

Run cleaning as its own pipeline stage:

```bash
thestill clean-transcript
```

Flags:

- `--dry-run` / `-d`: show what would be processed
- `--max-episodes` / `-m`: maximum episodes to process (default: 5)
- `--force` / `-f`: re-process even if a clean transcript exists
- `--stream` / `-s`: stream LLM output in real-time

## Output

Cleaning produces two artifacts per episode:

1. **JSON sidecar** (`*_cleaned.json`): the canonical per-segment `AnnotatedTranscript`. Every segment carries its cleaned text, kind, speaker, timing, and source anchors. All segment kinds are preserved — including full ad text — so consumers filter by kind instead of relying on redaction. The web viewer renders from this when showing the full transcript.
2. **Blended Markdown** (`*_cleaned.md`): an ads-stripped projection of the sidecar, fed to the summariser.

Debug artifacts land in `data/clean_transcripts/debug/`:

- `{base_name}.original.md` — formatted transcript before LLM cleaning (useful for diffing)
- `{base_name}.speakers.json` — speaker mapping from episode facts

## LLM Output Sanitization

LLMs occasionally emit raw control characters — the motivating incident was Gemini returning U+0000 in place of an `é`, which SQLite stored silently and which then propagated invisibly into search chunks. Two guards protect the output:

- **Control-character stripping at the schema boundary**: every patch's `cleaned_text` and `sponsor` pass through a Pydantic validator that strips C0/C1 control characters (tab, newline, and carriage return pass through). Stripping is never silent — it logs a `llm_control_chars_stripped` warning so a provider regression stays visible.
- **Per-batch prohibited-content fallback**: if the provider refuses a batch on content grounds (e.g. Gemini `PROHIBITED_CONTENT`, raised as `ProhibitedContentError`), the batch falls back to its raw ASR text — no speaker mapping, no ad tagging — instead of failing the whole episode. The fallback is logged as `segmented_cleanup_prohibited_content`.

## Tips for Best Results

### Choose the Right Provider

- **Gemini Flash**: fast, cheap, huge context — the recommended default
- **Anthropic / OpenAI**: high quality; prompt caching keeps repeated-prefix costs down
- **Ollama**: free and local, but slower; the batch budget is widened automatically to compensate for the lack of prompt caching

### Fix Speaker Names via Facts

If speakers are mislabelled, edit the episode facts file (`data/episode_facts/{podcast_slug}/{episode_slug}.facts.md`), then re-run:

```bash
thestill clean-transcript --force
```

### Keep Sponsors in Podcast Facts

Ad tagging works best when the podcast facts file lists known sponsors — the LLM populates the `sponsor` field from that list when possible.
