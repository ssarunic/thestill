# Segment-Preserving Transcript Cleaning

**Status**: 🚧 Active development
**Created**: 2026-04-15
**Updated**: 2026-04-15 (review pass: split routing/shadow flags, word
addressing scheme, single `has_usable_segment_structure` predicate,
pause-guarded merge, canonical API shape, three-tier identity model)
**Priority**: High (prerequisite for the richer media player and for future
transcript-editing UX)

## Overview

Today's cleaning pipeline throws away per-segment structure before the LLM ever
sees the transcript. [`TranscriptFormatter._merge_segments_by_speaker`](../thestill/core/transcript_formatter.py#L77-L121)
collapses the raw segment list into a flat run of `(first_start, speaker,
merged_text)` tuples, and [`_build_markdown`](../thestill/core/transcript_formatter.py#L134-L144)
emits it as `[HH:MM:SS] **SPEAKER_XX:** text` lines. From that point on
everything — the LLM cleanup prompt, the summariser, the stored artefact, the
web API, and the frontend — operates on opaque Markdown. Segment IDs, per-word
timestamps, segment end times, and boundaries between merged rows are all lost.
Timestamps survive only as human-readable strings that the frontend regex-parses
back out for display ([`TranscriptViewer.tsx:15-81`](../thestill/web/frontend/src/components/TranscriptViewer.tsx#L15-L81)).

This spec refactors cleaning so that structured segments are the **primary**
artefact and the Markdown becomes a render of that structure. The cleaned form
is a list of segments, each with a stable ID, a `(start, end)` anchored to the
audio, a speaker, the cleaned text, and a pointer back into the raw word
stream. Cleanup operates segment-by-segment (with neighbour context) using
prompt caching, never blending segments into one blob. Facts extraction (Pass 1)
is unchanged in intent but reads from the structured stream.

The direct consumer is a follow-up richer-media-player spec: with structured
segments available on the API, the player can highlight the segment currently
playing, seek to a segment on click, and later enable user-driven segment
merge/split/move operations. This spec only lands the data model, pipeline,
storage, API shape, and a side-by-side debug view — the player itself is out of
scope.

This work effectively replaces the still-unshipped
[spec #08 multi-phase-transcript-cleaning](08-multi-phase-transcript-cleaning.md)
proposal. Spec #08 will be updated to point here; its multi-phase ideas are
absorbed into the execution plan below.

## Goals

1. Cleaning reads raw segments (with word timestamps) as structured data and
   emits an `AnnotatedTranscript` object whose unit is a cleaned segment with
   `(id, start, end, speaker, text, source_segment_ids, source_word_span)`.
   Words are referenced positionally — see §"Word addressing scheme" below —
   because the raw `Word` model has no intrinsic id field.
2. No step in the cleaning pipeline blends segments into a single text blob.
   Blended Markdown exists only as a render of the structured form, for
   download and debugging.
3. Diarisation artefacts are repaired deterministically before LLM cleanup:
   short consecutive same-speaker fragments are merged, and a word-stream-aware
   re-chunker splits overly long single-speaker runs on sentence/paragraph
   boundaries using punctuation and inter-word silence.
4. LLM cleanup runs per segment (or per small batch) with ±N neighbour segments
   as context, plus episode/podcast facts, exploiting provider prompt caching
   on the cached prefix for meaningful token savings.
5. Both the legacy blended Markdown output and the new segmented output are
   produced side-by-side during development so they can be compared visually
   in the web UI and quantitatively via an eval harness.
6. An evaluation harness scores the old and new approaches on the same
   fixture episodes, producing a comparable quality report.
7. The web API exposes structured cleaned segments to the frontend. No player
   wiring yet, just the data plumbing and a debug viewer.
8. The data model and IDs are stable enough that a future spec can add
   user-driven segment merge/split/boundary edits without migration.

## Non-goals

- **The richer media player itself** (click-to-seek, auto-highlight,
  `onTimeUpdate` wiring, word-level karaoke highlighting). Follow-up spec.
- **Summarisation refactor.** The summariser will continue producing
  `[MM:SS]` textual citations from the blended Markdown render for the
  duration of this work. Moving the summariser onto structured segment
  references (so summary citations become stable seek targets) is a
  deliberate follow-up — this spec only guarantees the data model it will
  need.
- **User-facing segment-editing UI** (merge/split/move boundaries). This
  spec sizes the data model to accommodate it but does not ship it.
- **Parakeet word-timestamp fix.** Parakeet currently emits one stub
  `(id=0, start=0, end=0)` segment. That is a transcriber-side bug, not a
  cleanup bug, and is tracked as a separate small task. Until it is fixed,
  cleanup on Parakeet-transcribed episodes degrades gracefully to the legacy
  blended path (see Phase A below).
- **Audio proxying or backend streaming.** The player will continue to point
  at the publisher's MP3 URL. Small alignment offsets between the 16 kHz WAV
  and the publisher MP3 are handled by a per-episode
  `playback_time_offset_seconds` field (default `0.0`), not by re-serving
  audio.
- **Resume-from-midpoint for crashed cleanup runs.** Cleanup is re-runnable
  idempotently from the raw JSON; that is the recovery story.
- **Postgres / schema rewrite.** SQLite remains. We add two columns.

## Background findings

### Raw transcript model — already structured, already sufficient

The canonical transcriber output is a Pydantic model with full per-segment and
per-word detail:

- [`Segment`](../thestill/models/transcript.py#L38-L47) —
  `id: int`, `start: float`, `end: float`, `text: str`, `speaker: Optional[str]`,
  `words: List[Word]`, `confidence: Optional[float]`.
- [`Word`](../thestill/models/transcript.py#L28-L35) —
  `word: str`, `start: Optional[float]`, `end: Optional[float]`,
  `probability: Optional[float]`, `speaker: Optional[str]`.
- [`Transcript`](../thestill/models/transcript.py#L50-L83) —
  serialised with `model_dump()` in [`Transcriber._save_transcript`](../thestill/core/transcriber.py#L77-L88),
  written to `data/raw_transcripts/*.json`.

Whisper, WhisperX, Google Cloud, ElevenLabs and Dalston all populate segments
**and** word timestamps. Parakeet does not; it is the only degenerate case and
is handled by falling back to the legacy blended path.

The model already carries useful helpers that are currently unused by the
cleaning pipeline:

- [`Transcript.get_text_with_timestamps`](../thestill/models/transcript.py#L85-L102) —
  yields per-segment lines with `[MM:SS] [SPEAKER_XX] text` format.
- [`Transcript.get_words_in_range`](../thestill/models/transcript.py#L222-L238) —
  extracts the word stream in a `(start, end)` window.

These will be the foundation for the new `AnnotatedTranscript` builder (see
Phase B) rather than being re-implemented.

### Word addressing scheme — positional, not intrinsic

The raw `Word` model has no id field, so `source_word_ids` as a first-class
concept does not exist and must not be invented. Words are addressed
**positionally** via the tuple `(raw_segment_id, word_index)` where
`word_index` is the zero-based position within `Segment.words`. This is
stable as long as the raw JSON on disk is not rewritten — which it never is;
raw transcripts are write-once.

Each `AnnotatedSegment` therefore carries a single field
`source_word_span: Optional[WordSpan]` where

```python
class WordSpan(BaseModel):
    start_segment_id: int
    start_word_index: int
    end_segment_id: int       # inclusive
    end_word_index: int       # inclusive
```

The span is optional because:

- Some providers emit no word timestamps at all (today: Parakeet — see
  below). Those episodes go down the legacy path anyway and never reach the
  `AnnotatedSegment` construction site, but the field stays optional so
  fixture tests using hand-authored data don't have to fabricate spans.
- A `kind="ad_break"` segment whose raw span is known can still carry it;
  this is useful for the eval harness to measure ad-detection recall.

Word-level highlighting (karaoke-style) in the future player will read the
span, dereference into the raw JSON's `Segment.words` by index, and use the
word-level `start`/`end` timestamps directly.

### Degenerate-transcript detection — single predicate, single helper

There is exactly **one** predicate for "this raw transcript cannot go down
the segmented-cleanup path", and it lives in one helper so the routing
decision is consistent across the pipeline:

```python
def has_usable_segment_structure(transcript: Transcript) -> bool:
    """True iff segmented cleanup can run on this transcript.

    Requires (a) at least one segment whose end > start (rules out stub
    zero-length segments) and (b) at least one word with a non-None start
    (rules out providers that omit word timestamps). A legitimate
    single-segment transcript with real timestamps passes; a Parakeet stub
    with (id=0, start=0, end=0) and no words fails.
    """
    if not transcript.segments:
        return False
    if not any(s.end > s.start for s in transcript.segments):
        return False
    if not any(
        w.start is not None for s in transcript.segments for w in s.words
    ):
        return False
    return True
```

Every call site — Phase A's observability hook, Phase C's routing switch,
and the debug view's "segmented view unavailable" badge — consumes this
helper and nothing else. A one-segment legitimate transcript (e.g. a short
monologue) is valid and cleans segmentally. A Parakeet stub fails and
falls back to the legacy path.

### The destruction point, exact

One function, one call site, destroys structure:

- [`TranscriptFormatter._merge_segments_by_speaker`](../thestill/core/transcript_formatter.py#L77-L121)
  iterates `segments`, reading only `speaker`, `start`, and `text`. `Segment.id`,
  `Segment.end`, `Segment.words`, and `Segment.confidence` are never touched.
  The return value is `List[Tuple[float, str, str]]`.
- [`TranscriptFormatter._build_markdown`](../thestill/core/transcript_formatter.py#L134-L144)
  emits `[HH:MM:SS] **SPEAKER_XX:** text` lines from those tuples. From here
  on the transcript is a string.

The method is called from exactly three places:

1. [`TranscriptCleaningProcessor.clean_transcript:211`](../thestill/core/transcript_cleaning_processor.py#L209-L211) —
   the main cleaning path. This is the call site this spec targets.
2. [`FactsExtractor.extract_episode_facts:181`](../thestill/core/facts_extractor.py#L181) —
   Pass 1, facts extraction, also reads the lossy blob.
3. [`PodcastService.get_transcript:576`](../thestill/services/podcast_service.py#L563-L578) —
   fallback path when a raw transcript exists but no cleaned Markdown does.
   Used purely for display.

All three must move onto the structured form, with the service-layer fallback
becoming a renderer over `AnnotatedTranscript` instead of a parallel formatter.

### LLM cleanup contract today

[`TranscriptCleaner._build_cleanup_system_prompt`](../thestill/core/transcript_cleaner.py#L376-L478)
explicitly requires the model to preserve `[HH:MM:SS]` strings
character-for-character ("STRICT TIMESTAMP BINDING"), and explicitly allows the
model to merge adjacent same-speaker segments using the first timestamp. The
chunking logic in [`_process_chunks`](../thestill/core/transcript_cleaner.py#L265-L350)
splits on blank-line paragraph boundaries using an output-token-budget-derived
cap (≈16K output tokens, ≈64K chars).

The prompt is carefully crafted but built around the assumption that the LLM
sees a Markdown blob and must produce a Markdown blob. It will need to be
rewritten as a segment-batch prompt — the input is a JSON list of segments
(each carrying its id, speaker, raw text, neighbour context flags), the output
is a JSON list of patches keyed by segment id. This is mechanical but a real
change — the prompt is one of the most load-bearing files in the repo.

### Summariser reads the blended blob and cites strings

[`TranscriptSummarizer`](../thestill/core/post_processor.py#L62-L171)'s
`SYSTEM_PROMPT` instructs the model to cite every claim with `[MM:SS]` or
`[HH:MM:SS]` tokens copied from visible timecodes in the cleaned Markdown.
[`cli.py:1989-1990`](../thestill/cli.py#L1989-L1990) reads the cleaned
Markdown file as a plain string and hands it to `summarize()`. The summariser
never touches raw JSON.

This means the summariser will keep working for free as long as we render a
compatible blended Markdown view from the new `CleanedTranscript` during the
transition. That render is already on the critical path for the side-by-side
debug view, so there is no extra cost.

The future upgrade — teaching the summariser to emit stable `segment_id`
citations so the frontend can resolve them into seek points without string
parsing — is deliberately deferred. It becomes a much smaller change once the
segment store is in place.

### Storage layout

[`sqlite_podcast_repository.py:285-320`](../thestill/repositories/sqlite_podcast_repository.py#L285-L320)
defines the `episodes` table. Three relevant columns today:

- `raw_transcript_path TEXT NULL` — filename under `data/raw_transcripts/`.
- `clean_transcript_path TEXT NULL` — `{podcast_slug}/{file}_cleaned.md`.
- `summary_path TEXT NULL`.

The [`EpisodeState`](../thestill/models/podcast.py#L23-L43) enum is derived
purely from which of these columns is non-null
([`Episode.state`](../thestill/models/podcast.py#L161-L192)), so we do not need
to touch the state machine if we add new columns rather than replace existing
ones.

### Web API and frontend

- [`GET /api/podcasts/{p}/episodes/{e}/transcript`](../thestill/web/routes/api_podcasts.py#L239-L277)
  returns `{ content, available, transcript_type }` where `content` is a single
  Markdown string. The TS type is `ContentResponse`
  ([`types.ts:202-210`](../thestill/web/frontend/src/api/types.ts#L202-L210)).
- [`PodcastService.get_transcript`](../thestill/services/podcast_service.py#L531-L586)
  is the service behind it — reads the cleaned Markdown file, or falls back to
  raw JSON + formatter.
- [`TranscriptViewer.tsx`](../thestill/web/frontend/src/components/TranscriptViewer.tsx)
  regex-parses `[HH:MM:SS]` / `[MM:SS]` lines back into segment objects for
  display. No seek, no highlight, no audio binding.
- [`AudioPlayer.tsx`](../thestill/web/frontend/src/components/AudioPlayer.tsx)
  is a ~39-line wrapper around a plain `<audio controls>` pointing at the
  publisher's external URL. No ref, no `onTimeUpdate`, no state exported.

Adding structured segments to the API is additive: a new field on the
transcript response (or a dedicated `/segments` route), plus a new
`SegmentedTranscriptViewer` component that renders from the structured form.
The existing `TranscriptViewer` stays around unchanged during the debug phase.

### Related prior work

[Spec #08 multi-phase-transcript-cleaning](08-multi-phase-transcript-cleaning.md)
— proposes an `AnnotatedSegment`/`AnnotatedTranscript` model, hierarchical
phase processing, and prompt caching. Status: 💡 Proposal, never shipped. This
spec supersedes it. Spec #08 will be updated with a header pointer to this
spec in the same PR that creates this one.

## Clarifications

These are the architectural decisions locked in during Phase 3 of the planning
session. Every future edge case should be resolved in a way consistent with
the motivation captured here, not by re-litigating the decision.

### Segment granularity — merge diarisation fragments, keep word stream

**Decision:** The primary unit is a cleaned segment whose boundaries come from
a repaired, same-speaker-merged version of the raw segments. Word timestamps
are always preserved (when the provider gives them) so the pipeline — and any
future UI — can re-segment at finer grain (e.g. paragraph-level inside long
single-speaker runs).

**Why:** Diarisation routinely over-segments, emitting one- or two-word
fragments that are obviously part of a neighbouring run from the same speaker.
It also occasionally misattributes the first or last word of a sentence to the
wrong speaker. A same-speaker merge fixes the former; keeping word-level
timestamps lets us fix the latter (and lets a future editing UI snap segment
boundaries to word boundaries).

**How to apply:** The deterministic pre-segmentation phase (Phase B in the
execution plan) merges consecutive same-speaker segments, but **guarded by a
pause threshold**: two adjacent same-speaker segments are merged only when
the inter-segment silence — computed as `next.start - prev.end` — is below a
configurable ceiling (default `3.0` seconds). A silence longer than the
ceiling signals a likely topic shift or host handoff and is preserved as a
segment boundary even though the speaker label matches on both sides. After
the guarded merge, the merged runs are re-chunked on sentence/paragraph
boundaries using punctuation and inter-word silence (gap ≥ 0.5s is a
paragraph hint) derived from word timestamps. Speaker boundary repair at
the word level is bounded by the LLM cleanup prompt's context window: the
pass can see ±N neighbour segments plus episode facts, and is allowed to
move single trailing/leading words across a boundary. A whole-transcript
pass for deeper boundary repair is a future optimisation, not in the first
shippable version.

### Destructive cleanup and segment preservation — structured-first

**Decision:** We never blend the segment list into one text during cleaning.
The LLM operates on a segment list (or a small batch of them) with neighbour
context, and its output is a patch keyed by segment id. Filler removal, ad
replacement, and spelling correction are all applied per segment. A segment
whose cleaned text becomes empty stays in the list with `text=""` (and a
`kind: "filler" | "ad_break" | "content"` tag). Ad spans collapse into a
single `kind="ad_break"` segment that spans the original range and carries
the sponsor name.

**Why:** The user's core motivation is the richer media player, which needs a
stable grid of `(segment_id, start, end)` anchors to highlight and seek to.
Option "raw-segment preservation with some segments becoming empty" fits that
requirement directly. Option "cleaned segments with legal gaps in the timeline"
would leave holes where the player has nothing to highlight during
filler/ad playback, which is worse UX. Option "two-track" (raw grid + parallel
cleaned stream) adds coordination cost without buying anything the first
option doesn't already give us.

**How to apply:** Every cleaned segment carries `source_segment_ids: List[int]`
pointing back into the raw JSON. Multi-raw-to-one-cleaned merges are allowed
and common; one-raw-to-multi-cleaned splits are allowed (paragraph splitting).
The renderer that produces blended Markdown for download/backward-compat
drops `kind="filler"` segments and formats `kind="ad_break"` as the existing
`**[TIMESTAMP] [AD BREAK]** - Sponsor Name` marker, so the summariser keeps
producing the same output shape during the transition.

### Per-segment LLM cleanup with prompt caching

**Decision:** Cleanup runs in small batches — one segment at a time for short
segments, or a handful of short segments together, capped at a per-batch
character budget. Each LLM call sees:

- **Cached prefix** (identical across every call for the episode): system
  prompt, podcast facts, episode facts, speaker mapping, ad sponsor list.
- **Variable suffix** (different per call): ±N previous segments (already
  cleaned, for tone continuity), the target segment(s), ±N next segments
  (raw, for forward context), all as a JSON list.
- **Response:** JSON patch list, one entry per target segment id, carrying
  `{ cleaned_text, kind, sponsor?, boundary_adjustment? }`.

Prompt caching is used where the provider supports it (Anthropic explicit
cache control, OpenAI automatic prefix caching, Gemini implicit context
caching). Ollama and other no-cache providers fall back to a larger batch
size to amortise the repeated prefix.

**Why:** The current single-call whole-transcript approach has two failure
modes: the LLM skips content when the input is large (see spec #08 §1), and
it has no stable relationship between what it emits and the segment
structure. Per-segment processing with neighbour context trades raw context
window for reliability and structural fidelity. Prompt caching makes the
cost acceptable — the cacheable prefix is ~2–5K tokens (system prompt + facts
for most episodes), and it is reused for every segment batch in the episode.
Budget sanity check in Alternatives Considered below.

**How to apply:** The cleanup phase iterates segments in order, building each
call's input from (a) the last `k_prev` cleaned outputs from the running
buffer and (b) the next `k_next` raw segments from the input. Initial values:
`k_prev = 2`, `k_next = 2`, batch size = 1–3 segments depending on character
count. These are tunable via config; the spec does not hardcode them.

### Scope: cleanup in this spec, summarisation as follow-up

**Decision:** This spec only touches the cleanup stage and the data model /
storage / API plumbing required to expose its output. The summariser keeps
reading a blended-Markdown render of the new structured output and keeps
emitting `[MM:SS]` string citations. A later spec will teach it to emit
`segment_id` references.

**Why:** The user explicitly said summarisation is a follow-up. Keeping the
summariser on the Markdown render also buys us a regression-free migration:
as long as the render looks the same as the old single-call output, summaries
do not need re-running.

**How to apply:** The blended Markdown renderer lives inside
`CleanedTranscript.to_blended_markdown()` and reproduces the exact format the
current cleanup emits — `[HH:MM:SS] **Speaker Name:** text`, ad breaks as
`**[TIMESTAMP] [AD BREAK]** - Sponsor Name`, filler segments dropped. The
summariser continues to read from `data/clean_transcripts/{slug}/{file}_cleaned.md`
unchanged. A future spec can swap it onto the structured path.

### Audio source: keep publisher URL, add per-episode offset field

**Decision:** The player keeps pointing at the publisher's external audio URL.
A new `playback_time_offset_seconds: float` field is added to the episode
model (default `0.0`) and sent on every API response that carries segment
timestamps. The frontend will eventually apply it when seeking/highlighting.

**Why:** The downsampled 16 kHz WAV timestamps usually align with the
publisher MP3 to within a few hundred milliseconds — good enough for segment
highlighting. The rare episode that drifts (leading silence trimmed during
downsampling, inserted pre-roll, etc.) is fixable with a per-episode offset.
Serving the audio from the backend would guarantee alignment but breaks the
slim Docker image (spec #05) by requiring `data/original_audio/` to be kept.

**How to apply:**

- **Storage (authoritative):** the `episodes` SQLite table gets a
  `playback_time_offset_seconds REAL NOT NULL DEFAULT 0.0` column. **The
  database is the source of truth.** `NOT NULL` avoids the null-vs-zero
  ambiguity the earlier draft carried.
- **JSON sidecar (cache only):** the `AnnotatedTranscript` JSON on disk
  carries a copy of the field for offline-render convenience (so the
  blended-Markdown renderer can produce `[MM:SS]` timecodes shifted by
  the offset without consulting the DB). On every read that crosses the
  service layer, the JSON copy is **ignored and overwritten** with the DB
  value — if the two ever disagree, the DB wins, silently. Writers
  (cleaning processor, any future editing path) write the DB value first
  and then stamp the JSON. This makes the JSON a write-through cache, not
  an independent record.
- **API:** the field is returned on the episode detail endpoint and on the
  transcript endpoint's `segments` object. Both read from the DB, not the
  JSON, so API responses are authoritative by construction.

The field is inert in this spec — the player follow-up will actually
consume it.

### Parakeet: graceful fallback, not a blocker

**Decision:** Parakeet's one-stub-segment output is out of scope for this
spec. Episodes transcribed with Parakeet fall back to the legacy blended
cleaning path (unchanged from today). The underlying bug — Parakeet should be
emitting per-segment word timestamps like every other provider — is tracked
as a separate small task.

**Why:** The user agreed this is a transcriber bug, not a cleaning bug, and
fixing it belongs in a different change. Blocking segment-aware cleaning on
Parakeet parity would delay the feature unnecessarily.

**How to apply:** The new cleaning entry point inspects the raw JSON's
segment list; if there is a single segment with `start == 0 and end == 0`,
or if `len(segments) < 2`, it logs a warning and delegates to the legacy
`TranscriptFormatter` + blended-cleanup path. The debug view surfaces this
as "legacy cleanup — segmented view unavailable for this provider."

### Spec #08 is superseded

**Decision:** Spec #08 (multi-phase-transcript-cleaning, Proposal) is
absorbed into this spec and updated with a header pointing to spec #18 as
the authoritative plan. No content is deleted from #08 — it is useful
historical context and the prompt-caching cost analysis is re-used here.

**Why:** Two live proposals for the same area will diverge. One is the plan.

**How to apply:** In Phase A below, the first implementation change is a
one-line header edit to #08's status (`💡 Proposal` → `🗄 Archived`) and a
pointer line referencing this spec. The index table in `specs/README.md` is
updated to move #08 from Active Plans to… well, it is still in Active Plans
because it is a proposal, and Archived is not an active-plans marker. Easier:
leave #08 in Active Plans but flip the status marker to `🗄 Archived` and
update its Summary column to "Superseded by #18". The `specs/README.md`
status legend already knows about `🗄 Archived`.

### Dual output during debugging — two orthogonal flags

**Decision:** There are **two** independent config flags controlling
cleanup behaviour, and they do different things:

- `THESTILL_CLEANUP_PIPELINE` — **routing flag**, values
  `segmented` | `legacy`. Decides which cleaner is the *primary* producer of
  `clean_transcript_path`. Default `segmented` in development once Phase C
  lands; default `legacy` in production until Phase F flips it. This is the
  incident kill switch: setting it to `legacy` routes every episode through
  the untouched old cleaner. Parakeet-style transcripts (rejected by
  `has_usable_segment_structure`) always take the legacy path regardless of
  the flag — the flag has no authority over degenerate inputs.
- `THESTILL_LEGACY_CLEANUP_SHADOW` — **shadow/dual-run flag**, boolean.
  When true, whichever pipeline is *not* the primary is also invoked as a
  shadow, and its output is written to a sibling debug file so the two can
  be compared. Default on in development (so developers see both), off in
  production. Orthogonal to routing: shadow can run with either
  `segmented` or `legacy` as the primary.

**Why:** One flag cannot do both jobs. The prior draft of this spec
conflated them and would have left us unable to kill-switch cleanup during
an incident without also disabling the side-by-side debug view. Splitting
them lets "roll back to legacy" be a one-line flag flip while the debug
infrastructure stays independently togglable.

**How to apply:** During Phase C, the processor reads both flags. The
primary pipeline writes `clean_transcript_path` (Markdown, always) and, if
it was the segmented cleaner, also `clean_transcript_json_path` (JSON
sidecar). The shadow pipeline, if enabled, writes to a sibling debug file
under `data/clean_transcripts/{slug}/debug/` — see Phase D for filenames.
The API surface and frontend tab behaviour are documented in one place in
the "API response shape" section below; do not redefine them elsewhere.
Once the user is happy with the eval results, Phase F flips
`THESTILL_CLEANUP_PIPELINE` to `segmented` by default, turns
`THESTILL_LEGACY_CLEANUP_SHADOW` off by default, and retires the legacy
viewer from the frontend. The routing flag and the legacy cleaner itself
both remain available — the latter because Parakeet fallback still needs
it until Parakeet is fixed, the former as an incident kill switch.

### API response shape — single canonical definition

**Decision:** The transcript endpoint's response shape is defined here and
here only. Phase D's file-level plan implements this literally.

```ts
// Existing fields — unchanged, still populated for every episode
type TranscriptResponse = {
  episode_id: string
  episode_title: string
  content: string                        // blended Markdown (primary)
  available: boolean
  transcript_type: "cleaned" | "raw" | null

  // New optional fields — populated when the segmented cleaner produced
  // output for this episode (present iff clean_transcript_json_path is set)
  segments?: AnnotatedTranscriptDump     // full AnnotatedTranscript.model_dump()

  // New optional field — populated when the shadow pipeline ran for this
  // episode (present iff the sibling debug file exists on disk)
  shadow?: {
    pipeline: "segmented" | "legacy"     // which pipeline was the shadow
    content: string                      // the shadow's blended Markdown output
  }
}
```

**Why:** The earlier draft described the shape two different ways (one
section said `{ segmented, legacy }`, another said additive `segments` and
`legacy_content` fields). Pick one and write it down once. The shape above
is chosen because it preserves the existing `content`/`transcript_type`
fields unchanged — older frontend builds, the summariser's upstream
consumers, and any other client continue to work without modification —
while letting new clients read `segments` when they want structure. The
`shadow` object is symmetrical: it names the pipeline that produced it, so
the frontend can render either "legacy shadow of segmented primary" or
"segmented shadow of legacy primary" with the same code path.

**How to apply:** The response dict is assembled in
`api_podcasts.py`'s `get_episode_transcript_by_slugs` handler. `segments`
is populated by loading the `AnnotatedTranscript` JSON sidecar when
`episode.clean_transcript_json_path` is set. `shadow` is populated by
checking for the sibling debug file and loading it. Neither key is present
when the data is absent — callers use optional-chaining (`response.segments
?? fallback`). The TypeScript type declared in `types.ts` matches this
shape exactly, and the `SegmentedTranscriptViewer` only renders when
`segments` is present.

### Eval harness

**Decision:** Before flipping any default, land an eval harness that scores
both approaches on the same fixture episodes. The harness is a CLI command
`thestill eval-cleanup --episode-id <id>` and a JSON report artefact.
Metrics in v1:

- **Coverage**: fraction of raw-transcript word count that appears in the
  cleaned output. The legacy pipeline's "skip the first 47 minutes" bug that
  motivated spec #08 would show as ~50% coverage.
- **Word-preservation ratio**: edit distance between raw and cleaned text,
  normalised by raw word count. Catches paraphrasing.
- **Entity recall**: fraction of known keywords / sponsors / guest names
  (from `podcast_facts` and `episode_facts`) that appear in the cleaned
  output. Catches dropped proper nouns.
- **Ad detection recall**: fraction of ad-sponsor strings from
  `episode_facts.ad_sponsors` that land inside an `[AD BREAK]` marker span.
- **Segment boundary drift** *(new pipeline only)*: max deviation between a
  cleaned segment's declared `(start, end)` and the corresponding
  word-stream-derived ground truth. Should be ≤ word-boundary precision.
- **LLM-judge score** *(optional, costs tokens)*: a structured-output judge
  prompt that rates cleanup fidelity on 1–5 with reasons. Run opportunistically,
  not per-iteration.

**Why:** "It looks right in the UI" is not enough to flip a default. The
metrics above correspond to specific failure modes we have already seen
(spec #08 §1). They are also cheap to compute — all but the LLM judge are
deterministic and run offline.

**How to apply:** The eval harness is Phase E in the execution plan below.
It is the verification gate for flipping the default in Phase F.

### Future: user segment-editing UI — identity scheme

**Decision:** Out of scope to ship, but the data model must not foreclose
on it. Two distinct notions of identity live side by side on every
`AnnotatedSegment`:

- **`id: int`** — positional, assigned sequentially within one
  `AnnotatedTranscript` instance. Deterministic for a given input +
  algorithm version: re-running the segmented cleaner on the same raw
  JSON with the same code produces the same ids. **Not** stable across
  code changes that alter segmentation or boundary-repair heuristics — a
  re-clean after such a change legitimately renumbers. This is the id the
  LLM patches reference and the frontend uses as a React key.
- **`source_segment_ids: List[int]`** — the durable anchor. These are the
  raw `Segment.id`s from the immutable raw JSON on disk. They survive
  re-cleaning trivially because the raw JSON is write-once. Anything that
  needs to *persist* across re-cleans (bookmarks, user edits, future
  evaluation ground truth) should key off `source_segment_ids`, not `id`.
- **`user_segment_id: Optional[str]`** — reserved, null in this spec.
  The editing-UI follow-up will assign a UUID the first time a user
  touches a segment (merge, split, boundary move) and persist it in a
  separate `segment_edits` table keyed to `(episode_id, user_segment_id)`.
  Until then the field sits on the model unused.

**Why:** Hashing `(source_segment_ids, start, end)` (as the earlier draft
hinted) would be fragile: boundary adjustments from the LLM and float
precision noise would churn the hash on otherwise-identical re-runs. And a
content hash would ripple through every descendant when a word further up
the transcript is cleaned differently. Separating "positional id for this
run" from "durable anchor into the raw JSON" from "user-edit identity"
avoids the collision: each concept gets its own field, and the one that
needs to be stable across re-cleans (source anchors) is the one that
already is by construction.

**How to apply:** `AnnotatedSegment` declares all three fields. The
`source_segment_ids` list is populated during pre-segmentation (Phase B)
and preserved through LLM cleanup (Phase C) — the LLM's patch output is
allowed to modify `text`, `kind`, `sponsor`, and `boundary_adjustment`
but **may not** rewrite `source_segment_ids`. The cleaner enforces that
invariant before applying any patch. `metadata: Dict[str, Any]` remains
as a free-form bucket for other future fields (`edited_by_user`,
`reviewer_notes`, etc.).

## Execution plan

The plan is gated: each phase has a verification gate that must be green
before the next phase starts.

### Phase A — Preliminaries, spec bookkeeping, Parakeet fallback wiring

**Goal:** Ship the cheapest groundwork changes that unblock the rest.

| # | File | Change |
|---|---|---|
| 1 | [specs/08-multi-phase-transcript-cleaning.md](08-multi-phase-transcript-cleaning.md) | Flip status to `🗄 Archived`, add "Superseded by [spec #18](18-segment-preserving-transcript-cleaning.md)" pointer above §1. |
| 2 | [specs/README.md](README.md) | Update #08's row: status `🗄 Archived`, summary `Superseded by #18`. Add #18 row under Active Plans. |
| 3 | [thestill/utils/transcript_capabilities.py](../thestill/utils/transcript_capabilities.py) *(new)* | Define the `has_usable_segment_structure(transcript: Transcript) -> bool` helper per §"Degenerate-transcript detection". One function, one predicate, one home. |
| 4 | [thestill/core/transcript_cleaning_processor.py](../thestill/core/transcript_cleaning_processor.py) | At the top of `clean_transcript`, call `has_usable_segment_structure(transcript)`; when it returns False, log a structured `segmented_cleanup_unavailable` event with `reason=<missing_word_timestamps\|zero_length_segments\|no_segments>`. No behaviour change yet — this is just the observability hook that Phase C's routing switch will consume. |

**Verification gate:** `make test` green; `thestill status` still runs; the
`specs/README.md` index renders cleanly.

### Phase B — `AnnotatedTranscript` data model + deterministic pre-segmentation

**Goal:** Introduce the structured data model and the deterministic
diarisation-repair phase. No LLM changes yet.

| # | File | Change |
|---|---|---|
| 1 | [thestill/models/annotated_transcript.py](../thestill/models/annotated_transcript.py) *(new)* | Define `WordSpan`, `AnnotatedSegment`, and `AnnotatedTranscript` Pydantic models. `AnnotatedSegment` fields per §Clarifications and §"Word addressing scheme": `id: int` (positional), `start: float`, `end: float`, `speaker: Optional[str]`, `text: str`, `kind: Literal["content", "filler", "ad_break"]`, `sponsor: Optional[str]`, `source_segment_ids: List[int]`, `source_word_span: Optional[WordSpan]`, `user_segment_id: Optional[str] = None`, `metadata: Dict[str, Any] = {}`. `AnnotatedTranscript` carries `episode_id`, `segments: List[AnnotatedSegment]`, `playback_time_offset_seconds: float` (cached copy of the DB value), and `algorithm_version: str`. Include `to_blended_markdown()` that reproduces `TranscriptFormatter`'s current output format exactly. Include `from_raw(Transcript)` constructor that wraps raw segments 1:1 without repair, populating `source_segment_ids=[seg.id]` and a `source_word_span` covering the segment's full word range. |
| 2 | [thestill/core/transcript_segmenter.py](../thestill/core/transcript_segmenter.py) *(new)* | `TranscriptSegmenter` class with one entry point `repair(transcript: Transcript) -> AnnotatedTranscript`. Step 1: merge consecutive same-speaker segments, **guarded by a pause ceiling** — do not merge when `next.start - prev.end >= pause_ceiling_seconds` (default `3.0`). Step 2: for merged runs whose word count exceeds a configurable threshold, re-chunk on sentence/paragraph boundaries using word-stream punctuation and inter-word silence (gap ≥ 0.5s is a paragraph hint). Deterministic, no LLM, no network. `pause_ceiling_seconds` and the chunking thresholds are constructor args with sane defaults; see Open Questions for tuning. |
| 3 | [tests/core/test_transcript_segmenter.py](../tests/core/test_transcript_segmenter.py) *(new)* | Fixture-based tests: merging short fragments **inside** the pause ceiling, **refusing to merge** across a pause above the ceiling (topic shift case), splitting long single-speaker runs, preserving raw `source_segment_ids` across merges, and that `has_usable_segment_structure=False` inputs raise rather than being silently accepted (the processor, not the segmenter, handles the fallback). |
| 4 | [tests/models/test_annotated_transcript.py](../tests/models/test_annotated_transcript.py) *(new)* | Round-trip tests: `AnnotatedTranscript.from_raw(t).to_blended_markdown()` produces output **byte-identical** to `TranscriptFormatter().format_transcript(t.model_dump())` for a sample transcript. This is the load-bearing backward-compat guarantee for the summariser. |

**Verification gate:** `make test` green including the byte-identical
round-trip test; `./venv/bin/python -m mypy thestill/models/annotated_transcript.py thestill/core/transcript_segmenter.py` clean.

### Phase C — Segment-by-segment LLM cleanup with neighbour context

**Goal:** New `SegmentedTranscriptCleaner` that consumes an
`AnnotatedTranscript` and returns a cleaned `AnnotatedTranscript`, using
per-segment batches with prompt caching.

| # | File | Change |
|---|---|---|
| 1 | [thestill/core/segmented_transcript_cleaner.py](../thestill/core/segmented_transcript_cleaner.py) *(new)* | `SegmentedTranscriptCleaner` with one entry point `clean(annotated: AnnotatedTranscript, podcast_facts, episode_facts, language) -> AnnotatedTranscript`. Iterates segments in order, builds a per-batch prompt whose cacheable prefix is `(system_prompt, podcast_facts, episode_facts, speaker_mapping, sponsors)` and whose variable suffix is `(k_prev cleaned segments, target batch, k_next raw segments)` serialised as JSON. Uses structured output — the response is a JSON patch list `[{id, cleaned_text, kind, sponsor?}]`. Applies patches onto a running buffer. **Invariant enforced before applying any patch:** the patch may mutate `text`, `kind`, `sponsor`, `boundary_adjustment`; it may **not** rewrite `source_segment_ids` or `source_word_span`. The cleaner drops any patch key that would touch these fields and logs a warning. Exposes `k_prev`, `k_next`, `batch_char_budget` as constructor args with sane defaults (2, 2, ~4000 chars). |
| 2 | [thestill/core/llm_provider.py](../thestill/core/llm_provider.py) | Add an optional `supports_prompt_caching` capability query. Add a `chat_completion_cached` overload (or a `cache_control` parameter on `chat_completion`) that marks a prefix portion of the messages as cacheable. Implement it for Anthropic (explicit `cache_control` markers), OpenAI (automatic — capability just returns True, no extra call), Gemini (implicit — same). Ollama and any other provider return `supports_prompt_caching = False`; the cleaner must handle this by widening `batch_char_budget` to amortise the repeated prefix. |
| 3 | [thestill/core/transcript_cleaning_processor.py](../thestill/core/transcript_cleaning_processor.py) | Read the two flags: `THESTILL_CLEANUP_PIPELINE` (values `segmented`\|`legacy`, default `segmented`) selects the **primary**; `THESTILL_LEGACY_CLEANUP_SHADOW` (bool, default true in development) gates the shadow run. Routing algorithm: (a) if `has_usable_segment_structure(transcript)` is False, force primary = legacy regardless of flag and skip shadow entirely; (b) otherwise primary = flag value. Run primary, write `clean_transcript_path` (Markdown) and, if primary is segmented, also `clean_transcript_json_path`. If shadow flag is on and routing did not force-fallback, run the *other* pipeline too and write its output to a sibling debug file under `data/clean_transcripts/{slug}/debug/`. Return primary + shadow paths in the result dict. |
| 4 | [thestill/utils/path_manager.py](../thestill/utils/path_manager.py) | Add `clean_transcript_json_file()` and `clean_transcript_shadow_file(pipeline: str)` helpers returning `data/clean_transcripts/{slug}/{file}_cleaned.json` and `.../debug/{file}.shadow_{pipeline}.md`. The shadow helper takes the pipeline name so we can write either `shadow_legacy.md` or `shadow_segmented.md` depending on which one shadowed. |
| 5 | [tests/core/test_segmented_transcript_cleaner.py](../tests/core/test_segmented_transcript_cleaner.py) *(new)* | Unit tests with a fake LLM provider that returns canned patches. Verifies prefix is marked cacheable, patches apply to the correct segment ids, filler segments become `kind="filler"` with empty text, ad segments become `kind="ad_break"` with the sponsor, and **patches attempting to rewrite `source_segment_ids` are dropped with a warning**. Verifies that running the new cleaner and then `.to_blended_markdown()` produces output whose structure matches the legacy cleaner's contract (speaker names, timestamps, ad marker format). |
| 6 | [tests/core/test_cleanup_processor_routing.py](../tests/core/test_cleanup_processor_routing.py) *(new)* | Tests for the flag-driven routing matrix: `(pipeline=segmented, shadow=on)` → segmented primary, legacy shadow written; `(pipeline=legacy, shadow=on)` → legacy primary, segmented shadow written; `(pipeline=segmented, shadow=off)` → segmented only; `(pipeline=legacy, shadow=off)` → legacy only; degenerate Parakeet-style input → legacy primary, shadow skipped, regardless of flags. |

**Verification gate:** `make test` green; the routing matrix test passes;
on one fixture episode with `THESTILL_CLEANUP_PIPELINE=segmented
THESTILL_LEGACY_CLEANUP_SHADOW=1`, `thestill clean-transcript <episode-id>`
produces both `_cleaned.json` and `_cleaned.md` (from
`to_blended_markdown()`), and the shadow legacy output lives under
`debug/{file}.shadow_legacy.md`; flipping `THESTILL_CLEANUP_PIPELINE=legacy`
on the same episode produces a conventional legacy `_cleaned.md` with a
sibling `debug/{file}.shadow_segmented.md`. The summariser run against
either primary `_cleaned.md` succeeds without prompt changes.

### Phase D — Storage, API, and debug UI

**Goal:** Persist the new artefact and expose it so we can compare old and
new in the browser.

| # | File | Change |
|---|---|---|
| 1 | [thestill/repositories/sqlite_podcast_repository.py](../thestill/repositories/sqlite_podcast_repository.py) | Schema migration: add `clean_transcript_json_path TEXT NULL` and `playback_time_offset_seconds REAL NOT NULL DEFAULT 0.0` to `episodes`. Forward-only migration. No backfill — `clean_transcript_json_path` is populated on next cleanup; `playback_time_offset_seconds` gets its default on existing rows. |
| 2 | [thestill/models/podcast.py](../thestill/models/podcast.py) | Add the two new fields to the `Episode` model. `playback_time_offset_seconds: float = 0.0` (not `Optional` — matches the `NOT NULL DEFAULT 0.0` schema; the DB is the source of truth). `Episode.state` is **not** changed — the cleaned state remains gated on `clean_transcript_path` (the Markdown render), not on the JSON. This keeps the state machine stable and lets Parakeet-fallback episodes still reach CLEANED. |
| 3 | [thestill/services/podcast_service.py](../thestill/services/podcast_service.py) | Add `get_segmented_transcript(podcast_id, episode_id) -> Optional[SegmentedTranscriptResult]` that loads the `AnnotatedTranscript` JSON sidecar if present and **overwrites** its `playback_time_offset_seconds` field with the value from `episode.playback_time_offset_seconds` before returning, enforcing DB-is-source-of-truth on the read path. Also add `get_shadow_transcript(podcast_id, episode_id) -> Optional[ShadowResult]` that looks for the sibling debug file and returns `(pipeline, content)` if found. Rewrite `get_transcript`'s raw fallback path to build `AnnotatedTranscript.from_raw()` and render it via `to_blended_markdown()`, replacing the direct `TranscriptFormatter` call. |
| 4 | [thestill/web/routes/api_podcasts.py](../thestill/web/routes/api_podcasts.py) | Extend `get_episode_transcript_by_slugs` to assemble the response defined in the "API response shape" clarification: always return `content`/`transcript_type`/`available` unchanged; additionally return `segments: AnnotatedTranscriptDump` when the JSON sidecar exists; additionally return `shadow: {pipeline, content}` when the sibling debug file exists. Neither new key is present when its data is absent — absence is the signal. Do **not** introduce any other shape. |
| 5 | [thestill/web/frontend/src/api/types.ts](../thestill/web/frontend/src/api/types.ts) | Add `WordSpan`, `AnnotatedSegment`, `AnnotatedTranscriptDump`, and the extended `TranscriptResponse` TS types mirroring the Pydantic and API shapes exactly. The two new fields (`segments`, `shadow`) are declared as optional (`?`) with the exact types from the canonical API definition in this spec. |
| 6 | [thestill/web/frontend/src/components/SegmentedTranscriptViewer.tsx](../thestill/web/frontend/src/components/SegmentedTranscriptViewer.tsx) *(new)* | Renders from `segments: AnnotatedSegment[]` rather than parsing Markdown. Shows each segment with its `[MM:SS]` timestamp (derived from `segment.start + playback_time_offset_seconds` but rendered read-only — the field is inert in this spec), speaker, text, and a visual `kind` indicator (ad break / filler collapsed / content). No seek wiring yet — that is the player follow-up. |
| 7 | [thestill/web/frontend/src/pages/EpisodeDetail.tsx](../thestill/web/frontend/src/pages/EpisodeDetail.tsx) | Add a tab/toggle above the Transcript panel. Tabs are rendered based on what the response contains: "Segmented" shown when `response.segments` is present, "Legacy blended" shown when `response.content` is non-empty, "Shadow ({pipeline})" shown when `response.shadow` is present. Defaults to "Segmented" when available, otherwise "Legacy blended". The toggle is only rendered when two or more tabs would be shown. |

**Verification gate:** `make test` green; `thestill server` starts; the
episode detail page shows both tabs for a re-cleaned fixture episode;
pre-existing episodes (no JSON sidecar) still render via the legacy path
with the toggle hidden.

### Phase E — Eval harness

**Goal:** A repeatable offline script that compares the old and new pipelines
on the same episode and produces a JSON metrics report.

| # | File | Change |
|---|---|---|
| 1 | [thestill/evals/cleanup_eval.py](../thestill/evals/cleanup_eval.py) *(new)* | `CleanupEvaluator` with one method `evaluate(raw: Transcript, legacy_md: str, new: AnnotatedTranscript, facts: EpisodeFacts) -> EvalReport`. Implements the six metrics listed in §Clarifications / Eval harness. Uses no LLM except for the optional judge. |
| 2 | [thestill/cli.py](../thestill/cli.py) | New command `thestill eval-cleanup --episode-id <id> [--json]` that loads the raw transcript, the legacy cleaned Markdown (running the legacy cleaner if the shadow file is not on disk), the new `AnnotatedTranscript`, and the episode facts, then runs the evaluator and prints/dumps the report. |
| 3 | [tests/evals/test_cleanup_eval.py](../tests/evals/test_cleanup_eval.py) *(new)* | Metric unit tests on synthetic inputs — coverage should drop to ~0.5 when half the text is missing, entity recall should be 1.0 when every known keyword is present, ad detection recall should match the number of `ad_sponsors` found in marked ad-break spans. |
| 4 | [docs/eval-cleanup.md](../docs/eval-cleanup.md) *(new)* | How to run the eval, what each metric means, how to interpret a regression. Short — one page. |

**Verification gate:** `thestill eval-cleanup --episode-id <id>` produces a
report for both pipelines on the same episode. The user runs it on 3–5
representative episodes (including one Parakeet-only episode, one long
interview, one ad-heavy episode) and decides whether the new approach is
ready to become the default.

### Phase F — Flip default, retire shadow

**Goal:** Only after user sign-off on the eval report, make the new pipeline
the default and remove the shadow / dual-view scaffolding.

| # | File | Change |
|---|---|---|
| 1 | [thestill/core/transcript_cleaning_processor.py](../thestill/core/transcript_cleaning_processor.py) | Flip defaults: `THESTILL_CLEANUP_PIPELINE` stays `segmented` (already the default since Phase C); `THESTILL_LEGACY_CLEANUP_SHADOW` default flips from true to false in production environments. **The routing flag stays readable** — it is the incident kill switch and must remain available for operators to set `legacy` at runtime. The shadow flag also stays readable for operators who want to re-enable dual-run during investigations. |
| 2 | [thestill/web/frontend/src/components/TranscriptViewer.tsx](../thestill/web/frontend/src/components/TranscriptViewer.tsx) | Retire to a "classic" opt-in (hidden behind a URL query param) or delete outright — the `SegmentedTranscriptViewer` becomes the default and only view for episodes that have `segments`. Episodes that fell back to legacy (Parakeet) continue rendering legacy blended markdown via the existing `ReactMarkdown` path, but that lives in `SegmentedTranscriptViewer`'s empty-state branch, not in this retired component. |
| 3 | [thestill/web/frontend/src/pages/EpisodeDetail.tsx](../thestill/web/frontend/src/pages/EpisodeDetail.tsx) | Remove the "Segmented vs Legacy blended" toggle. The Shadow tab is also removed from the default UI (the response still carries `shadow` when the shadow flag is on, but the default frontend no longer renders it — operators who re-enable shadow can inspect it via the API directly). |
| 4 | [thestill/core/transcript_cleaner.py](../thestill/core/transcript_cleaner.py) | Retained but deprecated — still used for Parakeet fallback until the Parakeet bug is fixed, and still reachable via `THESTILL_CLEANUP_PIPELINE=legacy` as the kill switch. Add a module-level deprecation note pointing at `SegmentedTranscriptCleaner`. |

**Verification gate:** `make check` green; production cleanup runs go through
the new path; the eval report on the fixture set shows no regressions vs the
pre-flip baseline.

## Alternatives considered

### Sidecar JSON + Markdown as primary artefact

Keep the legacy Markdown cleanup unchanged and, as a post-step, re-align the
cleaned Markdown back to raw segments via fuzzy text matching. Store the
aligned segment list as a sidecar JSON.

**Rejected because** alignment is fragile. Every destructive edit the cleaner
makes — filler removal, merging, ad collapsing, paraphrasing — introduces a
chance of mis-alignment. Worse, alignment failures are silent: the sidecar
looks populated but its boundaries drift from reality. This defeats the
entire point, which is to have a stable anchor grid for the player.

### Spec #08 as-is: multi-phase (speaker ID, ad, cleanup) on segment metadata

Spec #08's hierarchical approach operates on different granularities per
phase — e.g., speaker ID on segment metadata only, spelling on full text. It
is a more ambitious restructure.

**Rejected because** it is larger than necessary to unblock the player. The
player needs structured segments reaching the frontend; it does not need
multi-phase cleanup. Spec #18 takes the data-model and prompt-caching ideas
from #08 but stays within a single-phase cleanup that operates per segment.
Multi-phase cleanup can still be added later on top of the segment store if
it turns out to be needed.

### Whole-transcript LLM pass, then post-hoc segment reconstruction

Run the existing big-blob cleanup, then have a second LLM pass whose job is
purely to re-align the cleaned output to raw segment IDs.

**Rejected because** it doubles LLM cost, still carries the original "model
skips content" failure mode of the single-pass cleaner, and any alignment
failures still land silently.

### Operate on `Transcript` directly without an `AnnotatedTranscript` wrapper

Just edit the raw `Transcript.segments` list in place, setting `Segment.text`
to the cleaned version and adding ad-break sentinel segments.

**Rejected because** the raw transcript is the source of truth for
re-cleaning and for the future editing UI. Mutating it in place loses the
ability to re-run cleanup deterministically from the on-disk JSON. Keeping
`CleanedTranscript` (`AnnotatedTranscript`) as a distinct artefact pointing
*back* at raw segments preserves that property.

### Prompt-caching budget sanity check

For a 60-minute episode with ~600 raw segments merged into ~200 annotated
segments, at batch size 1 and `k_prev=k_next=2`:

- Cached prefix (system prompt + facts + sponsors): ~3K tokens, cached once,
  free on subsequent calls.
- Variable suffix per call: 5 segments × ~150 tokens = ~750 tokens.
- ~200 calls × ~750 tokens ≈ 150K input tokens + 200 × ~150 output = 30K output.

The legacy single-pass cleanup on the same episode uses ~70K input + ~60K
output. So the new approach is ~2× input tokens and ~0.5× output tokens.
With prompt caching, the cached prefix is charged once, so ~200 × 750 =
150K *effective* input vs the legacy 70K — meaningful but not catastrophic.
This is acceptable if it buys us structural fidelity and eliminates the
"skips 47 minutes" failure mode. The eval harness in Phase E will verify
the quality trade.

## Files touched

**Modified (existing files):**

- [thestill/core/transcript_cleaning_processor.py](../thestill/core/transcript_cleaning_processor.py)
- [thestill/core/transcript_cleaner.py](../thestill/core/transcript_cleaner.py) (deprecation only)
- [thestill/core/llm_provider.py](../thestill/core/llm_provider.py)
- [thestill/services/podcast_service.py](../thestill/services/podcast_service.py)
- [thestill/repositories/sqlite_podcast_repository.py](../thestill/repositories/sqlite_podcast_repository.py)
- [thestill/models/podcast.py](../thestill/models/podcast.py)
- [thestill/utils/path_manager.py](../thestill/utils/path_manager.py)
- [thestill/web/routes/api_podcasts.py](../thestill/web/routes/api_podcasts.py)
- [thestill/web/frontend/src/api/types.ts](../thestill/web/frontend/src/api/types.ts)
- [thestill/web/frontend/src/pages/EpisodeDetail.tsx](../thestill/web/frontend/src/pages/EpisodeDetail.tsx)
- [thestill/web/frontend/src/components/TranscriptViewer.tsx](../thestill/web/frontend/src/components/TranscriptViewer.tsx) (retired in Phase F)
- [thestill/cli.py](../thestill/cli.py)
- [specs/08-multi-phase-transcript-cleaning.md](08-multi-phase-transcript-cleaning.md)
- [specs/README.md](README.md)

**Created:**

- `thestill/models/annotated_transcript.py`
- `thestill/core/transcript_segmenter.py`
- `thestill/core/segmented_transcript_cleaner.py`
- `thestill/utils/transcript_capabilities.py`
- `thestill/evals/cleanup_eval.py`
- `thestill/web/frontend/src/components/SegmentedTranscriptViewer.tsx`
- `tests/models/test_annotated_transcript.py`
- `tests/core/test_transcript_segmenter.py`
- `tests/core/test_segmented_transcript_cleaner.py`
- `tests/core/test_cleanup_processor_routing.py`
- `tests/evals/test_cleanup_eval.py`
- `docs/eval-cleanup.md`

**Deleted:** None during the plan. Phase F removes `TranscriptViewer.tsx`'s
regex-parser code path and the shadow wiring but the legacy `TranscriptCleaner`
stays for the Parakeet fallback until Parakeet is fixed.

## Open questions

1. **`k_prev` / `k_next` / batch size defaults.** The spec picks `(2, 2, ~4000
   chars)` as a starting point. Resolved during Phase C by running the eval
   harness with a grid of values on 3–5 fixture episodes and picking the
   cheapest configuration that meets coverage and entity-recall bars.

2. **Speaker-boundary repair at word level — dedicated phase or inside
   cleanup prompt?** The spec currently has it embedded in the cleanup
   prompt (each batch can move single trailing/leading words across its own
   boundaries). If the eval shows this is unreliable, a cheap fallback is a
   deterministic pass that looks at word-level silence gaps and moves a
   single last/first word when the gap straddles a silence. Decided during
   Phase C by measuring diarisation accuracy on the fixture set.

3. **Shadow flag default for production.** Spec says off-by-default in
   production. If we want a longer burn-in period post Phase F, we can flip
   the default back on for a week. User call.

4. **Segment ID stability scheme — resolved in §"Future: user
   segment-editing UI".** Positional `id` for LLM patches and React keys,
   durable `source_segment_ids` for cross-run persistence, reserved
   `user_segment_id` for the editing-UI spec. Listed here only so readers
   skimming the Open Questions section see that the earlier hashing
   proposal was discarded and where the current answer lives.

5. **Parakeet fix sequencing.** The Parakeet word-timestamp bug is out of
   scope here. If it gets fixed during this spec's timeline, Parakeet
   episodes will automatically move onto the new path: the
   `has_usable_segment_structure` helper starts returning True for them
   and the routing switch picks the primary per the flag. No coordination
   work needed.

6. **Pause ceiling for same-speaker merges.** Default `3.0s`. Too low and
   we fail to repair obviously-over-segmented diarisation; too high and
   we glue together unrelated topics. Decided during Phase B by running
   the eval harness's segment-boundary-drift metric against three
   candidate values (`2.0s`, `3.0s`, `5.0s`) on the fixture set.

7. **Prompt caching TTL and inter-batch pacing.** Anthropic's prompt cache
   has a 5-minute TTL. For very long episodes cleaned slowly (rate-limited
   providers), the cache may expire between batches. Measure during Phase
   C; if it matters, add an adaptive pacing step that fires batches in
   bursts.

8. **Shadow flag default for production.** Spec says off by default in
   production after Phase F. If we want a longer burn-in period we can
   flip it back on for a week — it is operator-togglable without a code
   change. User call.

## Rollback plan

The new pipeline is additive. Rollback at any point is a config flag flip
on the **routing** flag, not the shadow flag:

```bash
# Incident kill switch — routes every episode through the legacy cleaner
export THESTILL_CLEANUP_PIPELINE=legacy

# Optional: also stop running segmented cleanup as a shadow
export THESTILL_LEGACY_CLEANUP_SHADOW=0
```

The two flags are orthogonal by design (see §"Dual output during
debugging — two orthogonal flags"). The routing flag is the incident kill
switch — setting it to `legacy` makes the legacy `TranscriptCleaner` the
primary producer of `clean_transcript_path` for every eligible episode.
Parakeet-style degenerate inputs always take the legacy path regardless
of either flag, so this rollback is safe for the whole episode population.
The existing `clean_transcript_path` column and Markdown files are
untouched by the new path, so there is no reverse migration.

After Phase F, a deeper rollback means reverting Phase F's commits (they
are small and scoped to defaults + UI wiring). The underlying new code
paths stay callable. The `clean_transcript_json_path` column, the
`playback_time_offset_seconds` column, and the JSON sidecars remain
harmless if unreferenced.

**Non-revertable changes:** The schema migration in Phase D adds two
columns. SQLite column-adds are forward-only; to revert we would need a
`PRAGMA foreign_keys=OFF` + table rebuild, which is noisy. The columns
are cheap to keep even if unused, so the rollback plan is "leave them".
