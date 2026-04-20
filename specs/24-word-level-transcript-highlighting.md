# Word-Level Transcript Highlighting

**Status**: 📝 Draft
**Created**: 2026-04-19
**Updated**: 2026-04-19
**Priority**: Low (polish on top of spec #23; ships only if reading-along demand warrants the payload cost)

## Overview

Spec #23 highlights the currently-playing *segment*. This follow-up tightens
that loop to the *word* level: while audio plays, a soft tint rides the spoken
word across the active segment, like YouTube / Descript transcripts. Raw
Whisper/WhisperX already produces per-word timestamps, and the segmenter
records a `source_word_span` anchor on every `AnnotatedSegment` — the plumbing
to the client is the only missing piece.

## Goals

1. While the player is on an episode and actively playing, softly highlight
   the word whose `[start, end)` contains `currentTime` inside the
   currently-active segment.
2. Make the effect subtle — readers must still be able to scan ahead without
   the highlight feeling like a cursor chase.
3. Keep segment-level highlight (spec #23) as the unchanged fallback when
   word-level data is unavailable (e.g. older episodes, providers without
   word timestamps, Dalston in modes that drop words).
4. Respect `prefers-reduced-motion` — no slide or fade animations; static
   on/off tint only.
5. Behind an opt-in toggle ("Word highlight"), stored in `localStorage` next
   to `followPlayback` / `showFiller`.

## Non-goals

- Karaoke-style smooth-animated sweep or word-by-word scrolling.
- Reconciling cleaned text with raw words when they diverge (see
  "The alignment problem" below — we accept the limitation rather than solve
  it here).
- Exposing word data via public API stability guarantees — the DTO stays
  internal to the web frontend and may change between releases.
- Editing affordances (drag-to-reshape word boundaries, reassign speakers).
  That belongs in the transcript-editor spec, not here.

## Background

### What data already exists

- `thestill.models.transcript.Word` carries `word`, `start`, `end`,
  `probability`, `speaker` ([transcript.py:28](../thestill/models/transcript.py#L28)).
- `Segment.words: List[Word]` is populated by WhisperX, ElevenLabs, Google
  STT, and every provider that reports word-level timings. Whisper CPU mode
  (no alignment) leaves it empty — we must tolerate missing data per segment.
- `AnnotatedSegment.source_word_span` ([annotated_transcript.py:91](../thestill/models/annotated_transcript.py#L91))
  is an `(start_segment_id, start_word_index)` → `(end_segment_id, end_word_index)`
  pointer into the raw transcript's word stream. It is write-once and survives
  cleanup, so it's a durable anchor for word-level UI.
- The web API returns `AnnotatedTranscriptDump` ([types.ts:229](../thestill/web/frontend/src/api/types.ts#L229))
  with segments and `source_word_span`, but **not** the actual words — so the
  client has the pointer, not the data it points to.

### What the viewer needs

For each annotated segment the viewer has to render, it needs a flat list of
`{ w: string, s: number, e: number }` entries, ordered by `s`, scoped to the
words that fall inside that segment's `source_word_span` (offset-corrected via
`playback_time_offset_seconds`). A ~1h episode is ~10k words → ~600KB of raw
JSON, ~100-150KB gzipped. Acceptable as a one-off payload next to the ~200KB
segments dump already being sent, but large enough that we only fetch it when
the toggle is on.

## The alignment problem

Cleaning can rewrite a segment's text: fillers removed, stutters collapsed,
occasional grammar nudges (patches). So `annotated_segment.text` is **not**
the concatenation of the raw words inside `source_word_span`. Two ways out:

- **(a) Swap text on toggle.** When Word Highlight is on, the viewer renders
  raw words (with their spaces/punctuation) instead of the cleaned text for
  each segment. Pros: exact timestamps, trivial to implement, honest to the
  user about what we actually know. Cons: users lose some of the cleaning
  polish (capitalisation, repaired punctuation) in exchange for the
  highlight.

- **(b) Best-effort alignment.** Keep the cleaned text, tokenise it, and
  attribute each cleaned word to the nearest raw word via sequence alignment
  (Needleman-Wunsch on lowercased tokens). When confidence drops below a
  threshold, fall back to segment-level highlight for that segment. Pros:
  preserves cleaned text. Cons: complex, edge cases around collapsed
  stutters ("I-I-I think" → "I think"), and still lies when cleaning really
  did rewrite something.

**Default choice: (a)** — ship it simple. If users miss the cleaning polish,
(b) is a contained follow-up. The toggle label makes the tradeoff explicit
("Show raw words").

## Design

### Backend — new endpoint

```text
GET /api/episodes/{podcast_slug}/{episode_slug}/transcript/words
  → { episode_id, playback_time_offset_seconds, segments: [
        { segment_id, words: [{ w, s, e }, ...] },
        ...
      ] }
```

- `segment_id` matches `AnnotatedSegment.id` so the client joins trivially.
- `s` and `e` are absolute raw-audio seconds; the client adds
  `playback_time_offset_seconds` when deciding activity, same pattern as
  `findActiveSegmentIndex`.
- Segments with no raw words are omitted (not empty-array'd) so the client
  can cheaply fall back to segment-level for those.
- Served from the same raw-JSON sidecar the segmenter already reads; no new
  storage.
- 404 when the raw transcript is missing or has no word-level timestamps for
  this episode. The client treats 404 as "word data unavailable" and
  silently keeps segment-level highlight.

Pydantic models:

```python
class WordTimestamp(BaseModel):
    w: str
    s: float
    e: float

class SegmentWords(BaseModel):
    segment_id: int
    words: list[WordTimestamp]

class TranscriptWordsResponse(BaseModel):
    episode_id: str
    playback_time_offset_seconds: float
    segments: list[SegmentWords]
```

Short field names (`w`, `s`, `e`) are deliberate — they shave ~30% off the
JSON size on a 10k-word payload.

### Frontend — data plumbing

- New `useEpisodeTranscriptWords(podcastSlug, episodeSlug, { enabled })` hook
  alongside the existing `useEpisodeTranscript`. `enabled` is driven by the
  "Word highlight" toggle — when off we never issue the request, keeping the
  default page weight unchanged.
- Cached in React Query under `['episodes', podcastSlug, episodeSlug,
  'transcript', 'words']` with a long `staleTime` (transcript words don't
  change once produced).

### Frontend — viewer changes

1. A new toggle chip in the transcript toolbar: **Word highlight (raw)**.
   State persisted under `thestill:transcript:wordHighlight`, default off.
2. When the toggle is on and the hook has data:
   - Build `wordsBySegmentId: Map<segmentId, { w, s, e }[]>` once per data
     load.
   - In the active segment only, render the body as word-level `<span>`s
     from `wordsBySegmentId`; every other segment renders exactly as today.
     This keeps the DOM change scoped — inactive segments stay plain
     paragraphs, so tick cost stays O(words-in-active-segment), not
     O(all-words).
   - A helper `findActiveWordIndex(words, currentTime, offset, tolerance)`
     mirrors `findActiveSegmentIndex` (binary search on `s`, clamp by `e +
     tolerance`). Tolerance defaults to `0.15` — tighter than segment
     tolerance because word gaps are tiny.
3. Active word style: `bg-primary-100/80 rounded-sm` on a `<span>`. No
   animation. When `prefers-reduced-motion` is set, identical (there's
   nothing to simplify — it's already static).
4. When the toggle is on but a segment has no word data (null span or
   omitted from the response), render the cleaned text unchanged and rely
   on segment-level highlight. Log a one-time `console.debug` with
   `segment_id` for observability during rollout.

### Component contract change

```ts
interface SegmentedTranscriptViewerProps {
  transcript: AnnotatedTranscriptDump
  episodeId?: string | null
  onSeekRequest?: (seconds: number) => void
  // NEW
  words?: SegmentWordsMap  // Map<segmentId, WordTimestamp[]>
  wordHighlightEnabled?: boolean
}
```

The viewer stays dumb about fetching — `EpisodeDetail` owns the hook and
passes the built `Map` in. When `words` is `undefined`, word highlight is
effectively off regardless of the toggle (graceful degradation).

### Click-to-seek at word granularity (optional PR 2)

While we're rendering word spans anyway, extend click-to-seek: a click on a
word `<span>` seeks to `w.s + offset` instead of the segment start. Segment
click behaviour outside the active segment is unchanged.

## PR roadmap

| PR | Scope | New files | Touched files |
|---:|---|---|---|
| 1 | Backend words endpoint | `thestill/web/routers/transcript_words.py`, tests | `thestill/web/app.py`, `specs/02-api-reference.md` |
| 2 | Frontend data hook | `hooks/useEpisodeTranscriptWords.ts`, tests | `api/client.ts`, `api/types.ts` |
| 3 | Viewer toggle + active-word highlight | `utils/wordSearch.ts` + test | `SegmentedTranscriptViewer.tsx`, `EpisodeDetail.tsx` |
| 4 | Word-level click-to-seek | — | `SegmentedTranscriptViewer.tsx` |

PRs 1–3 are the shippable unit. PR 4 is opt-in polish.

## Performance considerations

- Payload: ~100-150KB gzipped per 1h episode; only fetched when the toggle
  is on; React Query caches it for the session.
- Tick cost: word highlight re-renders only the active segment's `<p>`. A
  typical segment is 20-50 words, so React reconciles ≤100 `<span>`s per
  tick. `React.memo` on segment rows already prevents non-active segments
  from re-rendering.
- `findActiveWordIndex` is O(log n) on the active segment's word array
  (typically 20-50 entries).

## Accessibility

- The active-word `<span>` gets `aria-current="true"` so assistive tech can
  report progress. No `aria-live` — screen readers already narrate audio;
  announcing every word would be hostile.
- Keep the existing segment-level `aria-label` on the clickable container.
- Contrast: the soft-tint background must meet WCAG AA against the body
  text. `bg-primary-100/80` on `text-gray-800` is ~7:1 — passes.

## Testing

- **Backend**: unit tests for the new endpoint — happy path, missing raw
  words (404), mismatched `source_word_span` indices (500 with clear
  error). Integration test: same episode renders segmented transcript **and**
  words, counts align.
- **Frontend**:
  - `wordSearch.test.ts` — binary-search edges (before first word,
    in-gap, exact boundary, offset applied, empty list).
  - `SegmentedTranscriptViewer.test.tsx` — with `words` prop + toggle on,
    rendering the active segment exposes a `data-word-active="true"`
    `<span>` that shifts as `PlayerTimeContext` advances.
  - Toggle persistence via `localStorage`, mirroring existing filler/follow
    tests.
- Manual smoke: play an episode with WhisperX word timestamps; verify the
  tint moves cleanly on paragraph transitions; verify fallback to
  segment-level on an ElevenLabs-transcribed older episode where words may
  be missing.

## Open questions

- **Do we persist the chosen toggle per-episode or globally?** Current
  `localStorage` scheme is global; a heavy reader might want word-level on
  some episodes (reading-along) and off on others (skimming). Deferred —
  ship global first, revisit if users ask.
- **Should the toggle label say "Show raw words" or "Word highlight
  (exact)"?** The first is honest but terse; the second hides the trade-off.
  Decide during PR 3 review once we can feel the UX.
- **Whisper-CPU (no word timestamps) episodes**: the endpoint 404s, the UI
  toggle becomes a no-op. Do we disable the toggle chip entirely on such
  episodes, or let it remain a silent no-op? Small UX call — lean toward
  disabling the chip with a tooltip explaining why.
