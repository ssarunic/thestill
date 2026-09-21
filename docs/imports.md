# Importing Episodes

> Status: shipped (spec [#31](../specs/31-import-arbitrary-episodes.md), phases 1–4; Spotify links per spec [#79](../specs/79-spotify-link-import.md))

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
| YouTube video | `https://www.youtube.com/watch?v=dQw4w9WgXcQ` | Also `youtu.be/...` and `/shorts/...`. A watch URL that carries a `list=` param imports just that video — the playlist context is ignored. Pasting a bare `youtube.com/playlist?list=...` index page is not supported (importing a whole playlist/channel is not a goal of this feature; use "follow" instead). The episode's parent channel is upserted into the system as an `auto_added` podcast row, hidden from refresh until you follow it. |
| Apple Podcasts share link | `https://podcasts.apple.com/us/podcast/the-daily/id1200361736?i=1000620312000` | Resolved via the iTunes Search API to the show's RSS feed and the episode's audio URL. Show-only links (no `?i=...`) are rejected — paste an episode link from the Share menu. |
| Spotify episode link | `https://open.spotify.com/episode/7kQ2xN9pZ1aB3cD4eF5gH6` | Also `spotify:episode:<id>` URIs, locale paths (`/intl-de/episode/...`) and `spotify.link/...` short links. Spotify IDs are opaque, so the episode is resolved by **metadata matching**: Spotify's own title / show / release date / duration → the show in the Apple Podcasts directory (which carries the RSS feed URL) → the episode in that feed, scored on title, date (±36 h) and duration. Spotify exclusives and video-only shows have no public feed and are rejected with a clear message. Show links (`/show/...`) belong on the Podcasts page (see below). See [Spotify resolution](#spotify-resolution). |
| Direct audio file | `https://cdn.example.com/episode.mp3` | Any URL ending in `.mp3`, `.m4a`, `.opus`, `.ogg`, or `.wav`. Falls back to a synthetic `audio-imports` parent. |

### Not supported

- **Spotify exclusives.** A show that exists only on Spotify has no public
  RSS feed, so there is nothing to import; the error names the show and
  suggests the Apple / RSS link when one exists.
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

The endpoint returns `400 Bad Request` for unsupported URLs (Vimeo,
anything no resolver matches) and for Spotify links that cannot be matched
to a public feed.

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
| Spotify | `spotify:<spotify_episode_id>` |
| Bare audio | `audio:<sha256_of_normalised_url>` |

Cross-source dedup (same episode pasted as YouTube link vs. Apple link)
is **not** currently supported — those collapse to different canonical
ids. Most URL variants of the same source dedup correctly.

## Spotify resolution

There is no deterministic mapping from a Spotify episode to an RSS item,
so `SpotifyResolver` (`thestill/core/spotify_resolver.py`) runs a
three-stage matching pipeline. Every stage is logged (`spotify_*` events)
with its scores so a wrong or missing match can be diagnosed.

1. **Spotify metadata.** The public `open.spotify.com/episode/...` page is
   fetched (SSRF-guarded, no auth) and its Open Graph tags read: `og:title`
   (episode), the show name from `<title>` / `og:description`,
   `music:release_date`, `music:duration`, `og:image`. With
   `SPOTIFY_CLIENT_ID` + `SPOTIFY_CLIENT_SECRET` set the Web API
   (`/v1/episodes/{id}`, client-credentials) is used instead; it also
   returns the publisher and does not depend on Spotify's page layout. The
   page scrape remains the fallback if the API call fails.
2. **Show → feed.** `itunes.apple.com/search?term=<show>&entity=podcast`
   results are fuzzy-scored on `collectionName` (and `artistName` against
   the publisher when known). The best result above the threshold supplies
   `collectionId` and `feedUrl`. A subtitle-stripped retry handles names like
   "The Rest Is Politics: US". No match ⇒ "Could not find … in the Apple
   Podcasts directory", which is the normal outcome for Spotify exclusives.
3. **Episode.** Candidates come from, in order: the show's iTunes
   200-episode window (`lookup?id=<collectionId>&entity=podcastEpisode`),
   an iTunes episode-title search filtered to the show, and finally the RSS
   feed itself (full history). Each candidate scores
   `0.6 × title + 0.25 × date + 0.15 × duration`, where the title score is a
   token-set similarity over a normalised title (lower-case, accents /
   punctuation / emoji stripped, `Ep. 123 –` prefixes dropped), the date
   score is 1.0 within ±36 h decaying to 0 at a week, and duration is 1.0
   within 2 min / 0.6 within 10 min (dynamic ad insertion) / 0.2 beyond.
   Accepted at ≥ 0.72 with a title score ≥ 0.5, or an exact title with a
   compatible date or duration. Two accepted candidates within 0.01 of each
   other ("Part 1" / "Part 2" on the same day) are reported as ambiguous
   rather than guessed.

The winning candidate's enclosure URL becomes the episode's `audio_url`, so
the parent-feed ingest that follows binds the import to the feed's own
episode row (see below) exactly as it does for Apple links. The canonical
id stays `spotify:<id>` so re-pasting the same link dedups.

**Spotify show links on the Podcasts page.** `Add podcast` accepts
`open.spotify.com/show/...` (and episode links) too: stages 1–2 run and the
resolved RSS feed is what gets followed — the Spotify URL itself is never
stored as a feed.

## What happens to the parent podcast

When the resolver can deduce a parent (YouTube channel, Apple or Spotify
show), the
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

Imports skip the local `download` and `downsample` stages and are enqueued
directly at `transcribe`, which fetches the audio from the resolved
`audio_url` itself. From there the chain continues like RSS-discovered
episodes:

```
transcribe → clean → summarize → entity branch
```

This shortcut currently requires the Dalston transcription provider (set
via `TRANSCRIPTION_PROVIDER`, default `whisper`); on a non-Dalston setup an
import's `transcribe` task has no local audio file to work from and will
fail. Failures surface the same way as RSS-discovered episodes (failed
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
- **"YouTube is blocking automated access from this server"** — YouTube's
  bot check ("Sign in to confirm you're not a bot") has flagged the
  server's IP address; datacenter ranges such as AWS are hit routinely.
  The video itself is fine. Retry later, or paste a direct audio link.
  Well-known yt-dlp failures (private, unavailable, age-restricted,
  region-locked, members-only, unfinished live stream) are rewritten
  into plain-language messages by `thestill/utils/youtube_errors.py`;
  anything unrecognised falls through with yt-dlp's own text.
- **"yt-dlp returned no metadata"** — yt-dlp succeeded but produced no
  usable payload. Check the URL in a browser.
- **"iTunes lookup found no episode"** — the Apple share link's `?i=`
  track id no longer exists (the show was unpublished or the episode
  was withdrawn).
- **"Could not find “<show>” in the Apple Podcasts directory"** — the
  Spotify show has no entry on Apple, i.e. it is (almost always) a Spotify
  exclusive with no public feed. If the show *is* on Apple under a
  different name, paste its Apple or RSS link.
- **"Found “<show>” … but could not confidently match the episode"** —
  the show resolved but no feed item cleared the score threshold: a
  Spotify-only bonus episode, a heavily retitled episode, or a trailer.
  Look for `spotify_episode_unmatched` / `spotify_episode_ambiguous` in the
  logs for the tiers tried and the best scores; paste the Apple episode link
  or RSS feed to import it directly.
- **"Spotify's episode page did not expose the episode title and show
  name"** — Spotify changed its page markup. Set `SPOTIFY_CLIENT_ID` /
  `SPOTIFY_CLIENT_SECRET` to switch to the Web API, or paste an Apple /
  RSS link meanwhile.
- **Inbox row stuck on `Downloading…`** — check the `Failed Tasks`
  page or the `download` stage worker logs. Common causes: blocked
  audio CDN, expired CDN URL (some publishers rotate), or yt-dlp /
  ffmpeg unavailable in the runtime.
