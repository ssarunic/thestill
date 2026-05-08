# Importing Episodes

> Status: shipped (spec [#31](../specs/31-import-arbitrary-episodes.md), phases 1–4)

Thestill lets you paste a URL and have the resulting episode land in your
inbox immediately. The transcription, cleaning, and summarisation pipeline
runs in the background; the inbox row updates in place as each stage
completes.

You do **not** need to follow the source podcast / channel to import a
single episode. Importing is decoupled from following — that's the whole
point of the feature.

## Supported URL kinds

| Kind | Example URL | Notes |
|---|---|---|
| YouTube video | `https://www.youtube.com/watch?v=dQw4w9WgXcQ` | Also `youtu.be/...`, `/shorts/...`, `/playlist?list=...`. The episode's parent channel is upserted into the system as an `auto_added` podcast row, hidden from refresh until you follow it. |
| Apple Podcasts share link | `https://podcasts.apple.com/us/podcast/the-daily/id1200361736?i=1000620312000` | Resolved via the iTunes Search API to the show's RSS feed and the episode's audio URL. Show-only links (no `?i=...`) are rejected — paste an episode link from the Share menu. |
| Direct audio file | `https://cdn.example.com/episode.mp3` | Any URL ending in `.mp3`, `.m4a`, `.opus`, `.ogg`, or `.wav`. Falls back to a synthetic `audio-imports` parent. |

### Not supported

- **Spotify share links.** Spotify exclusives have no enclosure URL we can
  fetch; non-exclusives need API auth that isn't worth the operational
  cost. The modal catches Spotify URLs client-side and shows a clear
  message — try the YouTube or RSS link for the same episode instead.
- **Pocket Casts share links.** Defer until there's user demand.
- **Shortened links** (e.g. `apple.co/...`, `youtu.be` redirects). Most
  short links work because they redirect to a supported host before
  reaching us; if a short link fails, expand it manually first.

## Using the import flow

### Web UI

1. Open the **Inbox** page.
2. Click **Import** in the header (or the empty-state CTA on a fresh
   inbox).
3. Paste the URL. The submit button enables once the field has any text.
4. Submit. The inbox refreshes, and the new row shows a `Downloading…` →
   `Transcribing…` → `Cleaning…` → `Summarising…` pill until the pipeline
   finishes. The page polls every 5 seconds while anything is in flight.

### HTTP API

```http
POST /api/imports
Content-Type: application/json

{ "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ" }
```

```json
HTTP/1.1 200 OK
{
  "status": "ok",
  "import": {
    "episode_id": "abc-...",
    "canonical_id": "youtube:dQw4w9WgXcQ",
    "title": "Some Talk",
    "kind": "youtube",
    "source_handle": "Lex Fridman",
    "deduplicated": false,
    "inbox_created": true,
    "inbox_entry": { "...": "..." },
    "parent": {
      "id": "...",
      "title": "Lex Fridman",
      "slug": "lex-fridman"
    }
  }
}
```

`parent` is `null` when the import falls back to the synthetic
`audio-imports` row (i.e. bare `.mp3` URLs with no deducible parent).

The endpoint returns `400 Bad Request` for unsupported URLs (Spotify,
Vimeo, anything no resolver matches).

## Idempotency

- Pasting the **same URL twice** by the same user returns the existing
  episode and inbox row (`deduplicated: true`, `inbox_created: false`).
- Pasting the **same URL by a second user** shares the episode (one
  Whisper run for the system) but creates a new inbox row for the second
  user.
- URL normalisation strips tracking parameters (`utm_*`, `fbclid`, etc.)
  and lowercases the host before computing the canonical id, so
  share-link variants of the same episode collapse to the same row.

The canonical id is derived per resolver:

| Kind | Canonical id |
|---|---|
| YouTube | `youtube:<video_id>` |
| Apple | `apple:<itunes_track_id>` |
| Bare audio | `audio:<sha256_of_normalised_url>` |

Cross-source dedup (same episode pasted as YouTube link vs. Apple link)
is **not** currently supported — those collapse to different canonical
ids. Most URL variants of the same source dedup correctly.

## What happens to the parent podcast

When the resolver can deduce a parent (YouTube channel, Apple show), the
podcast is upserted into `podcasts` with `auto_added=1`. These rows:

- **Are hidden** from `Browse podcasts` until at least one user follows
  them.
- **Are not refreshed** by the periodic feed-poll loop until at least one
  user follows them.
- **Are never followed** as a side-effect of the import — the user must
  click "Follow this channel" explicitly.

If the channel is already a podcast you follow, the import attaches the
new episode to your existing subscription without duplicating the row or
clearing the `auto_added` flag.

## Running the pipeline

Imports run through the same pipeline as RSS-discovered episodes:

```
download → downsample → transcribe → clean → summarize → entity branch
```

There is no special-case path. Failures surface in the same way (failed
state, `Failed Tasks` page, retry from the dead-letter queue).

## Quotas

Self-hosted single-user mode has **no enforced quota** — paste as many
URLs as you like. The service does emit an `imports_in_24h` field on
every successful import's structured log so future multi-user
deployments can wire up enforcement without back-filling history.

If/when a quota is enforced, the response will return `429 Too Many
Requests` with a clear message; the API surface won't change otherwise.

## Troubleshooting

- **"No resolver matched URL"** — the URL kind isn't supported. See the
  table above.
- **"yt-dlp returned no metadata"** — the video is private, age-gated,
  geo-restricted, or deleted. Check the URL in a browser.
- **"iTunes lookup found no episode"** — the Apple share link's `?i=`
  track id no longer exists (the show was unpublished or the episode
  was withdrawn).
- **Inbox row stuck on `Downloading…`** — check the `Failed Tasks`
  page or the `download` stage worker logs. Common causes: blocked
  audio CDN, expired CDN URL (some publishers rotate), or yt-dlp /
  ffmpeg unavailable in the runtime.
