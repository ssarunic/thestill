# Video Player for `<podcast:alternateEnclosure>` Episodes

**Status**: ⛔ Superseded by [#62 youtube-video-rendition](62-youtube-video-rendition.md) (2026-07-22) — the page-scoped embed predates spec #61's unified session; #62 rebuilds this on the engine-adapter boundary. The YouTube-ID extraction rules and variant-selection notes below remain the reference for #62 §4.
**Created**: 2026-05-13
**Updated**: 2026-05-13
**Priority**: Low (small surface; current data is one show)
**Builds on**: Podcasting 2.0 alternate-enclosure observer (see `core/feed_manager.py` and the `episode_alternate_enclosures` table)

## Intended outcome

When an episode has one or more `<podcast:alternateEnclosure>` entries already
captured in the database, the **Episode Detail** page renders an inline video
embed alongside the existing audio player. The audio pipeline is unchanged —
the alt-enclosure surface remains observational from a processing standpoint,
but becomes user-visible when the publisher supplies a video variant.

Today the entire corpus contains exactly **7 such entries**, all from
**The News Agents**, all `mime_type=video/youtube` with `source_uri` of the
form `https://youtu.be/<id>`. The plan is sized to that reality: it renders
YouTube embeds first-class, leaves room for `video/mp4` and HLS variants when
publishers eventually serve them, and explicitly defers hls.js and quality
switching until real data demands them.

### User experience

1. User lands on `/podcasts/the-news-agents/episodes/<slug>`.
2. Above the existing audio artwork + audio scrubber, a 16:9 YouTube embed
   appears.
3. Audio playback continues to work exactly as before — alt-enclosures are
   supplementary, not a replacement.
4. Episodes without alt-enclosures render identically to today (no empty
   container, no layout shift).

## Non-goals

- **hls.js**. The corpus has zero HLS today. Safari's native HLS support is
  enough for the few-row case where it surfaces; revisit only when a
  publisher actually serves `application/x-mpegurl`.
- **Unification with `FloatingPlayer` / `PlayerContext`**. The audio player is
  audio-only and route-persistent. Video stays scoped to the Episode Detail
  page; cross-route persistence for video is a much larger refactor.
- **Quality switching UI**. The publisher exposes multiple `<podcast:source>`
  entries on a single `alternateEnclosure` only rarely; we pick the best
  variant deterministically and ship that.
- **Transcript-sync with the video player**. The existing
  `SegmentedTranscriptViewer` syncs to the audio element; wiring it to video
  would couple to YouTube's iframe API, which is out of scope.
- **Caption / chapter / SponsorBlock UX**. YouTube's built-in controls cover
  the baseline.

## Data shape (already present)

Schema as of #88:

```sql
CREATE TABLE episode_alternate_enclosures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    length INTEGER NULL,
    bitrate REAL NULL,
    height INTEGER NULL,
    title TEXT NULL,
    rel TEXT NULL,
    language TEXT NULL,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (episode_id) REFERENCES episodes(id) ON DELETE CASCADE,
    UNIQUE(episode_id, source_uri)
);
```

Sample row:

```text
episode_id  = a3f4405c-a075-4d59-a698-ac1c3b5f7ab3
source_uri  = https://youtu.be/6wz3LrdMnVo
mime_type   = video/youtube
height      = NULL
is_default  = 0
title       = Inside the Premier League's 'best run' football club | …
```

Repository accessor already exists:
[`SqlitePodcastRepository.get_alternate_enclosures`](../thestill/repositories/sqlite_podcast_repository.py#L3659).

## Backend

### 1. Extend the episode-detail response

[`api_podcasts.py:271-329`](../thestill/web/routes/api_podcasts.py#L271-L329)
(`get_episode_by_slugs`) currently returns a flat episode dict. Add an
`alternate_enclosures` array:

```python
alts = state.repository.get_alternate_enclosures(episode.id)
return api_response({
    "episode": {
        # … existing fields …
        "alternate_enclosures": [
            {
                "source_uri": str(a.source_uri),
                "mime_type": a.mime_type,
                "height": a.height,
                "bitrate": a.bitrate,
                "title": a.title,
                "rel": a.rel,
                "is_default": a.is_default,
            }
            for a in alts
        ],
    }
})
```

Always emit the key (even when empty) so the frontend type stays
`AlternateEnclosure[]` rather than `AlternateEnclosure[] | undefined` —
simpler conditional in the component.

### 2. Test additions

In the existing `test_api_podcasts.py` (or the closest sibling), one test:

- Seed an episode with two alt-enclosure rows via `add_alternate_enclosures`.
- `GET /api/podcasts/<slug>/episodes/<slug>` returns them in the response.
- A second episode with zero rows returns `alternate_enclosures: []`.

No regression test needed for the audio pipeline — the alt-enclosure path is
read-only and does not feed download/transcribe.

## Frontend

### 3. New component: `VideoEnclosurePlayer.tsx`

Location: `thestill/web/frontend/src/components/VideoEnclosurePlayer.tsx`.

Props:

```ts
interface AlternateEnclosure {
  source_uri: string;
  mime_type: string;
  height: number | null;
  bitrate: number | null;
  title: string | null;
  rel: string | null;
  is_default: boolean;
}

interface VideoEnclosurePlayerProps {
  entries: AlternateEnclosure[];
}
```

#### Variant selection

Deterministic, no UI:

1. If any entry has `is_default === true`, pick the first such entry.
2. Otherwise pick the entry with the highest non-null `height`.
3. Otherwise the first entry.

#### Render dispatch on `mime_type`

| MIME | Render |
|---|---|
| `video/youtube` | `<iframe>` to `https://www.youtube.com/embed/<id>?modestbranding=1&rel=0`, wrapped in `aspect-video` |
| `video/mp4`, `video/webm`, `video/ogg`, anything else starting with `video/` | `<video controls playsInline src=…>` |
| `application/x-mpegurl`, `application/vnd.apple.mpegurl` | Native `<video>`; Safari plays it, other browsers will show no playable source — emit a "Open externally" fallback link beside it |
| anything else | "Open in new tab" link only — no embed |

YouTube ID extraction:

- `https://youtu.be/<id>` → `<id>` (everything before `?` or `&`)
- `https://www.youtube.com/watch?v=<id>` → query param `v`
- `https://www.youtube.com/embed/<id>` → last path segment
- Anything else → fall through to "Open externally" link

#### Visual treatment

- 16:9 ratio via Tailwind `aspect-video w-full max-w-3xl mx-auto`.
- Rounded corners (`rounded-lg overflow-hidden`) to match existing artwork.
- Caption row underneath: `{podcast_title} · video via {provider}` where
  provider is `"YouTube"` for `video/youtube`, derived from `mime_type` host
  for HTTP-streamed video, or `"video"` as a fallback.

### 4. Type extension

Wherever the Episode TypeScript type is declared (likely
`thestill/web/frontend/src/types/api.ts` or co-located in `EpisodeDetail.tsx`),
add:

```ts
alternate_enclosures?: AlternateEnclosure[];
```

`?` because older clients seeing pre-spec-39 server builds shouldn't break.

### 5. Wire into `EpisodeDetail.tsx`

[`EpisodeDetail.tsx`](../thestill/web/frontend/src/pages/EpisodeDetail.tsx)
around line 238 ("Audio player area"): render

```tsx
{episode.alternate_enclosures && episode.alternate_enclosures.length > 0 && (
  <div className="mb-4">
    <VideoEnclosurePlayer entries={episode.alternate_enclosures} />
  </div>
)}
```

immediately **above** the existing audio block. Do not replace the audio
block — audio remains the primary surface.

## Cross-cutting

### 6. CSP check

Confirm no Content-Security-Policy header blocks
`frame-src https://www.youtube.com https://www.youtube-nocookie.com`.
Likely paths to check:

- `thestill/web/middleware/` for any `Content-Security-Policy` response header.
- FastAPI app construction in `thestill/web/app.py`.

If a CSP exists and omits `frame-src` (or has `frame-src 'none'`), extend it.
If no CSP is set, no-op — the embed will load.

### 7. Manual QA

For each of the 7 News Agents episodes currently in the DB:

1. Navigate to the episode detail URL.
2. Confirm the YouTube embed renders.
3. Press play in the iframe; confirm playback.
4. Confirm the audio player below still works independently.

Episodes without alt-enclosures (the other 55 podcasts): confirm no layout
shift, no empty container, no console errors.

## Implementation phases

Single PR. The slice is small enough that splitting it adds churn.

### Phase 1 — End-to-end

1. Backend: extend `get_episode_by_slugs` response + one test.
2. Frontend: add `VideoEnclosurePlayer`, Episode type extension, wire into
   `EpisodeDetail`.
3. CSP check; extend if needed.
4. Manual QA on all 7 News Agents episodes + a no-alts episode.
5. Rebuild frontend bundle (`thestill/web/frontend && npm run build`).

Estimated effort: ~2–3 hours.

## Risks

- **CSP silent failure.** If a CSP header forbids `frame-src`, the iframe
  loads as an empty box with a console message. Easy to verify but easy to
  miss. The QA step explicitly checks DevTools.
- **YouTube ID parsing edge cases.** The 7 current entries are all
  `youtu.be/<id>`; the parser handles `watch?v=` and `embed/<id>` as
  defensive coverage, falling through to "open externally" on anything
  unexpected.
- **Mobile autoplay policies.** Not autoplaying — user must press play —
  so iOS Safari and Chrome Mobile policies don't apply.
- **Privacy / cookies.** Default to the standard YouTube embed; if we want
  to harden this later, swap to `youtube-nocookie.com`. Not in scope for v1.

## Open items

- Should we use `youtube-nocookie.com` by default? Marginally better privacy,
  identical functionality. Lean yes if no objection.
- When a future entry has multiple `<podcast:source>` variants of one
  `alternateEnclosure` element (e.g. MP4 + WebM fallback), the deterministic
  picker handles it via the height-then-default heuristic; revisit if we
  ever see this in real data.
- HLS (hls.js): re-open this spec if a publisher ships
  `application/x-mpegurl` entries.

## Cross-references

- [eda2e27](https://github.com/ssarunic/thestill/commit/eda2e27): backfill
  command + observer instrumentation that produced the data this spec consumes.
- [576c9df](https://github.com/ssarunic/thestill/commit/576c9df):
  `scripts/survey_alternate_enclosures.py`, used to track the
  1.4 %-and-rising adoption trajectory.
- Spec #22 (floating-media-player): explicitly **not** extended here — see
  Non-goals.
- Spec #23 (transcript-playback-sync): explicitly **not** extended — see
  Non-goals.
