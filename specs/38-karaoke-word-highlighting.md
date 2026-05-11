# Word-Level Reading Cursor (chip: "Karaoke")

**Status**: ✅ Shipped (PRs 1–5). PRs 6–7 intentionally deferred.
**Created**: 2026-05-11
**Updated**: 2026-05-11
**Priority**: Medium (visible playback polish; builds on specs #22 / #23)
**Supersedes**: [24-word-level-transcript-highlighting.md](24-word-level-transcript-highlighting.md)

## Intended outcome

While an episode is playing, the segmented transcript shows a **two-tone reading
cursor** inside the active segment: words the audio has crossed render in full
strength, words still to come render in a muted tint. The visual is a plain
text-colour swap — no gradient, no shadow, no animated wipe.

The original draft of this spec proposed an Apple-Music-Sing-style gradient
that wiped 0→100% across each word. During implementation we tried it, then
backed it out in favour of the simpler two-tone scheme — the gradient added
visual noise without making "where am I in this segment?" any clearer.

Concretely, when a user lands on an episode and presses play:

1. The active segment is highlighted (already done by spec #23).
2. Inside the active segment, the raw-word body replaces the cleaned text.
   Each word is one of two colours: muted (`text-gray-400`) for words still
   to come, full-strength (`text-gray-900`) for words the audio has reached.
3. The currently-spoken word is **visually identical** to other read words;
   only `aria-current="true"` distinguishes it for assistive tech.
4. **The highlight does not regress on pauses.** A separate "read-up-to"
   cutoff records the highest-indexed word whose start the audio has crossed
   and does not unset on silences. Backward seeks correctly pull the cutoff
   down because the audio cursor moves before the earlier words' starts.
5. Episodes without word timestamps (Whisper-CPU mode, ElevenLabs without
   alignment, very old episodes) fall back silently to the existing
   segment-level highlight.

This is opt-in behind a toolbar chip (**"Karaoke"**, persisted in
`localStorage`), default off, so the extra ~100–150KB word-data payload is
only paid for when a user wants it.

### Non-goals

- A gradient / wipe inside a word. Tried, removed during PR 4 review.
- Word-level click-to-seek. The spec's PR 6 was deferred — the cursor is
  read-only. Segment-level click-to-seek (jump + play) still works on the
  paragraph container around the words.
- Reduced-motion handling. The original spec needed it because of the wipe;
  the two-tone design has no motion to suppress, so the branch is gone.
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
   GET /api/podcasts/{slug}/episodes/{slug}/transcript/words
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
              └── ContentSegment (the active one)
                    │
                    ├── useKaraokeActiveWordIdx(words, offset, getCurrentTime)
                    │     └── rAF loop → setState only on index change
                    │     └── returns { activeIdx, readUpTo }
                    │
                    └── KaraokeWord <span> × N
                          └── read ? text-gray-900 : text-gray-400
                          └── aria-current iff i === activeIdx
```

Three design decisions deserve the call-out:

1. **`readUpTo` is tracked separately from `activeIdx`.** During a long
   pause the audio cursor sits past the last word's `end + tolerance`, so
   `findActiveWordIndex` correctly returns `-1` ("no word is being spoken
   right now"). Without a separate cutoff that bug surfaces as "the whole
   segment flashes back to grey on every silence." `readUpTo` is the
   highest-indexed word whose `start + offset ≤ currentTime`, with no
   upper bound, so silences leave the visual stable.
2. **The active-word index runs off `requestAnimationFrame`, not
   `usePlayerTime()`.** The browser's `timeupdate` event ticks at ~4 Hz,
   which can miss whole words at normal speech rates (3–5 words/sec ⇒
   200–333 ms/word). rAF reads `audioRef.current.currentTime` directly via
   a new `getCurrentTime()` on `PlayerContext`, so transitions land within
   ~16 ms of the audio cursor. React only re-renders when the index
   actually changes, so the host renders at per-word-transition rate, not
   60 fps.
3. **The visual is plain text colour, not a gradient or background.** Two
   Tailwind classes, swapped on each `<span>`. No CSS variable, no inline
   style, no compositor concerns. The original draft of this spec proposed
   a `linear-gradient(... calc(var(--karaoke-progress) * 100%) ...)` wipe;
   it was implemented, then discarded during review for adding motion
   without adding clarity.

### Backend — new endpoint

```text
GET /api/podcasts/{podcast_slug}/episodes/{episode_slug}/transcript/words
  → 200 { status, timestamp, episode_id, playback_time_offset_seconds,
          segments: [
            { segment_id, words: [{ w, s, e }, ...] }, …
          ] }
  → 404 when the raw transcript is absent, the segmented JSON sidecar is
        absent, or no segment has any resolvable word timestamps
  → 500 when a ``source_word_span`` references a non-existent raw segment
        or out-of-bounds word index (structured error log entry)
```

(The original draft proposed `/api/episodes/{slug}/{slug}/...`. The real
mount slots in next to the existing
`/api/podcasts/{slug}/episodes/{slug}/transcript` route in `api_podcasts.py`
so the URL pattern stays consistent.)

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

Hook lives alongside the other episode hooks in
`thestill/web/frontend/src/hooks/useApi.ts`:

```ts
useEpisodeTranscriptWords(podcastSlug, episodeSlug, enabled: boolean):
  UseQueryResult<KaraokeWordsByEpisode | null>
```

- `enabled` is driven by the karaoke toggle; when off, no request is fired,
  so the default page weight is unchanged.
- Cached in React Query under `['episodes', podcastSlug, episodeSlug,
  'transcript', 'words']` with `staleTime: 5 * 60_000` — word timestamps
  don't change once produced.
- Builds `Map<segmentId, WordTimestamp[]>` on the way in so the viewer
  doesn't repeat the work per render.
- **404 is a value, not an error.** `getEpisodeTranscriptWords` in
  `api/client.ts` resolves to `null` on 404 instead of throwing. The hook
  forwards `null` through `data`; the parent uses
  `isFetched && data === null` to flip the chip to disabled-with-tooltip.

API types added to [types.ts](../thestill/web/frontend/src/api/types.ts):

```ts
export interface WordTimestamp { w: string; s: number; e: number }
export interface SegmentWords { segment_id: number; words: WordTimestamp[] }
export interface TranscriptWordsResponse {
  status: string
  timestamp: string
  episode_id: string
  playback_time_offset_seconds: number
  segments: SegmentWords[]
}
export interface KaraokeWordsByEpisode {
  episodeId: string
  offset: number
  wordsBySegmentId: Map<number, WordTimestamp[]>
}
```

### Frontend — the karaoke driver

`thestill/web/frontend/src/hooks/useKaraokeActiveWordIdx.ts`:

```ts
interface KaraokeWordCursor {
  activeIdx: number   // current word; -1 during pauses
  readUpTo: number    // highest word whose start has been crossed
}

function useKaraokeActiveWordIdx(
  words: ReadonlyArray<WordTimestamp> | null,
  offset: number,
  getCurrentTime: () => number,
): KaraokeWordCursor
```

Behaviour:

- Lazy `useState` initializer computes the cursor synchronously so the
  first render is already correct — without it every active-segment swap
  would flash through `{ -1, -1 }` for one frame.
- The effect runs a `requestAnimationFrame` loop that reads
  `getCurrentTime()` each frame, computes both indices via
  `findActiveWordIndex` (with the default 0.15s tolerance) and
  `findReadUpToIndex` (a parallel binary search with no upper bound), and
  calls `setCursor` only when one of the two values has actually changed.
- `getCurrentTime` is a stable getter on `PlayerContext` returning
  `audioRef.current?.currentTime ?? 0`. The original spec called for
  injecting it as a prop; promoting it to the context value made the
  call site cleaner and matches how `usePlayer`/`usePlayerTime` are
  already split.

There is no per-word hook anymore. The original draft proposed a
`useKaraokeProgress` that wrote `--karaoke-progress` to each active word's
inline style via rAF; that was deleted when the wipe was dropped. With the
two-tone visual the only cross-frame data needed is the cursor itself.

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

`thestill/web/frontend/src/components/KaraokeWord.tsx`:

```tsx
interface Props {
  word: WordTimestamp
  read: boolean      // i <= readUpTo
  isActive: boolean  // i === activeIdx
}
```

- Renders `<span data-karaoke-word>` with the word text.
- `className={read ? 'text-gray-900' : 'text-gray-400'}` — full-strength
  for read words, muted for unread. The currently-spoken word is in the
  same colour as already-read words; only `aria-current` distinguishes
  it for assistive tech.
- `aria-current={isActive ? 'true' : undefined}`.
- Wrapped in `memo` so unchanged words don't re-render when the cursor
  advances.
- **No `onClick`, no `tabIndex`, no `role="button"`.** Click-to-seek
  (spec's PR 6) is deferred — see "What didn't ship" below.

### Click-to-seek behaviour — deferred

The original spec's PR 6 wired word-level click-to-seek with a "segments
jump+play, words scrub only" model: segment clicks call
`player.seek()` + `player.resume()` (existing behaviour), word clicks would
call `player.seek()` only and `e.stopPropagation()` to suppress the parent
segment's resume. The implementation is small but the design felt like
"feature because the spec said so" rather than a clear user need once the
gradient wipe was dropped — the tiny click targets aren't compelling on
their own. Reopen if usage feedback wants precision scrubbing inside a
segment; until then the segment-level click handler (jump + play on the
paragraph) is the only seek affordance from the viewer.

### Auto-scroll behaviour

The follow-playback hook from #23 already scrolls the active segment into
view. Karaoke does **not** add a sub-scroll to keep the active word in
viewport — the active segment is at most ~2 paragraphs of words, which fits
the visible area at typical widths. Sub-word scrolling would feel jittery.
Revisit only if usage telemetry shows users complaining.

### Accessibility

- `aria-current="true"` on the active word; no `aria-live` (the audio
  itself is the live region — duplicating it would be hostile).
- Text contrast: `text-gray-900` on white is ~17:1, `text-gray-400` is
  ~5.61:1 — both pass WCAG AA for body text.
- No `prefers-reduced-motion` handling. The two-tone visual has no motion;
  word transitions are atomic colour swaps. The original spec needed a
  reduced-motion branch because of the wipe — dropping the wipe dropped
  the requirement.
- No keyboard focus on words. They aren't interactive (no click-to-seek
  in this revision).

### Performance

- Payload: ~100–150KB gzipped per 1h episode, only when the chip is on.
- React reconcile cost: only the active segment renders `<span>`s. Typical
  segment = 20–50 words ⇒ ≤100 spans per active-word transition. The
  `KaraokeWord` `memo` keeps unchanged words from re-rendering when the
  cursor advances.
- rAF cost: two `O(log n)` binary searches per frame (one for
  `activeIdx`, one for `readUpTo`) plus a single setState call only when
  one of the two values has changed. At typical speech (3–5 transitions
  per second), the host re-renders ~5 Hz; the rAF itself is otherwise a
  pure read.
- Binary search arrays are the active segment's words only (n ≈ 20–50).

### Fallback matrix

| Condition | Behaviour |
|---|---|
| Toggle off | Default. Spec #23 segment highlight only. No word fetch. |
| Toggle on, endpoint 404 | Chip auto-unchecks and renders disabled with tooltip. Falls back to segment highlight. |
| Toggle on, segment has no word data | That segment uses cleaned text + segment highlight. Adjacent segments still render words. |
| Toggle on, player paused | `activeIdx` becomes -1 (no `aria-current`), `readUpTo` stays at the last-crossed word, so already-read words remain full-strength. |
| Toggle on, audio cursor in a long gap | Same as the paused case — `readUpTo` holds, visual stable. |
| Toggle on, audio seeked backward | `readUpTo` regresses to match. Previously-read words flip back to muted ahead of the new cursor. |

## What shipped

All landed in a single commit (`feat: word-level transcript highlighting
(spec #38 PRs 1–5)`) — the PR table below is the original roadmap with
shipped state annotated.

| PR | Scope | Status |
|---:|---|:---:|
| 1 | Backend `transcript/words` endpoint + DTOs + integration tests | ✅ |
| 2 | Frontend data plumbing (`useEpisodeTranscriptWords`, 404→null) | ✅ |
| 3 | Active-word tracking + `wordSearch` + shared `findActiveIndex` generic | ✅ |
| 4 | `KaraokeWord` component + viewer integration | ✅ |
| 5 | Toolbar chip, persistence, disabled-with-tooltip on 404, auto-uncheck | ✅ |
| 6 | Word-level click-to-seek | ❌ deferred |
| 7 | Reduced-motion fallback + a11y audit | ❌ no longer applicable |

PR 3's `useKaraokeProgress` rAF driver was built and then deleted along
with the wipe; it was replaced by `useKaraokeActiveWordIdx` returning
`{ activeIdx, readUpTo }`. PR 7's reduced-motion handling went with it —
the two-tone design has no motion to suppress.

### Open items — resolved

- **Gradient colour / opacity** — moot. No gradient. Words use
  `text-gray-900` (read) and `text-gray-400` (unread).
- **Chip label** — shipped as **"Karaoke"** (matches the spec's filename
  and feels right next to "Show filler" / "Follow playback"). "Sing along"
  was on the table; "Karaoke" won on familiarity.
- **`getCurrentTime` vs `usePlayerTime`** — `usePlayerTime` is 4 Hz
  (browser `timeupdate`), confirmed empirically. rAF wins for word-level
  granularity. `getCurrentTime` was promoted to a stable method on
  `PlayerContextValue` instead of being injected as a prop.

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

- `hooks/useKaraokeActiveWordIdx.test.ts`:
  - `null` words → `{ activeIdx: -1, readUpTo: -1 }`.
  - `currentTime` inside word N → both indices return N.
  - `currentTime` in a long gap past the tolerance → `activeIdx = -1` but
    `readUpTo` stays at the last word the audio crossed.
  - non-zero offset applied symmetrically.

  Caveat: the rAF effect deps include `getCurrentTime`. Tests must pass a
  **stable** function reference (declare outside `renderHook`) — an inline
  arrow gets fresh identity per render, triggers infinite effect re-fires,
  and OOMs the test worker. The hook is robust to this in production
  because `PlayerContext.getCurrentTime` is memoized.

### Frontend component tests

- `components/KaraokeWord.test.tsx`:
  - inactive → no `aria-current`, muted colour class.
  - active → `aria-current="true"`, full-strength colour class.
  - read+active vs read+inactive render the same colour (the spec's
    "currently-read word matches already-read" rule).
  - never emits inline `style.backgroundImage` (catches a regression to
    the old gradient design).

- `components/SegmentedTranscriptViewer.karaoke.test.tsx`:
  - With `karaokeEnabled=true` and a `words` map: the active segment
    renders word spans; inactive segments render cleaned paragraphs.
  - As `getCurrentTime` advances across word boundaries, `aria-current`
    moves from word N to word N+1.
  - With `karaokeEnabled=true` but `karaokeWords=null` (404 case): viewer
    falls back to segment-level highlight; no word spans in the DOM.
  - Chip rendering: shown when `onKaraokeToggle` is provided, hidden
    otherwise; disabled+tooltip when `karaokeChipDisabled` is true; fires
    `onKaraokeToggle` on click.
  - The file is split out from `SegmentedTranscriptViewer.test.tsx`
    because it mocks `PlayerContext` (the karaoke render path needs an
    active-segment state that the real `PlayerProvider` can't easily
    reach in jsdom).

### Manual smoke

- Play an episode known to carry WhisperX word timestamps; verify the
  word colour transitions track the audio at speech rate.
- Play an ElevenLabs-transcribed older episode where words are missing on
  some segments; verify silent fallback to segment-level highlight per
  segment (not whole-episode).
- Pause mid-segment; verify already-read words stay full-strength and the
  segment doesn't snap back to grey.
- Seek backward inside a segment; verify words past the new cursor flip
  back to muted.
- Mobile Safari + Chrome Android: verify the disabled-chip tooltip is
  reachable on touch (longer-press affordance).

### CI gates

- `make check` (black, isort, pylint, mypy) green.
- 255 tests pass (56 backend integration + 199 frontend).

## Migration & rollout

- No data migration. Endpoint is read-only over existing raw transcript
  files.
- Feature is opt-in client-side. No backend flag needed.
- Shipped under feat branch `feat/38-karaoke` (PR #88), targeted at
  `main`. Status flipped from Draft → Shipped on merge.
