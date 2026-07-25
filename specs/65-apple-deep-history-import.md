# Apple Deep-History Import Specification

> **Status:** 🚧 Implemented on `feat/65-apple-deep-history-import` (2026-07-25); pending merge
> **Created:** 2026-07-25
> **Updated:** 2026-07-25
> **Author:** Product & Engineering
> **Related:** [#31 import-arbitrary-episodes](31-import-arbitrary-episodes.md), [#42 robustness-and-failure-mode-hardening](42-robustness-and-failure-mode-hardening.md)

---

## Executive Summary

Importing an Apple Podcasts episode link fails when the episode is older
than the show's latest ~200 episodes. The iTunes Search API — the only
data source the `ApplePodcastsResolver` consults today — cannot reach
deeper: its per-episode index is incomplete and its show-level lookup is
hard-capped at `limit=200` with `offset` silently ignored (verified live,
see [Verified Findings](#verified-findings)).

Fix: add a **third fallback tier** to the Apple episode lookup. When both
iTunes lookups miss, fetch the pasted `podcasts.apple.com` episode page
itself, parse the `serialized-server-data` JSON blob Apple embeds in it
(title, release date, duration, direct enclosure `streamUrl`), and
best-effort cross-match the episode against the show's RSS feed — which
carries the full history — to confirm the canonical enclosure URL.

**Key principle:** no new resolver, no schema change, no API change. The
new tier is an internal fallback inside the existing
`_default_apple_episode_lookup` chain and returns the same
iTunes-lookup-shaped dict, so `ApplePodcastsResolver.resolve()` is
untouched.

---

## Motivation

Real failing import (2026-07-25):

```
https://podcasts.apple.com/gb/podcast/20vc-revolut-founder-nik-storonsky-on-when-and-where/id958230465?i=1000678898255
```

Error: `iTunes lookup found no episode for trackId 1000678898255
(searched 201 entries under collectionId 958230465)`.

The episode is from 2024-12-04. 20VC publishes ~3 episodes/week, so it
fell out of Apple's latest-200 window months ago. Any show with a deep
archive (interview shows, daily news) hits this for most of its history —
the exact catalogue-mining use case #31's import feature exists for.

## Verified Findings

All verified live on 2026-07-25 against the failing URL:

| # | Finding | Evidence |
|---|---|---|
| V1 | Direct per-episode lookup misses older episodes | `lookup?id=1000678898255&entity=podcastEpisode` → `resultCount: 0` |
| V2 | Show-level lookup is capped at 200 episodes | `limit=200` returns 201 results (show record + 200 newest episodes) |
| V3 | The cap cannot be paginated | `&offset=200` returns the **identical** 201 results — `offset` is silently ignored |
| V4 | The episode page embeds full metadata | `<script id="serialized-server-data">` JSON contains `title`, `releaseDate: "2024-12-04T17:30:00Z"`, `duration: 2753`, and `mediaEnclosures[0].streamUrl` = the real libsyn MP3 enclosure |
| V5 | The show RSS feed has the full history | `feedUrl` from the show lookup → 1,487 `<item>`s including the target episode |

V3 rules out "fetch more episodes" as a fix: there is no paginated or
historical access through the iTunes Search API. The page scrape (V4)
plus RSS confirmation (V5) is the only automatic path to deep history.

---

## Product Requirements

### User Stories

| As a... | I want to... | So that... |
|---------|--------------|------------|
| User | Paste an Apple episode link of any age | The episode imports even if it's years deep in the archive |
| User | Get a clear, actionable error if Apple's page can't be parsed | I know to paste the show's RSS feed instead of retrying blindly |
| Operator | See which lookup tier resolved each import | A silent shift to the fragile scrape tier (or its breakage) is visible in logs |

### Core Behaviors

1. **Tiered lookup.** `_default_apple_episode_lookup` becomes a
   three-tier chain, each tier tried only when the previous one misses:
   - **Tier 1** — direct `trackId` lookup (existing fast path).
   - **Tier 2** — show-level `collectionId` lookup, `limit=200` (existing).
   - **Tier 3 (new)** — episode-page scrape + RSS cross-match.
2. **Page scrape.** Tier 3 fetches the pasted episode URL, extracts the
   `serialized-server-data` JSON, and locates the episode node whose
   context URL carries `i=<track_id>`. Required fields: `title` and
   `mediaEnclosures[0].streamUrl`; missing either is a hard
   `ResolverError`. `releaseDate` and `duration` are optional extras.
3. **RSS cross-match (best-effort).** Using the `feedUrl` already
   obtained from the Tier-2 show record, fetch the show's RSS and find
   the matching item — by enclosure URL first, then exact title, then
   pub-date + normalised title. On a match, prefer the feed's enclosure
   URL over the scraped `streamUrl`. On any failure (feed unreachable,
   no match), log a warning and proceed with the scraped values — the
   cross-match improves canonicality, it must never block an import that
   Tier 3 already resolved.
4. **iTunes-shaped adapter.** Tier 3 returns a dict using the same keys
   `resolve()` already reads (`trackName`, `episodeUrl`, `feedUrl`,
   `collectionName`, `trackTimeMillis`, `releaseDate`, `description`,
   `artworkUrl600`), so the resolver body and everything downstream
   (parent auto-add, inbox row, download stage) is unchanged.
5. **Loud failure.** When Tier 3 also fails, the `ResolverError` names
   the reason and the escape hatch: *"…Apple's page could not be parsed.
   Paste the show's RSS feed and import the episode from there."*
6. **Tier observability.** Every successful lookup logs
   `apple_lookup_tier` (1/2/3) with `track_id` / `collection_id` via
   structlog, and Tier-3 entry logs why Tiers 1–2 missed. A drift in
   Apple's page internals must show up as a spike of loud Tier-3
   failures, never as silently degraded imports (#42 FM-4, FM-6).

### Non-Goals

- **Apple AMP API** (`amp-api.podcasts.apple.com`). Requires a scraped
  bearer token and is far more likely to be rate-limited or
  fingerprinted. The public page + public RSS are enough.
- **Show-only links.** `/idNNN` without `?i=` remains unsupported
  (unchanged from #31) — that's a follow/add flow, not an episode import.
- **Paywalled / subscriber-only Apple episodes.** No `streamUrl` in the
  page → hard error, same as today's no-audio path.
- **Generalising the scrape to other resolvers.** YouTube and Substack
  have their own working metadata paths.

---

## Design

### URL surface (verified shape, 2026-07-25)

```
Pasted:  https://podcasts.apple.com/gb/podcast/<slug>/id958230465?i=1000678898255
Page:    <script id="serialized-server-data">[{...}]</script>
           └─ episode node: {"title": "20VC: Revolut Founder Nik Storonsky…",
                             "releaseDate": "2024-12-04T17:30:00Z",
                             "mediaEnclosures": [{"streamUrl":
                               "https://traffic.libsyn.com/secure/thetwentyminutevc/New_Ads_Nik.mp3?dest-id=240976",
                               "duration": 2753, ...}],
                             "contextAction": {... "?i=1000678898255" ...}}
RSS:     https://rss.libsyn.com/shows/61840/destinations/240976.xml
           └─ 1,487 items; target episode present with the same enclosure URL
```

### Lookup chain

New helpers in
[import_service.py](../thestill/services/import_service.py), slotted into
`_default_apple_episode_lookup` after the existing Tier-2 miss (replacing
today's terminal `ResolverError` at the end of the show-window scan):

```python
def _apple_page_episode_lookup(
    episode_url: str,
    track_id: str,
    *,
    show_record: Optional[dict],
) -> dict:
    """Tier 3: scrape the episode page; return an iTunes-shaped dict.

    Apple's page internals are unversioned — every extraction is
    defensive and a missing required field raises ResolverError with
    the paste-the-RSS-feed escape hatch (never a KeyError).
    """
```

- **Fetch** via the existing `guarded_session` (SSRF guard, retries,
  10 s timeout) with a browser User-Agent — Apple serves the full
  server-rendered payload to browser UAs; the API-style UA used for
  iTunes lookups is not guaranteed to get it.
- **Parse**: `json.loads` the `serialized-server-data` script body, then
  walk the tree for dicts that look like episode nodes (have `title` +
  `mediaEnclosures`) and match `i=<track_id>` in an associated URL
  field. Tree-walk, not fixed paths — Apple reshuffles nesting more
  often than it renames leaf keys.
- **Threading the URL**: the resolver currently calls
  `self._lookup(episode_id, collection_id)`. The `AppleEpisodeLookup`
  callable signature gains the pasted URL (needed for the page fetch):
  `Callable[[str, Optional[str], str], dict]` — an internal seam with a
  single injection point in tests, safe to change.
- **Show record reuse**: Tier 2's show-level response already contains
  the show record (`feedUrl`, `collectionName`, `artworkUrl600`) even
  when the episode itself is outside the window. Tier 3 receives it and
  merges those show-level fields into its result so the parent-podcast
  bootstrap works exactly as before.

```python
def _apple_rss_cross_match(
    feed_url: str, scraped: dict, track_id: str
) -> Optional[dict]:
    """Best-effort: find the scraped episode in the show's RSS feed.

    Match order: enclosure URL (path component, query ignored) →
    exact title → pub-date (±24 h) + casefolded title. Returns the
    matched item's enclosure URL + guid, or None. Never raises.
    """
```

- Fetched via `guarded_session`, parsed with `feedparser` (already a
  dependency, already used by `FeedManager` — the parse here is
  read-only matching, not feed ingestion, so no reuse of the refresh
  path is warranted).
- On match, the feed's enclosure URL replaces the scraped `streamUrl`
  in the returned dict (`episodeUrl` key). The scraped and feed URLs
  are expected to be identical in practice (V4/V5); the substitution is
  a canonicality guarantee, not a correctness requirement.
- On no-match or fetch failure: `logger.warning("apple_rss_cross_match_miss", …)`
  and Tier 3 proceeds with scraped values (#42 FM-1 — degrade the
  enrichment, not the import).

### Failure modes (#42 checklist)

| FM | Application here |
|---|---|
| FM-1 errors-as-empty-results | Tier misses are explicit exceptions/None, never empty dicts; RSS cross-match is the only swallow-and-continue point and it logs |
| FM-4 silent degradation | `apple_lookup_tier` on every success; Tier-3 failure spike = Apple changed page internals, visible in logs immediately |
| FM-6 parallel-path drift | Tier 3 feeds the same dict shape into the same `resolve()` — no second materialisation path to drift |
| FM-7 unsanitized external output | Page JSON is untrusted: `streamUrl` must be http(s) (the download stage's URL guard applies regardless), `duration`/`releaseDate` type-checked before use |

---

## Edge Cases

| Case | Behavior |
|---|---|
| Episode page 404 / region-blocked | `ResolverError` with the paste-the-RSS-feed message. |
| Page fetch OK but no `serialized-server-data` script (consent wall, layout change) | Same hard error; logged as `apple_page_parse_failed` with a reason tag so drift is diagnosable. |
| Episode node found but no `streamUrl` (subscriber-only audio) | Hard error naming the paywall as the likely cause. |
| `?i=` track id appears in several nodes (up-next rails) | Prefer the node that also carries `mediaEnclosures`; if several qualify, take the one whose canonical URL matches the pasted episode slug. |
| Show record absent (Tier 2 itself errored, e.g. iTunes 5xx) | Tier 3 still runs from the pasted URL alone; parent bootstrap degrades to the feed URL discovered via RSS cross-match, or the synthetic parent path from #31 if that also fails. |
| RSS feed truncated (some hosts serve last-N only) | Cross-match misses → warning + scraped values. Import still succeeds. |
| Locale variants (`/gb/`, `/us/`, …) | Page content is keyed by ids, not locale; no normalisation needed beyond what `extract_apple_episode_id` already does. |
| Very large feeds (20VC = 1,487 items, ~5 MB) | One-shot fetch+parse at import time, miss path only — acceptable; no caching in v1. |

---

## Testing

Extend `tests/unit/services/test_import_service_apple.py` (fixtures under
`tests/fixtures/apple/`):

- **Tier-3 happy path** — Tier 1 and 2 return misses, page fixture
  (captured, trimmed copy of the Storonsky episode page) resolves; assert
  title, enclosure, pub date, duration, and parent feed all land in
  `CanonicalSource`; assert `apple_lookup_tier=3` logged.
- **RSS cross-match** — each match strategy (enclosure / title /
  date+title) hit in isolation; feed-miss and feed-error paths degrade to
  scraped values with a warning, never an exception.
- **Missing `streamUrl`** in the episode node → `ResolverError`
  mentioning the RSS-feed escape hatch.
- **No `serialized-server-data`** in fetched HTML → same hard error.
- **Multiple candidate nodes** → the enclosure-bearing node wins.
- **Tier ordering** — Tier 3 fetcher is never invoked when Tier 1 or
  Tier 2 resolves (guard against quietly scraping on every import).
- **Untrusted-field hygiene** — non-string `streamUrl`, non-numeric
  `duration`, garbage `releaseDate` are dropped/type-checked, not raised.

Integration (one, gated `NETWORK_TESTS=1`): the real failing URL resolves
end-to-end and the enclosure host is libsyn.

---

## Implementation Phases

Single phase — one PR.

### Phase 1 — Tier-3 fallback end-to-end

- `_apple_page_episode_lookup` + `_apple_rss_cross_match` in
  [import_service.py](../thestill/services/import_service.py).
- Widen `AppleEpisodeLookup` to carry the pasted URL; thread it through
  `ApplePodcastsResolver.resolve()`.
- Structured tier logging (`apple_lookup_tier`, `apple_page_parse_failed`,
  `apple_rss_cross_match_miss`).
- Unit tests + captured page fixture as enumerated above.
- Manual smoke test against the Storonsky URL (record in PR description).

No schema changes, no API surface changes, no frontend changes.

---

## Resolved Decisions

| # | Question | Decision |
|---|---|---|
| A1 | Fetch more episodes from iTunes instead? | **Impossible.** `limit` caps at 200 and `offset` is ignored (V3). Not a trade-off — a dead end. |
| A2 | Where does the fallback live? | **Inside the lookup chain**, returning an iTunes-shaped dict. `resolve()` and everything downstream unchanged; one materialisation path (#42 FM-6). |
| A3 | Trust the scraped `streamUrl` directly? | **Cross-match RSS first, degrade gracefully.** The feed enclosure is canonical (dedupes cleanly if the show is later followed); the scrape alone still suffices when the feed is unreachable. |
| A4 | Scrape risk posture | **Accepted, contained.** `serialized-server-data` is undocumented and can change without notice. Containment: Tiers 1–2 (stable, decade-old API) keep covering recent episodes; Tier-3 breakage is loud, logged, and its error tells the user the RSS workaround. |
| A5 | AMP API as an alternative | **Rejected.** Token scraping is strictly more fragile than page scraping, for the same data. |

---

## Open Items

- **Fixture staleness.** The captured page fixture freezes today's JSON
  shape; when Apple drifts, unit tests stay green while production Tier 3
  breaks. The `NETWORK_TESTS=1` integration test is the canary — worth
  running on a schedule if Tier-3 failure logs ever show up.
- **Feed-fetch dedup.** Tier 3's RSS fetch duplicates the fetch the
  parent auto-add refresh performs moments later. Harmless (miss path
  only); if import latency ever matters, pass the parsed feed forward.

---

## Cross-References

- **Spec #31** — parent import machinery; this changes only the Apple
  lookup depth, not the resolver protocol or dedup contract.
- **Spec #37** — sibling resolver spec; the captured-fixture +
  injectable-fetcher test pattern here mirrors it.
- **Spec #42** — failure-mode checklist applied throughout (FM-1, FM-4,
  FM-6, FM-7).
