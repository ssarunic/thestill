# Substack Import Resolver Specification

> **Status:** 📝 Draft
> **Created:** 2026-05-08
> **Updated:** 2026-05-08
> **Author:** Product & Engineering
> **Related:** [#31 import-arbitrary-episodes](31-import-arbitrary-episodes.md)

---

## Executive Summary

Add a `SubstackResolver` to the import lineup so users can paste a Substack
post URL — e.g. `https://open.substack.com/pub/lenny/p/he-saved-openai-bret-taylor`
or `https://www.lennysnewsletter.com/p/...` — and have the embedded podcast
audio land in their inbox. Reuses every primitive shipped in #31 (canonical
id, parent-podcast bootstrap, inbox-first UX, shared-episode dedup); the
only new code is one resolver and a small URL-pattern helper.

**Mental model:** Substack posts are RSS-feed-backed podcast episodes
wearing a magazine layout. Each post page already embeds the enclosure URL
(`api.substack.com/api/v1/audio/upload/<uuid>/src`) plus the publication's
RSS feed link. We extract both, hand the audio URL down the existing
pipeline, and bootstrap the parent podcast from `/feed` like Apple does
from iTunes.

**Key principle:** No new pipeline, no new schema, no new endpoint.
Substack drops in next to Apple/YouTube/BareAudio and inherits everything.

---

## Table of Contents

1. [Motivation](#motivation)
2. [Product Requirements](#product-requirements)
3. [URL Surface](#url-surface)
4. [Resolver Design](#resolver-design)
5. [Edge Cases](#edge-cases)
6. [Testing](#testing)
7. [Implementation Phases](#implementation-phases)
8. [Resolved Decisions](#resolved-decisions)
9. [Open Items](#open-items)
10. [Cross-References](#cross-references)

---

## Motivation

Spec #31 picked the three highest-volume share surfaces (YouTube, Apple,
bare audio) and shipped the rest of the import machinery as a pluggable
resolver protocol. Substack now hosts a non-trivial slice of the
podcast-style content users want transcribed (Lenny, Pivot's Substack
edition, Search Engine cross-posts, individual essays-as-audio), and its
share UI defaults to `open.substack.com/pub/...` — which is neither RSS,
Apple, YouTube, nor a bare `.mp3`, so the v1 resolver lineup currently
returns `unsupported_url`.

The fix is small enough that it's not worth a dedicated phase 5 of #31:
extracting it into its own spec keeps #31's "import primitive shipped"
status clean and gives this resolver room to document the Substack-specific
quirks (paywalls, dual hosts, posts that aren't podcast episodes) without
bloating the parent doc.

---

## Product Requirements

### User Stories

| As a... | I want to... | So that... |
|---------|--------------|------------|
| User | Paste a Substack post URL with audio | The episode lands in my inbox like any other import |
| User | Paste either the `open.substack.com/pub/<pub>/p/<slug>` form or the publication's custom domain (`<pub>.substack.com/p/<slug>`, `www.lennysnewsletter.com/p/<slug>`) | The two URL shapes collapse to one episode |
| User | Get a clear error if the post is text-only (no audio) | I don't wonder why nothing happened |
| User | See the publication appear as a real auto-added podcast | "Follow this publication" CTA works post-import like it does for YouTube/Apple |

### Core Behaviors

1. **URL recognition.** The resolver matches when the host is one of:
   - `open.substack.com` (the share-link form).
   - `*.substack.com` (publication-hosted).
   - Any host whose post HTML advertises `<meta name="generator" content="Substack" />` and exposes a `<link rel="alternate" type="application/rss+xml">` to a `/feed` endpoint that returns Substack-shaped RSS. Matched lazily via a HEAD/GET probe so custom-domain publications (`www.lennysnewsletter.com`) work without a hard-coded list.
2. **Audio extraction.** The resolver fetches the post HTML, locates the
   first `"podcast_url":"…/api/v1/audio/upload/<uuid>/src"` JSON field, and
   uses that as `audio_url`. Format is .m4a in observed cases; the
   downloader does not need to care.
3. **Canonical id.** `substack:<pub_slug>:<post_slug>` — derived from the
   canonical post URL (`og:url` meta tag), not the pasted form. Two share
   variants of the same post collapse to one episode.
4. **Parent podcast.** The publication's RSS feed (`<link rel="alternate"
   type="application/rss+xml">` on the post page, typically `/feed` on the
   publication's primary domain) becomes a `CanonicalParent` exactly like
   Apple's `feedUrl`. The publication is upserted as `auto_added=1` and
   the existing `_feed_manager` one-shot refresh fills in description /
   cover / episode list.
5. **No follow side-effect.** Same contract as the rest of #31: importing
   does not subscribe the user to the publication.
6. **Idempotent.** Re-pasting either URL form returns the existing
   episode + inbox row.

### Non-Goals

- Substack newsletter text-only posts. If `podcast_url` is absent or `null`,
  reject with a clear error — we don't have a "transcribe an essay" pipeline
  and faking one via TTS belongs in spec #34, not here.
- Paywalled audio that requires the publication's auth cookie. Probe and
  fail loudly; do not store or accept user-supplied Substack cookies in v1.
- Browsing or importing whole publications. Following the parent (after
  auto-add) feeds the existing RSS refresh loop — no separate "import
  publication" surface.
- Substack Notes, comment threads, or Substack Chat content.

---

## URL Surface

Verified against the user's example URL on 2026-05-08:

```
Pasted:    https://open.substack.com/pub/lenny/p/he-saved-openai-bret-taylor?utm_campaign=...
HTML has:  <meta property="og:url" content="https://www.lennysnewsletter.com/p/he-saved-openai-bret-taylor" />
           <link rel="alternate" type="application/rss+xml" href="/feed" title="Lenny's Newsletter" />
           "podcast_url":"https://api.substack.com/api/v1/audio/upload/7bf3abae-fbfc-45dd-91da-47f63cf6d49d/src"
RSS:       https://www.lennysnewsletter.com/feed   → 200, Substack-generated RSS with itunes:* tags
```

Three URL shapes the resolver must accept and collapse:

| Pasted host | Example | Notes |
|---|---|---|
| `open.substack.com` | `open.substack.com/pub/<pub>/p/<slug>` | The "Share" button default. Always 200s; HTML carries `og:url` to the canonical form. |
| `<pub>.substack.com` | `lenny.substack.com/p/<slug>` | Publication on Substack's domain. |
| Custom domain | `www.lennysnewsletter.com/p/<slug>` | Requires the generator/RSS-link probe to recognise. |

All three forms expose the same `og:url` and the same embedded
`podcast_url`, so canonicalisation is straightforward.

---

## Resolver Design

### URL pattern helper

Add to [thestill/utils/url_patterns.py](../thestill/utils/url_patterns.py):

```python
def is_substack_url(url: str) -> bool:
    """Cheap host-based prefilter. The full match is HTML-confirmed."""
    host = (urlparse(url).hostname or "").lower()
    if host == "open.substack.com" or host.endswith(".substack.com"):
        return True
    return False  # custom domains fall through to HTML probe in resolver

def extract_substack_post_slug(url: str) -> tuple[str | None, str | None]:
    """Return (pub_slug, post_slug) where deducible from the URL alone."""
    # open.substack.com/pub/<pub>/p/<slug> or <pub>.substack.com/p/<slug>
    ...
```

The host-based prefilter is for the resolver's `matches()`. Custom-domain
publications match via a lazy HTML probe — see "Match strategy" below.

### `SubstackResolver` class

Lives next to `ApplePodcastsResolver` in
[thestill/services/import_service.py](../thestill/services/import_service.py).

```python
class SubstackResolver:
    """
    Resolver for Substack post URLs that embed podcast audio.

    Handles three URL shapes:
      - open.substack.com/pub/<pub>/p/<slug>   (share-link form)
      - <pub>.substack.com/p/<slug>            (Substack-hosted)
      - <custom-domain>/p/<slug>               (publication custom domain)

    All three carry a canonical og:url and the same embedded
    "podcast_url" JSON field. The resolver fetches the post HTML once,
    extracts audio + metadata, and resolves the parent feed via the
    publication's <link rel="alternate" type="application/rss+xml"> tag.
    """

    _AUDIO_URL_RE = re.compile(
        r'"podcast_url"\s*:\s*"(https://api\.substack\.com/api/v1/audio/upload/'
        r'[0-9a-f-]+/src)"'
    )
    _OG_URL_RE = re.compile(r'<meta\s+property="og:url"\s+content="([^"]+)"')
    _OG_TITLE_RE = re.compile(r'<meta\s+property="og:title"\s+content="([^"]+)"')
    _OG_IMAGE_RE = re.compile(r'<meta\s+property="og:image"\s+content="([^"]+)"')
    _PUB_DATE_RE = re.compile(
        r'<meta\s+property="article:published_time"\s+content="([^"]+)"'
    )
    _RSS_LINK_RE = re.compile(
        r'<link[^>]+rel="alternate"[^>]+type="application/rss\+xml"[^>]+href="([^"]+)"'
    )
    _GENERATOR_RE = re.compile(
        r'<meta[^>]+name="generator"[^>]+content="Substack"', re.IGNORECASE
    )

    def __init__(self, *, fetcher: Optional[Callable[[str], str]] = None) -> None:
        # Override in tests with a fake fetcher returning canned HTML.
        self._fetch = fetcher or _default_substack_fetch

    def matches(self, url: str) -> bool:
        if is_substack_url(url):
            return True
        # Custom-domain probe: only fires when the URL looks like a post
        # path (contains "/p/") AND the resolver above didn't claim it.
        # The probe is the same fetch we'd do in resolve(), so we cache
        # the body via a small request-scoped memo to avoid double-fetching.
        return self._probe_custom_domain(url)

    def resolve(self, url: str) -> CanonicalSource:
        html = self._fetch(url)
        if not self._GENERATOR_RE.search(html):
            raise ResolverError("URL does not look like a Substack post.")
        canonical_url = self._extract(self._OG_URL_RE, html) or url
        audio_url = self._extract(self._AUDIO_URL_RE, html)
        if not audio_url:
            raise ResolverError(
                "This Substack post has no podcast audio. Paste a post that "
                "embeds an audio player (the speaker icon next to the title)."
            )
        title = self._extract(self._OG_TITLE_RE, html) or "Untitled Substack episode"
        image = self._extract(self._OG_IMAGE_RE, html)
        pub_date = _parse_iso8601(self._extract(self._PUB_DATE_RE, html))
        feed_href = self._extract(self._RSS_LINK_RE, html)
        feed_url = urljoin(canonical_url, feed_href) if feed_href else None

        pub_slug, post_slug = _split_substack_slugs(canonical_url)
        canonical_id = f"substack:{pub_slug}:{post_slug}"

        parent: Optional[CanonicalParent] = None
        publication_title = _publication_title_from_feed_link(html)
        if feed_url and pub_slug:
            parent = CanonicalParent(
                external_id=pub_slug,
                rss_url=feed_url,
                title=publication_title or pub_slug,
                description="",
                image_url=None,  # filled by feed_manager refresh
            )

        return CanonicalSource(
            kind="substack_episode",
            canonical_id=canonical_id,
            audio_url=audio_url,
            title=title,
            description="",
            duration_seconds=None,    # not in the post HTML; RSS has it post-refresh
            pub_date=pub_date,
            image_url=image,
            source_handle=publication_title or pub_slug or "Substack",
            external_id=post_slug or canonical_id,
            parent=parent,
        )
```

### Match strategy

`SubstackResolver.matches()` runs a two-stage check so the cheap `*.substack.com` host-prefix path stays free of network calls and the
custom-domain path opts into a single lazy fetch:

1. **Cheap path** — `is_substack_url(url)` returns `True` for
   `open.substack.com` and `*.substack.com`. Resolver claims the URL.
2. **Custom-domain probe** — if the URL contains `/p/` and the cheap path
   missed, fetch the page once and check for the `Substack` generator
   meta. The probe response is cached on the resolver instance keyed by
   normalised URL so `resolve()` doesn't re-fetch.

The resolver lineup order in `ImportService.__init__` becomes:

```python
[ApplePodcastsResolver(), YouTubeResolver(),
 SubstackResolver(), BareAudioResolver()]
```

`SubstackResolver` sits ahead of `BareAudioResolver` so that
`api.substack.com/api/v1/audio/upload/<uuid>/src` URLs pasted *directly*
(not the post URL) still flow through the bare-audio path — the Substack
resolver only claims post URLs, not raw enclosure URLs.

### Reuse, don't reinvent

- **Parent upsert** — uses the existing `_upsert_auto_added_podcast`
  path. No new column, no new flag.
- **One-shot feed refresh** — the existing `_feed_manager.refresh_feed`
  call after auto-add fills in the publication description, cover, and
  full episode list from `/feed`. Substack's RSS includes itunes tags,
  so this Just Works.
- **Audio download** — the .m4a returned by `api.substack.com/api/v1/audio/upload/<uuid>/src` is a normal HTTP audio resource. The existing
  `download` stage handles it without changes (verified: `Content-Type: audio/mp4`, `Content-Length` present).

---

## Edge Cases

| Case | Behavior |
|---|---|
| Text-only post (`podcast_url` absent or `null`) | `ResolverError("This Substack post has no podcast audio…")`. Surfaces as 400 `resolver_failed` from `POST /api/imports`. |
| Paywalled audio (probe HEAD on enclosure 401/403) | `ResolverError("This episode is paywalled. Importing paywalled Substack audio isn't supported in v1.")`. Detected by a HEAD on `audio_url` *before* enqueueing the download task — the inbox row should not be created when we know the pipeline will fail. |
| Custom-domain publication (e.g. `www.lennysnewsletter.com`) | Match via the generator-meta probe; resolve identically. |
| Multiple `podcast_url` hits in HTML | The post page embeds related-episode cards from the same publication. Use the *first* match — the post-detail JSON appears before the related-episodes list. Verified against Lenny's post. If brittleness shows up, switch to scoping the regex to the JSON object that also contains the matching `og:url` slug. |
| Substack post that links to a third-party host (rare; pre-Substack imports) | `podcast_url` may be a `.libsyn.com` / `.megaphone.fm` URL rather than `api.substack.com/...`. Resolver passes it through as `audio_url` regardless of host — we just need a fetchable enclosure. |
| `og:url` missing | Fall back to the input URL (after stripping tracking params). Canonical id derives from URL path slugs; this still collapses share-variants because `utm_*` are stripped upstream. |
| Substack Notes / chat URL pasted | Path won't contain `/p/`; resolver declines and BareAudio also declines → `unsupported_url`. The error message in spec #31's modal already reads well. |

---

## Testing

Unit tests in `tests/unit/services/test_import_service_substack.py`,
mirroring `test_import_service_apple.py`:

- **Pasted share URL → canonical resolution** using a captured fixture of
  Lenny's post HTML (saved under `tests/fixtures/substack/lenny-bret-taylor.html`).
- **Three URL shapes collapse to one canonical id** (`open.substack.com`,
  `lenny.substack.com`, custom-domain).
- **Tracking params stripped** before canonicalisation.
- **Text-only post** raises `ResolverError` with the documented message.
- **Paywalled audio** (mocked HEAD returning 401) raises `ResolverError`
  before the inbox row is created.
- **Multi-`podcast_url` HTML** picks the first (post-detail) audio.
- **Generator probe** correctly identifies a custom-domain Substack post
  and rejects a non-Substack page that happens to contain a `/p/` path.

Integration test (one): paste the Lenny URL → episode appears, parent
publication auto-added, inbox row visible, download stage receives a
real `.m4a` URL. Gated behind a `NETWORK_TESTS=1` flag like other
network-touching tests in the repo.

---

## Implementation Phases

Single phase — this is one resolver, fits in one PR.

### Phase 1 — `SubstackResolver` end-to-end

- Add `is_substack_url` + `extract_substack_post_slug` (or equivalent
  internal helper) to [url_patterns.py](../thestill/utils/url_patterns.py).
- Add `SubstackResolver` class to
  [import_service.py](../thestill/services/import_service.py) and slot it
  into the default resolver lineup ahead of `BareAudioResolver`.
- Extend the modal copy in the import UI to mention Substack alongside
  the existing kinds.
- Unit tests as enumerated above.
- One captured-HTML fixture committed to `tests/fixtures/substack/`.
- Manual smoke test against the user's URL and one custom-domain
  publication (record results in the PR description).

No schema changes. No API surface changes (the existing
`POST /api/imports` accepts the new URL kind transparently). The only
user-visible change beyond "it works" is the modal copy.

---

## Resolved Decisions

| # | Question | Decision |
|---|---|---|
| S1 | Match by hostname allowlist or HTML probe? | **Both.** Cheap host check for `*.substack.com`; one-shot HTML probe for custom domains. The probe response is reused by `resolve()` so there's no double fetch. |
| S2 | Canonical id shape | **`substack:<pub_slug>:<post_slug>`.** Deterministic from `og:url`. Avoids collisions across publications. |
| S3 | Parent podcast handling | **Auto-add the publication via `/feed` RSS** — same path Apple uses. Following remains a separate explicit action (matches #31's contract). |
| S4 | Paywalled audio | **Reject with a clear error.** No cookie storage in v1. |
| S5 | Text-only posts | **Reject with a clear error.** TTS-of-essay is a separate product (see spec #34's TTS pipeline) and shouldn't ride the import path. |
| S6 | Where in the resolver lineup | **Ahead of `BareAudioResolver`.** Substack post URLs aren't bare-audio URLs, but raw `api.substack.com/.../src` URLs are — putting Substack first ensures post URLs resolve correctly, and bare-audio still claims direct enclosure URLs. |

---

## Open Items

- **Episode duration.** Not present in the post HTML JSON. Filled by the
  one-shot `feed_manager.refresh_feed` call after parent auto-add (RSS
  carries `itunes:duration`). If that refresh fails, the episode shows
  with an unknown duration until first playback measures it. Acceptable
  for v1; revisit if it produces enough UX papercuts.
- **Cover image.** `og:image` is the post hero, not the publication
  cover — fine as the episode image, but the parent podcast cover comes
  from the post-auto-add RSS refresh. Same fallback story as duration.
- **Cross-posted publications.** A post that lives in two Substacks would
  produce two distinct canonical ids (different `pub_slug`). Acceptable —
  spec #32 (episodes-as-first-class) is the right place to merge cross-
  posted instances; doing it here would couple this resolver to a
  not-yet-shipped membership model.

---

## Cross-References

- **Spec #31** — Parent spec. This resolver is the first follow-up
  resolver added after the v1 lineup (Apple/YouTube/BareAudio) and
  follows the same protocol, dedup contract, and parent-podcast
  bootstrap path.
- **Spec #32** — When membership lands, cross-posted Substack episodes
  become trivial to merge; until then they're acceptably separate.
- **Spec #34** — Briefing audio. If a future "narrate this Substack
  essay" feature emerges, it belongs there (TTS pipeline), not in this
  resolver.
