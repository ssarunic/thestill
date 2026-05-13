# LLM Prohibited-Content Fallback

**Status**: 🚧 Active development (option B shipped; option A still owed)
**Created**: 2026-05-13
**Updated**: 2026-05-13
**Priority**: Low (single-batch impact; only true-crime / edgy-comedy podcasts so far)
**Related**: [18-segment-preserving-transcript-cleaning.md](18-segment-preserving-transcript-cleaning.md)

## Background

Gemini's `FinishReason.PROHIBITED_CONTENT` is a hard, non-bypassable signal:
the model refuses to produce output for content it deems prohibited, even
when `safety_settings` are set to `OFF` for every documented category. We
already turn safety off across the four standard buckets in
[llm_provider.py:2120-2125](../thestill/core/llm_provider.py#L2120-L2125),
but PROHIBITED_CONTENT sits below that gate.

This was first observed on **episode 53712f4c — "Epizoda 218: F/M/K"**
(Croatian true-crime / edgy-comedy podcast *Mjesto Zločina*). The clean
stage ran nine cleanup batches successfully, then the tenth batch (segments
406–424, ~2.4 KB of source text containing slurs and references to
homosexuality / pedophiles / hating immigrant groups) triggered
PROHIBITED_CONTENT. The cleaner had no escape hatch; the task exhausted its
three retries and the episode was marked failed at the `clean` stage.

The structured-output path in `GeminiProvider.generate_structured` was also
flying blind — it never inspected `candidate.finish_reason`, so every
PROHIBITED_CONTENT / SAFETY / RECITATION / MAX_TOKENS condition collapsed
into a single useless `ValueError("Gemini returned empty response")`. The
chat path at
[llm_provider.py:2231-2274](../thestill/core/llm_provider.py#L2231-L2274)
already inspected finish_reason; the structured path did not.

## Intended outcome

A single batch that trips PROHIBITED_CONTENT must not doom the rest of the
episode. The clean stage runs to completion; the tripped batch keeps its raw
ASR text (no speaker mapping, no ad tagging, no filler removal); structured
logs surface the batch range and provider/model so the failure is
attributable.

The exception is **specific** — distinct from `TransientError` (retry won't
help) and `FatalError` (the input is fine; another provider may succeed).
Callers decide recovery.

## Non-goals

- **Per-batch model fallback** (option A below). The "right" answer for
  recurring sensitive content, but needs design work — see Open Questions.
  Pass-through is the no-brainer interim.
- **Disabling Gemini for these podcasts entirely**. Per-podcast LLM routing
  is a different surface; not warranted on a single observation.
- **Surface PROHIBITED_CONTENT in the UI**. The structured warning is enough
  for now; cross-episode prevalence will tell us whether a user-facing
  affordance is justified.

## Shipped (option B — pass-through)

### Exception class

A new `ProhibitedContentError(ThestillError)` in
[thestill/utils/exceptions.py](../thestill/utils/exceptions.py). Carries
`provider`, `model`, and `finish_reason` in its `context` dict. Distinct
from `TransientError` / `FatalError` so the task-worker retry/DLQ machinery
does not catch it accidentally — recovery is the **direct caller's**
responsibility.

### Structured-output path inspects finish_reason

`GeminiProvider.generate_structured` in
[thestill/core/llm_provider.py](../thestill/core/llm_provider.py) now
inspects `candidate.finish_reason` **before** the text-extraction step:

| finish_reason         | Behaviour                                                                  |
|-----------------------|----------------------------------------------------------------------------|
| `PROHIBITED_CONTENT`  | raise `ProhibitedContentError` with `provider` / `model` / `finish_reason` |
| `SAFETY`              | raise `RuntimeError` with `candidate.safety_ratings`                       |
| `RECITATION`          | raise `RuntimeError` if no content; warn + return if partial content       |
| `MAX_TOKENS` (empty)  | raise `RuntimeError` naming the limit                                      |
| empty without reason  | raise `ValueError` that includes `finish_reason` and `prompt_feedback`     |

Mirrors the non-structured chat path at
[llm_provider.py:2231-2274](../thestill/core/llm_provider.py#L2231-L2274).
Other providers (Anthropic, OpenAI, Mistral, Ollama) do not yet raise
`ProhibitedContentError` — they have analogous signals (Anthropic's
`stop_reason="refusal"`, OpenAI's `content_filter`) but no current consumer
needs them. Wire them up when a failure is observed.

### Cleaner pass-through

`SegmentedTranscriptCleaner.clean` in
[thestill/core/segmented_transcript_cleaner.py](../thestill/core/segmented_transcript_cleaner.py)
wraps the per-batch `generate_structured_cached` call in
`try / except ProhibitedContentError`. On catch:

1. Emit a `segmented_cleanup_prohibited_content` warning with
   `episode_id`, batch start/end, target segment count, target chars, and
   the provider/model/finish_reason from the exception context.
2. Set `patched = list(target)` — the source segments pass through with
   their original ASR text, `kind="content"` defaults, no sponsor tags.
3. Continue the loop. Subsequent batches still run normally.

No retry of the failed batch with different parameters — pass-through is
deterministic and lets the rest of the episode produce its usable artefact.

### Verification

- [scripts/diag_gemini_clean_218.py](../scripts/diag_gemini_clean_218.py)
  — monkey-patches `generate_structured` with diagnostic logging, captures
  the full Gemini response metadata (`finish_reason`, `safety_ratings`,
  `prompt_feedback`, `usage_metadata`) per batch. Use to characterise any
  future "Gemini returned empty response" failure.
- End-to-end re-run of episode 218 with the fix in place produces a 45 KB
  cleaned-Markdown artefact; the warning fires exactly once for the
  affected batch.

## Owed (option A — per-batch model fallback)

The pass-through batch carries raw ASR text — punctuation drift, speaker
labels still as `SPEAKER_XX`, no ad detection, no filler removal. For shows
that hit PROHIBITED_CONTENT recurrently this is a quality cliff. The right
answer is to retry the offending batch against a **different** provider
that will accept the content — usually Anthropic or Mistral.

### Design sketch

- `SegmentedTranscriptCleaner` grows a `fallback_provider: Optional[LLMProvider]`
  constructor arg (default `None`).
- On `ProhibitedContentError`, if `fallback_provider` is set, re-issue the
  batch against it with the same prompt; only fall through to pass-through
  if the fallback also refuses or errors.
- `TranscriptCleaningProcessor.clean_transcript` constructs the fallback
  from config: a separate `LLM_CLEAN_FALLBACK_PROVIDER` /
  `LLM_CLEAN_FALLBACK_MODEL` pair (or reuse the existing Anthropic /
  Mistral configs when present, picking the first non-Gemini provider with
  credentials).
- Add a per-batch metric / log line so we can measure how often fallback
  actually fires before promoting this to "always-on".

### Open questions

1. **Prompt caching across providers**. Each provider has its own caching
   semantics; the batch-shaped prompt was tuned for Gemini's implicit cache
   - Anthropic's `cache_control` markers. A fallback call burns cache by
   definition. Acceptable for the rare-event case; need to confirm before
   making fallback default-on.
2. **Sponsor tagging consistency**. If batch N's ads were tagged by Claude
   while batch N±1's were tagged by Gemini, do we ever see split ad-break
   boundaries? Likely not — ad spans rarely span a batch boundary — but
   worth checking on the eval harness.
3. **Cost telemetry**. Fallback batches are 5–20× the marginal cost of the
   primary path. We don't currently track per-batch cost. Solve it when
   per-episode cost reporting (an open item on spec #18) lands.
4. **Symmetric error class for Anthropic / OpenAI**. Once a non-Gemini
   provider is plumbed as a fallback, we'll want its analogous refusal
   signal to surface as `ProhibitedContentError` too — otherwise the
   fallback's refusals collapse into generic errors. Wire when needed.

## Watch list

A `segmented_cleanup_prohibited_content` warning in production logs should
be treated as a quality signal, not a failure. If we see it firing on:

- More than ~5% of cleaned episodes → promote option A to active work.
- A single podcast feed repeatedly → consider a per-podcast LLM override
  before the global fallback.
- Multiple batches in the same episode → pass-through is no longer a
  cosmetic loss; ship option A.

Until any of those trip, B is sufficient.
