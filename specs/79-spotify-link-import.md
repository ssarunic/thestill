# Spotify Link Import Specification

> **Status:** ✅ Implemented (2026-09-21)
> **Created:** 2026-09-21
> **Updated:** 2026-09-21
> **Author:** Product & Engineering
> **Related:** [#31 import-arbitrary-episodes](31-import-arbitrary-episodes.md), [#65 apple-deep-history-import](65-apple-deep-history-import.md), [#27 add-podcast-search-discoverability](27-add-podcast-search-discoverability.md)

---

## Executive Summary

Users share podcast episodes as `open.spotify.com/episode/...` links at
least as often as Apple links, and until now the import modal rejected
them client-side ("Spotify links are not supported"). The blocker was never
the audio — most shows on Spotify are ordinary RSS podcasts — but the ID:
Spotify episode and show IDs are opaque and share nothing with Apple's IDs
or the RSS GUID, so there is no deterministic mapping.

This spec adds a **metadata-matching resolver** that turns a Spotify link
into the show's public RSS feed and the matching feed item:

1. read Spotify's own metadata for the link (page Open Graph tags; Web API
   when credentials are configured);
2. find the show in the Apple Podcasts directory with the iTunes Search
   API, which carries the RSS `feedUrl`;
3. find the episode in that show — iTunes 200-window, iTunes episode
   search, then the RSS feed itself — scored on title, release date and
   duration.

The inbox import (`POST /api/imports`) accepts episode links; **Add
podcast** accepts show (and episode) links and follows the resolved feed.
Spotify exclusives, which have no public feed, are a clean "not found"
rather than a low-confidence match.

**Key principle:** no schema change and no API change. `SpotifyResolver`
plugs into the existing `Resolver` protocol and emits a `CanonicalSource`
whose `audio_url` is the feed's enclosure, so the parent-feed ingest,
dedup, follow CTA and pipeline hand-off all work unchanged.

---

## Problem

- `ImportEpisodeModal` blocked `spotify.com` URLs before calling the API,
  and `docs/imports.md` listed Spotify under "Not supported".
- `RSSMediaSource.extract_metadata` resolved Apple URLs to RSS but treated
  a Spotify show URL as if it were a feed, so `thestill add` and the Add
  podcast modal failed on it with an unhelpful parse error.
- There is no ID-level bridge between Spotify and RSS/Apple. Odesli-style
  link resolvers have patchy podcast coverage.

## Goals

1. Pasting a Spotify episode link into the inbox import lands the episode
   exactly as an Apple link would (same parent bootstrap, follow CTA,
   pipeline).
2. Pasting a Spotify show link into Add podcast follows the show's real
   RSS feed.
3. Wrong matches are rarer than misses: below-threshold and ambiguous
   results are rejected with a message that names the show/episode and the
   escape hatch (Apple / RSS link).
4. Works with no credentials; optionally uses the Spotify Web API when
   `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` are set.
5. Fully unit-testable offline; every network collaborator injectable.

## Non-goals

- Importing Spotify exclusives / video-only shows (no public enclosure).
- Cross-source dedup (`spotify:<id>` vs `apple:<track>` of the same
  episode) beyond what the existing parent-feed ingest already achieves by
  binding both to the feed's row via `audio_url`.
- Podcast Index API as a second directory. Left as a documented extension
  point (`find_apple_show` is the only directory call).

---

## Design

### Module layout

| Piece | Where | Role |
|---|---|---|
| URL shapes | `utils/url_patterns.py` | `SPOTIFY_HOST_RE`, `SPOTIFY_ENTITY_RE` (locale prefix, 22-char base62 id), `SPOTIFY_URI_RE`, `SPOTIFY_SHORT_LINK_RE`; `is_spotify_url`, `extract_spotify_entity`. Registered in `ALL_PATTERNS` for the ReDoS suite. |
| Pipeline | `core/spotify_resolver.py` | `SpotifyLinkResolver.resolve_episode / resolve_show`, page parsers, `SpotifyWebApi`, `find_apple_show`, `pick_episode` + scoring, candidate adapters (iTunes, RSS). |
| Import | `services/import_service.py` | `SpotifyResolver` (protocol adapter) in the default lineup; `CanonicalSource.kind` gains `spotify_episode`. |
| Add podcast | `core/media_source.py` | `RSSMediaSource._extract_rss_from_spotify_url`, tried after the Apple resolver in `extract_metadata`; `is_valid_url` accepts Spotify URLs. |
| Provenance | `utils/episode_origin.py` | `spotify:` prefix → `import_kind = spotify_episode`. |
| Config | `utils/config.py`, `.env.example`, `docs/configuration.md` | Optional `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET`. |
| Frontend | `ImportEpisodeModal.tsx`, `AddPodcastModal.tsx`, `episodeInformation.tsx`, `api/types.ts` | Client-side block removed; copy updated; `ImportKind` + "Imported (Spotify)" label. |

Layering: the pipeline lives in `core` because `media_source` (core) needs
it; `services` wraps it. HTTP goes through `utils.url_guard.guarded_session`
(SSRF guard, redirect re-validation) with a browser User-Agent — iTunes
returns 403 to non-browser agents.

### Stage 1 — Spotify metadata

`default_episode_metadata(episode_id, page_url)`:

- **Web API** (when both env vars are set): client-credentials token from
  `accounts.spotify.com/api/token`, then `GET /v1/episodes/{id}?market=US`
  (retry without `market` on failure). Yields name, description,
  `release_date` (padded for month/year precision), `duration_ms`, images,
  `show.name`, `show.publisher`. Any failure logs
  `spotify_api_episode_failed` and falls back to the page.
- **Page scrape** (default): `open.spotify.com/episode/<id>` (canonical URL,
  tracking params dropped). `parse_meta_tags` reads the first 512 KB with
  bounded regexes. Fields: `og:title` → episode title; show name from
  `<title>` ("`<episode> - <show> | Podcast on Spotify`"), else
  `og:description` ("`<show> · Episode`" or the legacy "Listen to this
  episode from `<show>` on Spotify."); `music:release_date`;
  `music:duration`; `og:image`; a best-effort `"publisher":"…"` from
  embedded JSON. Title + show name are required; the rest only weakens the
  score.

`spotify.link/<token>` short links are expanded through the guarded session
first; `spotify:episode:<id>` URIs and `/intl-xx/` locale paths are parsed
directly.

### Stage 2 — Show → Apple directory → feed

`find_apple_show(show_name, publisher)`:
`itunes.apple.com/search?term=<show>&media=podcast&entity=podcast&limit=25`,
score each result with a `feedUrl`:

```
name    = title_similarity(show_name, collectionName)
score   = name                                   # publisher unknown
        = 0.75·name + 0.25·max(pub, 0.5·name)    # publisher known
```

`title_similarity` = max(token-set ratio, 0.6·token-set + 0.4·sequence
ratio) on normalised text (stdlib `difflib`; `rapidfuzz` is only an
optional extra). A second search with the subtitle stripped
(`"X: Y"`, `"X - Y"`, `"X | Y"` → `"X"`) runs unless the first already scored
≥ 0.9. Accept at ≥ 0.6; otherwise `SpotifyResolutionError` "Could not find
“<show>” in the Apple Podcasts directory … Spotify exclusive …".

### Stage 3 — Episode

Candidates, in tiers (each only when the previous produced no accepted
match):

1. `lookup?id=<collectionId>&entity=podcastEpisode&limit=200` — the same
   hard window spec #65 documented;
2. `search?term=<episode title>&media=podcast&entity=podcastEpisode&limit=50`
   filtered to `collectionId`;
3. the RSS feed at `feedUrl` (full history; enclosure, `itunes:duration`,
   `pubDate`, guid).

Per-candidate score:

```
title    = title_similarity(spotify.title, candidate.title)
date     = 1.0 within ±36 h, linear to 0.3 at 7 d, 0 beyond; 0.5 if unknown
duration = 1.0 within 2 min, 0.6 within 10 min, 0.2 beyond; 0.5 if unknown
combined = 0.6·title + 0.25·date + 0.15·duration
accepted = (combined ≥ 0.72 and title ≥ 0.5)
        or (title ≥ 0.95 and (date ≥ 0.3 or duration ≥ 0.6))
```

`pick_episode` takes the best accepted candidate; if the runner-up is also
accepted, within 0.01, and has a different normalised title, the result is
ambiguous (`spotify_episode_ambiguous`) → miss. Duration is deliberately a
tiebreaker: dynamic ad insertion makes Spotify and Apple durations differ
by minutes.

### Output

`ResolvedSpotifyEpisode` → `CanonicalSource(kind="spotify_episode",
canonical_id="spotify:<id>", audio_url=<feed enclosure>,
parent=CanonicalParent(rss_url=feedUrl, title=collectionName, …))`.
Feed-side title/description/duration/date/artwork win over Spotify's; the
Spotify values fill gaps. Because `audio_url` is the feed enclosure,
`ImportService._ingest_parent_feed` binds the import to the feed's own
episode row and stamps `spotify:<id>` on it — identical to the Apple path.

`resolve_show(url)` runs stages 1–2 for `/show/` links (show page or Web
API `/v1/shows/{id}`) and for episode links (show name from the episode
metadata) and returns the `AppleShowMatch`; `RSSMediaSource` then fetches
that feed as usual, so the stored `rss_url` is the real feed, never the
Spotify URL.

### Errors (user-facing, verbatim through `400`)

| Situation | Message (abridged) |
|---|---|
| Not an episode/show link (playlist, artist, malformed) | "Spotify link does not point to an episode or show…" |
| Show link pasted into the import modal | "This is a Spotify show link, not an episode… add it from the Podcasts page." |
| Page markup changed | "Spotify's episode page did not expose the episode title and show name…" |
| Show not on Apple (exclusive) | "Could not find “<show>” in the Apple Podcasts directory… Spotify exclusive…" |
| Show found, episode not matched / ambiguous | "Found “<show>” … but could not confidently match the episode “<title>”…" |

### Logging

`spotify_metadata_fetched` (source page/api, which signals are present),
`spotify_show_matched` / `spotify_show_unmatched` (best score + candidate),
`spotify_episode_matched` (tier, per-signal scores),
`spotify_episode_unmatched` (tiers tried), `spotify_episode_ambiguous`,
`spotify_itunes_*_failed`, `spotify_feed_fetch_failed`,
`spotify_show_feed_resolved` / `_unresolved` (add-podcast path).

---

## Testing

All offline; network collaborators injected.

- `tests/unit/security/test_url_patterns.py` — Spotify shapes, locale
  prefixes, URIs, id length, negatives; new patterns join the ReDoS sweep.
- `tests/unit/core/test_spotify_resolver.py` — page parsing from
  `tests/fixtures/spotify/*.html` (built from the Open Graph tags Spotify
  serves; `open.spotify.com` was unreachable from the build sandbox, so a
  live capture should replace them when available), Web API mapping,
  normalisation, token-set ratio, show scoring / retry / exclusive miss,
  episode scoring (ad drift, timezone lag, far date + exact title,
  neutral unknowns, ambiguity, duration tiebreak), iTunes/RSS adapters, the
  three tiers end-to-end, short-link expansion, show resolution.
- `tests/unit/services/test_import_service_spotify.py` — `CanonicalSource`
  shape, auto-added parent, dedup across URL forms and users, feed-row
  binding with a feed manager, error propagation, default lineup.
- `tests/unit/core/test_media_source_spotify.py` — `is_valid_url`,
  `_extract_rss_from_spotify_url`, `extract_metadata` fetching the resolved
  feed.
- Frontend: `ImportEpisodeModal.test.tsx` (Spotify link goes to the API;
  backend message surfaces), `episodeInformation.test.tsx` label.

## Verification checklist (live, post-merge)

- [ ] Paste an `open.spotify.com/episode/…` link for an RSS-backed show:
      inbox row appears, parent podcast auto-added with the real feed,
      `spotify_episode_matched` logged with `matched_via=itunes`.
- [ ] Paste an episode older than the show's newest 200:
      `matched_via=rss`.
- [ ] Paste a Spotify exclusive: 400 naming the show, no rows created.
- [ ] Add podcast with an `open.spotify.com/show/…` link: podcast row's
      `rss_url` is the feed, episodes refresh normally.
- [ ] With `SPOTIFY_CLIENT_ID/SECRET` set: `spotify_metadata_fetched
      source=api`.

## Open questions / follow-ups

- **Podcast Index** as a second directory when iTunes search misses
  (`find_apple_show` is the single seam).
- **Live page fixture.** Replace the synthetic HTML fixtures with a trimmed
  capture of a real episode/show page once one can be fetched from CI.
- **Cross-source canonical id.** Both `apple:` and `spotify:` imports of
  the same episode already converge on the feed's row when the parent feed
  ingests; a GUID-based canonical key would make that explicit.
