# Karaoke-Style Word Highlighting

**Status**: 📝 Draft
**Created**: 2026-05-11
**Updated**: 2026-05-11
**Priority**: Medium (visible playback polish; builds on specs #22 / #23)
**Supersedes**: [24-word-level-transcript-highlighting.md](24-word-level-transcript-highlighting.md)

## Intended outcome

While an episode is playing, the segmented transcript shows a **karaoke-style
highlight** that smoothly fills the currently-spoken word left-to-right as
audio advances through that word's `[start, end)` window — like Apple Music
Sing, Spotify lyric sync, or the Shopify product narration the user referenced.

Concretely, when a user lands on an episode and presses play:

1. The active segment is highlighted (already done by spec #23).
2. Inside the active segment, the active word gets a coloured fill that **wipes
   from 0% to 100% across the duration of that word** — not a binary on/off
   snap. Inactive words in the same segment stay plain text.
3. The transition between consecutive words is seamless: as one word's fill
   reaches 100%, the next word starts at 0% on the same animation frame.
4. Clicking any word seeks the player to that word's start time.
5. Users with `prefers-reduced-motion: reduce` see a static tint on the active
   word (the spec #24 behaviour), never a wipe.
6. Episodes without word timestamps (Whisper-CPU mode, ElevenLabs without
   alignment, very old episodes) fall back silently to the existing
   segment-level highlight.

This is opt-in behind a toolbar chip (**"Karaoke"**, persisted in
`localStorage`), default off, so the extra ~100–150KB word-data payload is
only paid for when a user wants it.

### Non-goals

- Letter-by-letter animation — words are atomic.
- Cross-fading or animated transitions when the active word changes
  segments (the segment highlight already handles that).
- Reconciling cleaned text with raw words when cleaning rewrote a segment.
  We swap to raw words for the active segment when karaoke is on, same
  honest-about-what-we-know approach #24 took.
- Editing affordances (drag-to-reshape word boundaries) — belongs in a
  future transcript-editor spec.
- A public API stability guarantee on the new endpoint — the response shape
  is internal to the web frontend and may change between releases.

## How we're going to do it

### Architecture overview

```text
                 ┌────────────────────────────┐
                 │ raw transcript JSON sidecar│
                 │  (Whisper/WhisperX/etc.)   │
                 └────────────┬───────────────┘
                              │ read same file the segmenter reads
                              ▼
            GET /api/episodes/{slug}/{slug}/transcript/words
                              │
                              ▼
       useEpisodeTranscriptWords()  ← React Query, enabled by toggle
                              │
                              ▼
         wordsBySegmentId: Map<segmentId, WordTimestamp[]>
                              │
                              ▼
       SegmentedTranscriptViewer (active segment only)
              │
              ├── findActiveWordIndex(words, t, offset)   ← O(log n)
              │
              └── KaraokeWord <span>
                    └── useKaraokeProgress(ref, word, t0)
                          └── rAF loop writes --karaoke-progress
                              to ref.style; no React re-render
```

Two design decisions deserve the call-out:

1. **The wipe is a CSS background gradient, not an animated width.** The
   active `<span>` gets `background: linear-gradient(to right,
   var(--karaoke-color) calc(var(--karaoke-progress) * 100%),
   transparent 0)`. A gradient stop is composited on the GPU, so the wipe
   runs at 60fps without layout thrash. Animating `width` on a child element
   would force a reflow per frame.
2. **The progress value is driven by `requestAnimationFrame` writing
   directly to `ref.current.style.setProperty('--karaoke-progress', …)`.**
   React never re-renders on every frame — it only reconciles when the
   *active word index* changes. This is the same separation `PlayerContext`
   already uses between `usePlayer()` (state) and `usePlayerTime()` (tick).

### Backend — new endpoint

Same shape as #24 proposed; carried forward here so the spec is standalone.

```text
GET /api/episodes/{podcast_slug}/{episode_slug}/transcript/words
  → 200 { episode_id, playback_time_offset_seconds, segments: [
            { segment_id, words: [{ w, s, e }, ...] }, …
          ] }
  → 404 when the raw transcript is absent or has no word-level timestamps
```

- `segment_id` matches `AnnotatedSegment.id`
  ([annotated_transcript.py:75](../thestill/models/annotated_transcript.py#L75))
  so the client joins trivially.
- `s` and `e` are absolute raw-audio seconds. The client adds
  `playback_time_offset_seconds` before comparing to `currentTime`, mirroring
  [transcriptSearch.ts](../thestill/web/frontend/src/utils/transcriptSearch.ts).
- Segments with no word data are omitted (not empty-array'd), so the client
  falls back cheaply.
- New file: `thestill/web/routes/api_transcript_words.py` matching the
  `api_*.py` naming used elsewhere
  (e.g. [api_top_podcasts.py](../thestill/web/routes/api_top_podcasts.py)).

Pydantic DTOs (short field names shave ~30% off a 10k-word payload):

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

Source data: `thestill.models.transcript.Word`
([transcript.py:28](../thestill/models/transcript.py#L28)) already carries
`word`, `start`, `end`, `probability`, `speaker`. The endpoint reads the
raw sidecar JSON the segmenter consumes — no new storage, no new migration.

### Frontend — data plumbing

New hook: `thestill/web/frontend/src/hooks/useEpisodeTranscriptWords.ts`.

```ts
useEpisodeTranscriptWords(podcastSlug, episodeSlug, { enabled }):
  { data: SegmentWordsMap | undefined, isLoading, isError }
```

- `enabled` is driven by the karaoke toggle; when off, no request is fired,
  so the default page weight is unchanged.
- Cached in React Query under `['episodes', podcastSlug, episodeSlug,
  'transcript', 'words']` with a long `staleTime` — word timestamps don't
  change once produced.
- Builds `Map<segmentId, WordTimestamp[]>` on the way in so the viewer
  doesn't repeat the work per render.

API types added to [types.ts](../thestill/web/frontend/src/api/types.ts):

```ts
export interface WordTimestamp { w: string; s: number; e: number }
export interface SegmentWords { segment_id: number; words: WordTimestamp[] }
export interface TranscriptWordsDump {
  episode_id: string
  playback_time_offset_seconds: number
  segments: SegmentWords[]
}
export type SegmentWordsMap = Map<number, WordTimestamp[]>
```

### Frontend — the karaoke driver

New file: `thestill/web/frontend/src/hooks/useKaraokeProgress.ts`.

```ts
function useKaraokeProgress(
  ref: RefObject<HTMLElement>,
  word: WordTimestamp | null,
  offset: number,
): void
```

Behaviour:

- When `word` is `null`, sets `--karaoke-progress: 0` and exits.
- Otherwise starts a `requestAnimationFrame` loop. Each frame reads
  `audioElement.currentTime` (provided via a small `getCurrentTime`
  injection, not the React state in `usePlayerTime`, to avoid the React
  60fps tax — same pattern the wave-form players use), computes
  `progress = clamp01((t - (word.s + offset)) / (word.e - word.s))`, and
  calls `ref.current.style.setProperty('--karaoke-progress', String(p))`.
- Stops the rAF on unmount or when `word` changes (the outer
  `KaraokeWord` is keyed by word index, so React unmount-mounts on
  word change, which means each loop owns exactly one word's lifetime —
  no manual cleanup needed beyond the rAF cancel in the effect's
  cleanup).

If `prefers-reduced-motion: reduce`, the hook bypasses rAF and snaps
progress to `1` when `t >= word.s + offset` (static tint, no wipe).

### Frontend — viewer changes

In [SegmentedTranscriptViewer.tsx](../thestill/web/frontend/src/components/SegmentedTranscriptViewer.tsx):

1. New props (purely additive; `undefined` ⇒ karaoke off, viewer behaves
   exactly as today):

    ```ts
    words?: SegmentWordsMap
    karaokeEnabled?: boolean
    ```

2. New toolbar chip **Karaoke** alongside the existing "Follow playback" /
   "Show filler" chips. State persisted under
   `thestill:transcript:karaoke`, default off. The chip is **disabled with a
   tooltip** ("No word timestamps for this episode") when the hook resolved
   to 404. This is the open question #24 left unanswered; we resolve it
   here.
3. When `karaokeEnabled && words` and a segment is **active**:
   - Render its body as a sequence of `KaraokeWord` `<span>`s built from
     `words.get(segment.id)`. Segments without word data render the
     cleaned text unchanged (segment-level highlight applies).
   - Use raw words (with raw spacing/punctuation) for the active segment.
     This is the "Swap text on toggle" approach spec #24 already chose; we
     keep it.
4. Inactive segments always render cleaned text — DOM cost stays
   O(words-in-active-segment), not O(all-words).
5. `findActiveWordIndex(words, t, offset, tolerance=0.15)` in
   `utils/wordSearch.ts` mirrors `findActiveSegmentIndex` — binary search on
   `s`, clamp by `e + tolerance`. Tighter tolerance than the segment search
   because word gaps are sub-second.

### `KaraokeWord` component

New file: `thestill/web/frontend/src/components/KaraokeWord.tsx`.

```tsx
interface Props {
  word: WordTimestamp
  isActive: boolean
  offset: number
  onSeek: (seconds: number) => void
}
```

- Renders `<span data-karaoke style={{ '--karaoke-progress': 0 }}>`.
- When `isActive`, calls `useKaraokeProgress(ref, word, offset)` so the
  CSS variable advances.
- Tailwind classes apply the gradient:
  `bg-[linear-gradient(to_right,theme(colors.primary.500)_calc(var(--karaoke-progress)*100%),transparent_0)]`
  (exact class name TBD during PR; the principle is what matters — keep
  the wipe purely in CSS).
- `aria-current={isActive ? 'true' : undefined}` for assistive tech.
- `role="button"`, `tabIndex={0}`, Enter/Space handlers → `onSeek(word.s +
  offset)`.

### Click-to-seek behaviour

`EpisodeDetail` owns the seek decision (same pattern as #23 PR2): if the
active player track matches `episode.id`, call `player.seek()`; otherwise
`player.play(track, { startSeconds })`. Preserves play/pause state — clicking
a word in a paused transcript does not start audio.

### Auto-scroll behaviour

The follow-playback hook from #23 already scrolls the active segment into
view. Karaoke does **not** add a sub-scroll to keep the active word in
viewport — the active segment is at most ~2 paragraphs of words, which fits
the visible area at typical widths. Sub-word scrolling would feel jittery.
Revisit only if usage telemetry shows users complaining.

### Accessibility

- Static tint (no wipe) under `prefers-reduced-motion`.
- `aria-current="true"` on the active word; no `aria-live` (the audio
  itself is the live region — duplicating it would be hostile).
- Contrast on the wipe colour must meet WCAG AA against the body text.
  `primary.500` on `text-gray-800` is already used in the speaker palette
  and passes — but we re-verify with the exact opacity used.
- Keyboard parity with mouse for word click-to-seek (Enter/Space).
- `focus-visible:ring-2 ring-primary-400` on the word `<span>` so power
  users can tab through.

### Performance

- Payload: ~100–150KB gzipped per 1h episode, only when the chip is on.
- React reconcile cost: only the active segment renders `<span>`s. Typical
  segment = 20–50 words ⇒ ≤100 spans per active-word transition.
- rAF cost: one update per frame, writing one CSS variable on one element.
  Negligible — comparable to a typical video scrub UI.
- Binary search `findActiveWordIndex` is O(log n) over the active
  segment's array (n ≈ 20–50).

### Fallback matrix

| Condition | Behaviour |
|---|---|
| Toggle off | Default. Spec #23 segment highlight only. No word fetch. |
| Toggle on, endpoint 404 | Chip disabled with tooltip. Falls back to segment highlight. |
| Toggle on, segment has no word data | That segment uses cleaned text + segment highlight. Adjacent segments still karaoke. |
| Toggle on, `prefers-reduced-motion` | Static tint snap on active word, no wipe. |
| Toggle on, player paused | Active word stays at its current progress (rAF still ticks but `currentTime` is constant). |

## Todo

PR-by-PR roadmap; each PR is independently shippable and revertable.

| PR | Scope | New files | Touched files |
|---:|---|---|---|
| 1 | Backend `transcript/words` endpoint | `thestill/web/routes/api_transcript_words.py`, integration test | `thestill/web/app.py`, `02-api-reference.md` |
| 2 | Frontend data plumbing | `hooks/useEpisodeTranscriptWords.ts` + test | `api/client.ts`, `api/types.ts` |
| 3 | `useKaraokeProgress` rAF driver + `wordSearch` | `hooks/useKaraokeProgress.ts` + test, `utils/wordSearch.ts` + test | — |
| 4 | `KaraokeWord` component + viewer integration | `components/KaraokeWord.tsx` + test | `SegmentedTranscriptViewer.tsx`, `EpisodeDetail.tsx` |
| 5 | Toolbar chip, persistence, disabled-with-tooltip on 404 | — | `SegmentedTranscriptViewer.tsx`, `EpisodeDetail.tsx` |
| 6 | Word-level click-to-seek | — | `KaraokeWord.tsx`, `EpisodeDetail.tsx` |
| 7 | Reduced-motion fallback + a11y audit | — | `useKaraokeProgress.ts`, `KaraokeWord.tsx` |

PRs 1–5 are the shippable unit. PRs 6–7 are polish but should land before
removing the "Draft" tag on this spec.

### Open items to resolve during implementation

- Pick the exact gradient colour and opacity (likely
  `primary.500 / 30%`-ish — verify against light + dark themes).
- Decide whether the chip label says "Karaoke" or "Sing along" — settle in
  PR 5 review when we can feel it.
- Confirm `getCurrentTime` injection vs `usePlayerTime` empirically — if
  the latter's tick frequency is already 60fps, we can skip the rAF
  injection. Profile in PR 3.

## Tests

### Backend

- `tests/integration/web/test_api_transcript_words.py`:
  - happy path: known episode → 200 with words shaped per the Pydantic
    contract, `playback_time_offset_seconds` echoed correctly.
  - episode with raw transcript missing word timestamps → 404.
  - mismatched `source_word_span` index (corrupted data) → 500 with a
    structured error log entry (per
    [03-error-handling.md](03-error-handling.md)).
  - offset sanity: when the raw transcript carries
    `playback_time_offset_seconds != 0`, the returned absolute `s` / `e`
    values still match the raw words (offset is response metadata, not
    applied server-side).

### Frontend unit tests

- `utils/wordSearch.test.ts`:
  - empty word list → `-1`.
  - `t` before first word's start → `-1`.
  - `t` exactly at a boundary → returns that word.
  - `t` in a gap between two words → last word whose `s + offset ≤ t`,
    only if `e + offset + tolerance ≥ t`; otherwise `-1`.
  - non-zero offset applied symmetrically.

- `hooks/useKaraokeProgress.test.ts`:
  - at `t === word.s + offset`, progress is `0`.
  - at `t === word.e + offset`, progress is `1`.
  - clamped: `t < word.s` ⇒ `0`; `t > word.e` ⇒ `1`.
  - `prefers-reduced-motion: reduce` ⇒ progress snaps to `0` or `1`, no
    intermediate values (mock `matchMedia`).
  - rAF is cancelled on unmount (`cancelAnimationFrame` called with the
    handle returned by the last `requestAnimationFrame`).

### Frontend component tests

- `components/KaraokeWord.test.tsx`:
  - `isActive=false` → renders plain word, no `aria-current`, no
    `--karaoke-progress` style override.
  - `isActive=true` → renders with `aria-current="true"` and starts a rAF
    loop (assert via spy on `requestAnimationFrame`).
  - Enter / Space / click → `onSeek` called with `word.s + offset`.

- `components/SegmentedTranscriptViewer.test.tsx` (extend existing):
  - With `karaokeEnabled=true` and a `words` map: the active segment
    renders word spans; inactive segments render cleaned paragraphs.
  - As `usePlayerTime()` advances across word boundaries, `aria-current`
    moves from word N to word N+1.
  - With `karaokeEnabled=true` but `words` undefined (404 case): toolbar
    chip renders disabled; viewer falls back to segment-level highlight;
    no word spans in the DOM.
  - Toggle persistence: flipping the chip writes
    `thestill:transcript:karaoke`; mounting with the key set restores
    the chip state. Mirrors the existing `followPlayback` / `showFiller`
    tests.

### Manual smoke

- Play an episode known to carry WhisperX word timestamps; verify the wipe
  rides cleanly across the active segment and transitions cleanly at
  paragraph breaks.
- Play an ElevenLabs-transcribed older episode where words are missing on
  some segments; verify silent fallback to segment-level highlight per
  segment (not whole-episode).
- Toggle reduced-motion in the OS preferences pane mid-playback; verify
  the wipe stops and the active word stays statically tinted.
- Mobile Safari + Chrome Android: verify the gradient wipe doesn't drop
  frames at 60fps and the disabled-chip tooltip is reachable on touch
  (longer-press affordance).
- Click random words in a paused episode; verify seek lands on the word
  and playback does **not** start.

### CI gates

- `make check` (black, isort, pylint, mypy) green on each PR.
- New tests added per PR; coverage targets in
  [04-testing.md](04-testing.md) preserved.

## Migration & rollout

- No data migration. Endpoint is read-only over existing raw transcript
  files.
- Feature is opt-in client-side. No backend flag needed.
- After PRs 1–5 land and karaoke is exercised on real episodes for a
  week, remove the "Draft" tag and update this spec's status to
  ✅ Complete.
