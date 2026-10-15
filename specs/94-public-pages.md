# Public Pages

> **Status:** 🚧 Implemented, in review (branch `feat/94-public-pages`, 2026-10-15); see [Outcome](#outcome-2026-10-15)
> **Created:** 2026-10-08
> **Updated:** 2026-10-15
> **Priority:** High. Every other growth idea (launch posts, directory listings, share links, a digest) needs a page a stranger can land on. Today there is none.
> **Author:** Product & Engineering
> **Related:** [#13 multi-user-shared-podcasts](13-multi-user-shared-podcasts.md) (the corpus is shared across users, which is what makes it publishable), [#25 security-audit-and-hardening](25-security-audit-and-hardening.md) (CSP, default-deny routers), [#42 robustness-and-failure-mode-hardening](42-robustness-and-failure-mode-hardening.md), [#54 summary-segment-citations](54-summary-segment-citations.md), [#73 mobile-list-row-density](73-mobile-list-row-density.md) (tokens the landing page reuses), [#76 episode-detail-page-hierarchy](76-episode-detail-page-hierarchy.md), [#80 share-to-thestill](80-share-to-thestill.md) (root static files, `next` through login), [#87 episode-platform-links](87-episode-platform-links.md) (Listen on … links), [#91 transcript-and-summary-export](91-transcript-and-summary-export.md), [#93 native-web-capabilities](93-native-web-capabilities.md) (manifest, icons)

---

## Executive summary

A logged-out visitor to thestill.me sees a Google button and the words
"Podcast Intelligence". Nothing on the hosted site says what the product
does, what a summary looks like, or why anyone would sign in. The GitHub
README is the only description of the product, and it is written for
people who want to run a CLI. No page is indexable, no link unfurls with
an image, and the Login page promises Terms and Privacy pages that do not
exist.

This spec makes four kinds of page readable without a session, in
multi-user mode only:

| Page | URL | What a visitor gets |
|---|---|---|
| Landing | `/` | One sentence, one real summary, three outcomes, Sign in |
| Podcast | `/podcasts/:slug` | Artwork, description, Listen-on links, recent summarised episodes |
| Episode | `/podcasts/:slug/episodes/:slug` | Header, description, the **summary**; the transcript stays behind sign-in |
| Entity (Phase 2) | `/entities/:type/:slug` | Who or what it is, which shows discuss it, recent mentions |

Plus `/top`, `/terms`, `/privacy`, `robots.txt` and `sitemap.xml`.

Three decisions shape the design:

1. **Publish the shared corpus, never the user.** Spec #13 already
   processes each podcast once for everyone, so a podcast or episode page
   reveals nothing about any individual. Inbox, read state, saved rows,
   follows, briefings, progress and search stay behind a session.
2. **Summaries public, transcripts private.** The summary is a document
   thestill wrote; the transcript is the publisher's show in text form.
   The public episode page carries the summary, the publisher's own
   description, and Listen-on links back to Apple, Spotify and YouTube.
3. **Crawlers get real `<head>` tags from the server, not a rendered
   React app.** The SPA shell gains per-URL title, description, Open
   Graph, canonical and JSON-LD injected by the catch-all route. No SSR.

The API change is small because the read endpoints are already
user-agnostic: [api_podcasts.py:314](../thestill/web/routes/api_podcasts.py#L314)
and [api_podcasts.py:449](../thestill/web/routes/api_podcasts.py#L449)
take no user at all. They are private only because the router is
registered with `require_auth` at
[app.py:726](../thestill/web/app.py#L726). The spec moves an explicit
allowlist of read-only routes onto public routers and keeps default-deny
for everything else.

## Outcomes

| # | Outcome | Verified by |
|---|---|---|
| O1 | A stranger landing on `/` understands the product within one screen and can sign in | Vitest `Landing`, manual |
| O2 | Every episode with a real summary has a public page a crawler can index | pytest head-injection + sitemap tests, `@live` Playwright against thestill.me |
| O3 | A pasted episode link unfurls in Slack, iMessage and X with artwork, title and a one-line description | pytest OG tags, manual with the three previewers |
| O4 | No per-user data is reachable without a session, and no private page is indexable | pytest route-allowlist test, `robots` meta assertions |
| O5 | An anonymous visitor cannot make the server spend money or scrape the corpus cheaply | pytest "translation never generated for anonymous", rate-limit tests, transcript 401 |
| O6 | Signed-in users see exactly what they see today | existing Vitest/Playwright suites unchanged |

## Current state

- **Routing.** [App.tsx:52-66](../thestill/web/frontend/src/App.tsx#L52-L66):
  `/login` is the only route outside `ProtectedRoute`; `/` redirects to
  `/inbox`, which redirects anonymous visitors to `/login`.
  [ProtectedRoute.tsx:27-33](../thestill/web/frontend/src/components/ProtectedRoute.tsx#L27-L33)
  passes everything in single-user mode.
- **Shell.** [index.html](../thestill/web/frontend/index.html) has
  `<title>Thestill</title>`, no description, no Open Graph tags, no
  canonical, Vite's `vite.svg` favicon, no manifest.
  [app.py:791](../thestill/web/app.py#L791) serves that one file for
  every non-API path with `Cache-Control: no-cache`, GET only. `HEAD /`
  returns 405.
- **API.** [app.py:721-760](../thestill/web/app.py#L721-L760) registers
  every `/api` router except `auth` with `require_auth`; in production
  `/api/podcasts`, `/api/top-podcasts` and `/api/entities/…` return 401
  without a cookie. Unauthenticated today: `/api/auth/*`, `/health`,
  `/unsubscribe/*`, `/webhook/*`, `/mcp`.
- **Summary endpoint.** `GET …/summary?lang=xx` generates a missing
  translation synchronously with the LLM
  ([api_podcasts.py:502-513](../thestill/web/routes/api_podcasts.py#L502-L513))
  and detects an unrecorded summary language with one LLM call. Fine
  behind a session; a cost hole if opened as is.
- **Legal.** [Login.tsx](../thestill/web/frontend/src/pages/Login.tsx)
  says "By signing in, you agree to our Terms of Service and Privacy
  Policy." Neither page exists. Google's OAuth consent-screen
  verification also wants a privacy policy URL on the app's home page.
- **Scale.** Production on 2026-10-08: 99 podcasts, 6,224 episodes,
  2,760 with summaries. One sitemap file holds 50,000 URLs.
- **Measurement.** No product analytics anywhere in the frontend. Spec
  #75 rejected PostHog for LLM traces on confidentiality grounds; that
  decision does not cover page views. Product analytics is out of scope
  here (see Non-goals); this spec logs public views server-side only.

## Design

### 1. Visibility model

A page is **public** when it is served without a session in multi-user
mode. Single-user mode is unchanged: there is one implicit user, every
route is already open on the LAN, and the landing page never renders.

| Surface | Anonymous | Signed in |
|---|---|---|
| `/` | Landing page | Redirect to `/inbox` (today's behaviour) |
| `/login` | Sign-in card (unchanged; keeps working as a deep-link target) | Redirect to `/` |
| `/top` | Chart, no Follow buttons (Sign in to follow) | Unchanged |
| `/podcasts/:slug` | Metadata, Listen-on links, episodes with summaries | Unchanged |
| `/podcasts/:slug/episodes/:slug` | Header, description, summary; transcript tab replaced by a sign-in card | Unchanged |
| `/entities/:type/:slug` | Phase 2: entity page without per-user controls | Unchanged |
| `/terms`, `/privacy` | Static pages | Same |
| `/inbox`, `/briefings/*`, `/search`, `/settings`, `/episodes`, `/status`, `/queue`, `/failed`, `/podcasts` (the followed list) | Redirect to `/login?next=` | Unchanged |

Briefings are **not** public and get no share link in this spec. A
briefing is composed from one user's inbox, so its contents are a list of
what that person follows. A token-scoped share (the pattern
[unsubscribe.py](../thestill/web/routes/unsubscribe.py) uses) is the
right shape if it is ever wanted; it is a separate spec.

#### Per-podcast opt-out

New column `podcasts.public_pages` (boolean, default `true`), one Alembic
migration, exposed on the Podcast model and through
`thestill set-podcast-visibility --podcast-id X --public|--private`.
When false: public API routes answer 404 for the podcast and its
episodes, the SPA shell is served with status 404 and `noindex`, and the
sitemap skips them. This is the takedown path; it has to exist before the
first public page does. Signed-in users are unaffected.

### 2. Public API routers

Each affected module gains a second `APIRouter` named `public_router`
holding only the routes below. `app.py` registers the public routers
**without** `require_session`, next to the existing registration, and the
default-deny comment is updated to name the allowlist. The handlers
themselves do not move or fork (FM-6): a route lives on exactly one
router.

| Route | Module | Notes |
|---|---|---|
| `GET /api/podcasts/{slug}` | api_podcasts | keeps `get_current_user`; `is_following` is false for anonymous |
| `GET /api/podcasts/{slug}/episodes` | api_podcasts | adds `has_summary=true` filter; public pages only list summarised episodes |
| `GET /api/podcasts/{slug}/episodes/{slug}` | api_podcasts | see payload hygiene below |
| `GET /api/podcasts/{slug}/episodes/{slug}/summary` | api_podcasts | see cost guard below |
| `GET /api/top-podcasts` | api_top_podcasts | unchanged |
| `GET /api/episodes/recent-summaries` | api_episodes | **new**; cross-corpus feed for the landing page, see below |
| `GET /api/entities/{type}/{slug}` | api_entities | Phase 2 |
| `GET /api/entities/{type}/{slug}/episodes` | api_entities | Phase 2 |
| `GET /api/episodes/{id}/entities` | api_entities | Phase 2 |

Everything else, including transcript, transcript words, search, follow,
inbox, briefings, imports and commands, stays where it is. The
cross-corpus `GET /api/episodes` list stays session-only: it has no
summary filter and exposes processing state.

**Recent summaries feed.** `GET /api/episodes/recent-summaries?limit=N`
(default 6, max 12) returns the newest episodes across the corpus whose
podcast has `public_pages = true` and whose summary is real (not `N/A`),
ordered by the time the summary was written, newest first. Each item is
the public episode shape plus podcast title, slug and artwork, and the
first sentence of the summary's gist for the landing card. It is the
only cross-corpus public read, it takes no filters beyond `limit`, and
the repository query behind it reuses the sitemap predicate from §4 so
the feed and the sitemap can never disagree about what is public.
Cached in-process for five minutes.

**Payload hygiene.** Public responses must not carry storage paths,
pipeline state internals or provider details. The episode payload is
audited and any `*_path`, `state`-adjacent failure fields and import
origin internals are dropped from the public shape; the signed-in shape
is unchanged. A test walks every public route's response and fails on
any key ending in `_path`. Pydantic response models for the public
routes make the allowlist explicit (constitution §5).

**Cost guard.** The public summary route never calls an LLM:

- `lang` is honoured only when that language is already in
  `available_languages`; otherwise 404. No synchronous translation.
- If the canonical language is unrecorded, the route serves the stored
  summary as the podcast's language and returns `available_languages`
  with that one entry. Detection stays a signed-in side effect.
- A `N/A` summary is a 404 on the public route, not a page that says
  "N/A" (FM-1 in reverse: a non-result must not render as content).

Implemented as a keyword flag on the service call
(`allow_generate=False`), asserted by a test whose provider factory
raises if invoked.

**Rate limit.** New bucket in
[rate_limit.py](../thestill/web/middleware/rate_limit.py):
`RATE_LIMIT_PUBLIC_MAX` (default 120) per
`RATE_LIMIT_PUBLIC_WINDOW_SECONDS` (default 60), keyed by client IP as
resolved behind `TRUSTED_PROXIES`, applied as a dependency on the public
routers only. Requests carrying a valid session skip the bucket (they
are keyed by user elsewhere). Over the limit: 429 with `Retry-After`.
Crawlers that respect `robots.txt` crawl-delay stay well under it.

**Cache headers.** Authentication accepts a session cookie **or** a
bearer token ([dependencies.py:208-220](../thestill/web/dependencies.py#L208-L220)),
so the policy keys on "any credential present", not on the cookie
alone. A public GET that carries neither the auth cookie nor an
`Authorization` header returns
`Cache-Control: public, max-age=300, stale-while-revalidate=3600`
(summary responses already carry a content-hash ETag from
[responses.py:176](../thestill/web/responses.py#L176)). The same route
with either credential returns `Cache-Control: private, no-cache`.
**Every** public-route response, anonymous or not, carries
`Vary: Cookie, Authorization`, so a cached anonymous payload is never
reused for a request that presents a credential, and the reverse never
happens either ([RFC 9111 §4.1](https://www.rfc-editor.org/rfc/rfc9111.html#section-4.1)).
One helper reads the same credential extractor the auth dependency uses
(no second definition of "has a credential", FM-6) and sets both
headers; a test covers cookie, bearer and anonymous on every public
route.

### 3. HTML shell: head injection, status codes, HEAD

The catch-all in [app.py:791](../thestill/web/app.py#L791) becomes
`api_route(methods=["GET", "HEAD"])` and, for public content paths,
resolves the entity and injects tags before `</head>`:

```html
<title>{Episode title} · {Podcast} · Thestill</title>
<meta name="description" content="{first sentence of the summary's gist, ≤ 160 chars}">
<link rel="canonical" href="{PUBLIC_BASE_URL}/podcasts/{slug}/episodes/{slug}">
<meta property="og:type" content="article">
<meta property="og:title" content="…">
<meta property="og:description" content="…">
<meta property="og:image" content="{episode image_url or podcast image_url or /og-default.png}">
<meta property="og:url" content="{canonical}">
<meta name="twitter:card" content="summary_large_image">
<script type="application/ld+json">{ "@type": "PodcastEpisode", … }</script>
```

Rules:

- The page template is `index.html` with one marker comment; injection
  is string replacement of that marker, and every value passes through
  `html.escape`. A test feeds a title containing `</title><script>` and
  asserts it is inert.
- Which paths get content tags: `/`, `/top`, `/podcasts/{slug}`,
  `/podcasts/{slug}/episodes/{slug}`, and `/entities/{type}/{slug}` in
  Phase 2. Every other path gets the default title plus
  `<meta name="robots" content="noindex">`.
- Unknown slug, hidden podcast or `N/A` summary on a public path: the
  shell is served with **status 404** and `noindex`, so a crawler drops
  the URL; the SPA renders its existing not-found state.
- Lookups are the same repository calls the API uses, behind three
  guards, because the shell is reachable by anyone and every distinct
  URL is a potential Postgres query:
  1. **Slug validation before any query.** Slugs come from
     [slug.py:29](../thestill/utils/slug.py#L29) (python-slugify,
     lowercase `[a-z0-9-]`, at most 100 characters plus a `-N` suffix).
     A path segment that does not match `^[a-z0-9]([a-z0-9-]{0,110})$`
     is served the default tags with status 404 and no lookup at all.
  2. **Positive and negative in-process cache**, 120 seconds, keyed by
     path, bounded in size. A miss (unknown slug, hidden podcast, `N/A`
     summary) is cached as a miss, so repeating a bad URL costs nothing
     after the first hit.
  3. **The same per-IP bucket as the public API** (§2) is charged for
     every lookup that reaches the repository, i.e. a cache miss. Over
     the limit the shell returns 429 with `Retry-After`, like the API;
     crawlers treat 429 as temporary and come back. Cached hits and
     non-content paths are never charged, so a signed-in user reloading
     `/inbox` is unaffected.
  A repository error (not a miss) serves the default tags with status
  200 and logs at warning; it must not turn into a 500 for the SPA
  shell, and it is not cached.
- The shell keeps `Cache-Control: no-cache, must-revalidate`.
- The JSON-LD uses schema.org `PodcastSeries` / `PodcastEpisode` with
  `associatedMedia` pointing at the publisher's enclosure URL, not at
  thestill.

Default assets, served as root-level static files per
[spec #80 §3.2](80-share-to-thestill.md): `favicon.svg`,
`apple-touch-icon.png`, `og-default.png` (1200×630), `robots.txt`. The
manifest itself stays with spec #80 / #93.

### 4. robots.txt and sitemap.xml

The body of every public page is still client-rendered, so a crawler
that renders JavaScript (Googlebot does) must be allowed to fetch the
JSON the page loads and the one auth-bootstrap call the shell makes
(`GET /api/auth/status`, [AuthContext.tsx:51](../thestill/web/frontend/src/contexts/AuthContext.tsx#L51)).
Head tags alone give previews, not an indexable summary body. The file
therefore allows exactly the public API paths from §2 and disallows the
rest of `/api/`; Google and Bing resolve conflicts by the most specific
(longest) matching rule, so the `Allow` lines win for those prefixes.

`robots.txt` (static):

```
User-agent: *
Allow: /$
Allow: /top
Allow: /podcasts/
Allow: /entities/
Allow: /api/auth/status
Allow: /api/podcasts/
Allow: /api/top-podcasts
Allow: /api/entities/
Allow: /api/episodes/recent-summaries
Allow: /api/episodes/*/entities
Disallow: /inbox
Disallow: /briefings
Disallow: /search
Disallow: /settings
Disallow: /episodes
Disallow: /status
Disallow: /queue
Disallow: /failed
Disallow: /login
Disallow: /api/
Disallow: /mcp
Sitemap: {PUBLIC_BASE_URL}/sitemap.xml
```

`/podcasts` without a slug is the signed-in followed list; the shell
serves it with `noindex`, and `Allow: /podcasts/` only admits the slug
paths. The same trailing slash on `Allow: /api/podcasts/` keeps the bare
followed-podcasts list disallowed. Transcript, follow and search routes
under the allowed API prefixes answer 401 to a crawler and are never
requested by an anonymous page, so they need no extra rule. `/entities/`
and its API prefix are listed from day one so Phase 2 needs no robots
change. A test fetches `robots.txt` and asserts, with a robots parser,
that every public API route is fetchable and `/api/inbox` is not.

`sitemap.xml` is a route, not a file: all public podcasts, every episode
of theirs with a real summary (`lastmod` = summary written time), and in
Phase 2 every entity with at least one mention in a public podcast.
Generated on demand, cached in-process for one hour, split into a
sitemap index at 40,000 URLs. Hidden podcasts and `N/A` summaries are
excluded; a test asserts both.

### 5. Frontend

**Viewer mode.** `useAuth()` already exposes `isAuthenticated` and
`isMultiUser`. A derived `isAnonymous = isMultiUser && !isAuthenticated`
is added to the context so pages do not recompute it.

**Routes.** [App.tsx](../thestill/web/frontend/src/App.tsx) gains a
`PublicLayout` branch rendered when `isAnonymous`: a slim header (logo,
Top podcasts, Sign in) and a footer (GitHub, Terms, Privacy, "Summaries
are written by a language model"). Under it: `Landing`, `TopPodcasts`,
`PodcastDetail`, `EpisodeDetail`, `Entities` (Phase 2), `Terms`,
`Privacy`. Signed-in users keep today's `Layout` and route tree
untouched; `/` keeps redirecting to `/inbox`. `ProtectedRoute` redirects
to `/login?next=<current path>` so a Follow or Read-transcript click
completes after sign-in; the `next` validation and the
`/api/auth/google/login?next=` plumbing are the ones specified in
[spec #80 §3](80-share-to-thestill.md) and land here if #80 has not
shipped first.

**Shared page components in anonymous mode.** `PodcastDetail`,
`EpisodeDetail` and `TopPodcasts` are reused, not forked (FM-6). Each
reads `isAnonymous` and:

- hides Follow / Save / Send to my inbox / Mark read / Resume and the
  reading-position and read-on-view effects (spec #29 marking must not
  fire without a user);
- hides the episode list's inbox and progress decorations;
- replaces the Transcript tab in `EpisodeReader` with a card: "The full
  transcript, with speaker names and timestamps, is one sign-in away."
  Summary citations (spec #54) render as plain `[49:30]` text with no
  link, since there is no transcript to jump into;
- does not mount the player shell (spec #71) or the floating video tile
  (spec #61). Listening is a Listen-on row built from the spec #87
  platform links, plus the publisher's enclosure as a plain `<a>` for
  feeds with no platform match. Anonymous playback with progress is
  spec #90 territory and a possible Phase 3;
- shows the podcast's `copyright` line and `explicit` label when set.

**Landing page** (`pages/Landing.tsx`). Built from the spec #73 tokens
and the editorial rule that the intelligence is the hero:

1. Masthead: "Read your podcasts." One sentence under it: thestill turns
   every episode you follow into a summary you can read in three
   minutes, with the moments cited, and a morning briefing across all of
   them. One primary button: Continue with Google.
2. A real summary, live: the most recent public episode with a summary,
   rendered with the real `SummaryViewer` inside a reader frame, clamped
   to the first section with a "Read the whole summary" link to its
   public page. Served by `GET /api/episodes/recent-summaries?limit=1`
   (§2), so the page is never stale and needs no hand-maintained
   fixture.
3. Three outcomes, each one line with a small illustration from the real
   UI: the inbox ("every new episode, summarised the morning it drops"),
   the briefing ("one page across all your shows, narrated if you
   like"), the knowledge base ("search every mention of anything across
   every show you follow; ask Claude about it").
4. "Fresh this week": the same feed with `limit=6`, rendered as
   `ListRow`s linking to the public episode pages.
5. Footer: open source on GitHub, self-hostable, Terms, Privacy.

No carousel, no animation on load, no cookie banner (nothing is set
before sign-in), no install nag (spec #93's permission rule applies).
Page weight target: the landing must not pull the player, markdown
vendor chunk or entity chunks; `SummaryViewer` is the one exception and
loads lazily.

**Terms and Privacy** are static Markdown rendered by the existing
markdown pipeline from `frontend/public/legal/terms.md` and
`privacy.md`, so they can be edited without a code change. The copy is an
open question (below); the pages ship with a clearly labelled draft.

### 6. Share affordance (Phase 3)

The episode page's action row (spec #76, full) gains nothing. The
spec #91 Export menu gains **Copy link**, which copies the canonical public
URL; on phones it opens the Web Share sheet with title and URL. No new
endpoint. The link works for everyone because of sections 2 and 3; that
is the whole point of doing those first.

### 7. Measurement (server-side only)

Every served public page logs one structlog event:

```text
public_page.view  kind=episode|podcast|entity|landing|top  anonymous=true
                  referrer_host=…  ua_class=browser|bot|preview
```

`ua_class` is a coarse classifier (Googlebot, bingbot, Slackbot,
Twitterbot, facebookexternalhit, iMessage → `preview`/`bot`). No cookie,
no fingerprint, no IP in the event. Two saved CloudWatch queries in
[docs/logging-cloudwatch-queries.md](../docs/logging-cloudwatch-queries.md):
views per kind per day, and top referrer hosts. Sign-in conversion from
public pages is answered by the `next` parameter on the login event,
which already exists once spec #80's plumbing lands.

Product analytics (funnel, activation, retention) is deliberately a
separate decision; see Non-goals.

## Failure modes

| Risk | Guard |
|---|---|
| Public cache stores a signed-in response, or serves a cached anonymous body to a signed-in client | §2 cache helper: `private` whenever a cookie **or** bearer token is present, `Vary: Cookie, Authorization` on every public response; tested per route with cookie, bearer and neither |
| Anonymous request triggers LLM spend | §2 cost guard; test with a raising provider factory |
| Public route leaks storage paths or pipeline internals | Explicit Pydantic response models; `_path` key walker test |
| New `/api` router added later is accidentally public | Route-allowlist test: every route under `/api` either carries `require_auth`/`require_admin` or is on the written allowlist |
| Head injection renders attacker-controlled podcast metadata as HTML | `html.escape` on every value; XSS fixture test (FM-7: feed metadata is untrusted input) |
| Crawl of 3k episode URLs, or a flood of random slugs, hammers Postgres through the shell lookups | §3: slug regex before any query, positive + negative 120 s cache, the public per-IP bucket charged on every cache miss |
| Crawler can fetch the shell but not the JSON, so pages index as empty | §4 robots allows the public API prefixes and `/api/auth/status`; parser-backed test |
| Sitemap advertises pages that 404 (hidden podcast, `N/A` summary) | Same predicate used for sitemap, shell status and API 404; one function |
| Takedown request with no way to act | `public_pages` column + CLI before Phase 1 ships |
| Read-on-view marking or progress writes fire for anonymous visitors | Effects gated on `!isAnonymous`; Vitest asserts no `POST` from the anonymous reader |

## Non-goals

- Server-side rendering or a prerender service. Head injection covers
  previews and indexing; the body stays client-rendered.
- Public transcripts, public search, public briefings, public inbox.
- Anonymous playback with progress (spec #90).
- A newsletter, weekly digest or "this week across the charts" page.
  Worth doing, separate spec; it depends on this one.
- Product analytics. If wanted, it is its own spec (next free number)
  covering vendor choice against the spec #75 confidentiality stance,
  consent, and the signup → first follow → first briefing funnel. This
  spec only guarantees the server-side view log above.
- Single-user mode changes.
- The web app manifest and icons beyond the favicon and touch icon
  (spec #80 / #93).

## Phases

| Phase | Scope | Ships alone? |
|---|---|---|
| 0 | Shell hygiene: `HEAD` on the catch-all, favicon + touch icon, default `<meta name="description">`, default OG tags, `og-default.png`, `robots.txt` disallowing every private path, `noindex` on the shell, `/terms` + `/privacy` with draft copy | Yes, no behaviour change for users |
| 1 | `public_pages` column + CLI; public routers for podcast, episodes list, episode, summary, top; cost guard; rate limit; cache helper; head injection + 404 shell; sitemap; `PublicLayout`; anonymous mode in `PodcastDetail`, `EpisodeDetail`, `TopPodcasts`; `Landing` | Yes; this is the launchable unit |
| 2 | Entity pages and episode entity chips public; entity URLs in the sitemap; entity head tags | Yes |
| 3 | Copy link / Web Share in the Export menu; `@live` unfurl check in CI | Yes |

Phase 1 is the first thing a launch post can link to and should not
wait for 2 or 3.

## Implementation plan (2026-10-15)

This section supersedes the Phases table above where they differ. It
records the decisions taken before implementation started and the order
the work ships in. The UX brief ("Thestill public pages — UX design
brief", 2026-10-15) and the "Thestill Editorial" design system (tokens,
14 components, built from the Stitch project "Thestill Public Pages UX
Design") are the design inputs.

### Clarifications (2026-10-15)

| # | Question | Decision | Why |
|---|---|---|---|
| 1 | Where does the plan live? | Here, in #94; branch `feat/94-public-pages` in worktree `../thestill-94` | #94 is the spec being implemented |
| 2 | Reuse signed-in pages in an anonymous mode, or build separate public pages? | Separate public pages under `pages/public/` built from new `components/public/`; data hooks shared where they fit; signed-in pages untouched | `EpisodeReader` runs ~10 per-user hooks at mount (mark-read, task polling, transcript download) and the editorial design differs from the app; gating every hook is riskier than a separate tree. Supersedes §5's "reused, not forked" |
| 3 | Where does a person's "What they said" come from? | Mention rows (role not `speaking`) in segments whose `speaking` row resolves to that person | Mention excerpts are ±200 chars of real speech and carry the entity they are about, so the topic filter works; speaking rows hold only a first sentence |
| 4 | Featured quote ranking before #100 exists | Read-time heuristic: last 90 days, outside the first 90 s and last 60 s, longest excerpt that passes a lexical sponsor check | Cheap, deterministic; #100 replaces it |
| 5 | Person takedown | `entities.public_hidden` + `thestill set-entity-visibility` ship with the person pages | UX brief open question 2: required before person pages go live |
| 6 | Entity extraction in prod | Running (quotes from 2026-10-14 episodes resolve) | No Phase 0 fix needed |
| 7 | Sign-in return path | Built here: `next` validated on the client and server, carried through Google OAuth in a short-lived cookie | #80 never shipped it |
| 8 | Fonts | Self-host Newsreader via `@fontsource-variable/newsreader`; no CSP change | CSP blocks Google Fonts; Inter is already self-hosted |
| 9 | Shipping | Stacked PRs A–E below | Each phase reviewable alone |
| 10 | Defaults | RSS URL not public; single-user mode gets no public pages; Terms/Privacy ship as labelled drafts; one default OG image; 120 req/min/IP | Brief and #94 defaults |

### Architecture

**A separate public API namespace, `/api/public/*`.** Instead of moving
existing handlers onto `public_router`s (§2), public reads get their own
router module with explicit Pydantic response models. The public shapes
differ from the signed-in ones (no `state`, `external_id`, failure
fields, `rss_url`, `refresh_pending`, `is_following`), the cost guard is
structural (the public module never imports the LLM provider factory or
`refresh_on_open`), and the route allowlist test reduces to "everything
under `/api/public/` and nothing else is unauthenticated". Handlers call
the same services and repositories as the private routes; no SQL is
duplicated. `robots.txt` allows `/api/public/` and `/api/auth/status`
and disallows the rest of `/api/`.

**Visibility predicate in one place.** `PublicVisibility` (a small
service) answers `podcast_is_public(slug)`, `episode_is_public(...)`
(public podcast **and** a real summary: `summary_preview` non-empty and
not `N/A`), and filters entity-page lists to public podcasts and
non-hidden entities. API 404s, the shell's status codes and the sitemap
all call it (FM-6).

**Frontend split.** `AuthContext` gains `isAnonymous` (false while
loading). `App.tsx` renders a `PublicApp` route tree when `isAnonymous`:
`PublicLayout` (scoped `.public-theme` tokens, SiteHeader, SiteFooter,
scroll restoration, no PlayerProvider) and lazy public pages. Signed-in
routing is unchanged; `ProtectedRoute` sends anonymous visitors on
private paths to `/login?next=…`.

### Execution plan

**Phase A — public API (backend only).**

| # | File | Change |
|---|---|---|
| A1 | `thestill/migrations/versions/0015_public_visibility.py`, `postgres_schema.py`, `sqlite_podcast_repository.py`, `sqlite_entity_repository.py` | `podcasts.public_pages boolean NOT NULL DEFAULT true`, `entities.public_hidden boolean NOT NULL DEFAULT false` |
| A2 | `models/podcast.py`, podcast + entity repositories | Read/write the two flags; `set_podcast_public_pages`, `set_entity_public_hidden` |
| A3 | `cli.py` | `set-podcast-visibility`, `set-entity-visibility` |
| A4 | `web/public_access.py` (new) | `PublicVisibility`; credential-present helper; public cache headers (`public, max-age=300, stale-while-revalidate=3600` / `private, no-cache`, always `Vary: Cookie, Authorization`) |
| A5 | `web/middleware/rate_limit.py` | `PUBLIC_LIMIT` bucket; dependency that skips requests carrying a valid credential |
| A6 | `web/routes/api_public.py` (new), `app.py` | Podcast, summarised episodes (limit ≤ 50), episode, summary (`allow_generate=False`), recent summaries, top chart; mounted without `require_auth`, only in multi-user mode |
| A7 | `tests/integration/web/test_public_api.py` (new) | Anonymous 200s; hidden podcast 404; `N/A` summary 404; no LLM factory call; no refresh enqueue; no forbidden keys; cache headers per credential; rate limit; route allowlist over `app.routes` |

Gate: `PYTHONPATH=$PWD ./venv/bin/pytest tests/integration/web/test_public_api.py tests/unit/web -q` green; `ruff` clean on touched files.

**Phase B — shell, robots, sitemap.**

| # | File | Change |
|---|---|---|
| B1 | `web/public_shell.py` (new), `app.py` | GET+HEAD catch-all; per-path title/description/OG/canonical/JSON-LD injected at a marker in `index.html`; slug regex before lookup; 120 s positive/negative cache; 404 + `noindex` for unknown/hidden; private paths `noindex` |
| B2 | `app.py`, `frontend/public/` | `robots.txt`, `sitemap.xml` (index past 40k), `og-default.png`, `favicon.svg` routes before the catch-all |
| B3 | `frontend/index.html` | `<!--thestill:head-->` marker, default description |
| B4 | tests | Escaping (`</script>` in a title), status codes, HEAD, sitemap predicate parity, robots parser |

**Phase C — public frontend: layout, landing, chart, show, episode, sign-in return.**

| # | File | Change |
|---|---|---|
| C1 | `routes/auth.py` | `next` on `/google/login` validated (`/`-relative, not `//`, not `/login`), stored in a 10-minute cookie, honoured by the callback |
| C2 | `contexts/AuthContext.tsx`, `utils/safeNext.ts`, `ProtectedRoute.tsx`, `pages/Login.tsx` | `isAnonymous`; `login(next)`; redirects carry `next` |
| C3 | `package.json`, `package-lock.json`, `index.css`, `tailwind.config.js` | Newsreader font; `.public-theme` token scope (Thestill Editorial tokens) |
| C4 | `components/public/*` | SiteHeader, SiteFooter, SignInCard, QuoteCard, FilterChip, EntityDot, EpisodeRow, PersonRow, ShowTile, FactList, ListenOn, ActivitySparkline, ReaderTabs, Button |
| C5 | `App.tsx`, `PublicApp.tsx`, `layouts/PublicLayout.tsx`, `pages/public/{Landing,Top,Show,Episode,NotFound}.tsx`, `api/public.ts`, `hooks/usePublicApi.ts` | Public route tree and pages |
| C6 | tests | Vitest for AuthContext/App split and pages; no per-user API call fires anonymously |

**Phase D — person and entity pages.**

| # | File | Change |
|---|---|---|
| D1 | `postgres_entity_repository.py`, `sqlite_entity_repository.py`, `entity_repository.py` | Queries: quotes by speaker entity (optionally `about`), quotes about an entity by named speakers, who's talking, topic options, weekly activity; all one row per segment, public podcasts only, hidden entities excluded, read-time gate (≥ 12 words, no unnamed speakers, sponsor terms) |
| D2 | `api_public.py` | `/entities/{type}/{slug}`, `/entities/{type}/{slug}/quotes`, `/podcasts/{s}/episodes/{e}/quotes` |
| D3 | `pages/public/{Person,Entity}.tsx` | Person and entity pages per the brief; `?about=` filter in the URL |
| D4 | shell + sitemap | Entity head tags, entity URLs in the sitemap (≥ 3 quality quotes, else `noindex`) |

**Phase E — legal, polish, end-to-end.** Terms/Privacy pages from
`public/legal/*.md` (labelled drafts); Playwright anonymous spec at phone
and desktop widths that fails on any unmocked private `/api` call;
`docs/web-server.md` section on public pages.

### Alternatives considered

- **Move existing handlers onto `public_router`s (§2 as written).**
  Rejected: the signed-in payloads carry fields that must not be public,
  so every handler would need a second response model and cost-guard
  flags; a separate module makes the boundary structural.
- **Reuse signed-in pages with an anonymous mode (§5 as written).**
  Rejected for the reasons in Clarification 2.
- **Read transcript turns from storage for person quotes.** Rejected for
  now: one storage read per quote per anonymous request; spec #100's
  candidates table is the right home for full-turn quotes.

### Rollback

Every phase is additive. The two columns default to today's behaviour
(`public_pages = true`, `public_hidden = false`) and can stay if the
code is reverted. Turning public pages off without a revert: the public
router and shell injection only mount in multi-user mode, and a
`PUBLIC_PAGES_ENABLED=false` setting disables both.

## Testing

**pytest** (`tests/web/`):

- Route allowlist: iterate `app.routes`; every `/api` route is either in
  the public allowlist or has a `require_auth`/`require_admin`
  dependency. Fails on any new router that is neither.
- Public routes with no credential: 200, `Cache-Control: public …`, no
  `_path` keys; with a cookie, and separately with a bearer token:
  `private`; `Vary: Cookie, Authorization` in all three cases.
- `recent-summaries`: excludes hidden podcasts and `N/A` summaries,
  caps `limit` at 12, ordered by summary time.
- Summary route anonymous: `lang` not in `available_languages` → 404;
  provider factory never called; `N/A` → 404.
- Hidden podcast: API 404, shell 404 + `noindex`, absent from sitemap.
- Head injection: tags present and escaped for episode, podcast, top,
  landing; default + `noindex` for `/inbox`; `HEAD /` is 200.
- Sitemap: counts match the predicate; splits above the threshold.
- Rate limit: 121st anonymous request in a minute is 429; a session
  request is not counted. The shell: 121 distinct unknown slugs → 429;
  121 requests for the same cached episode URL → all 200; a malformed
  slug → 404 with zero repository calls.
- `robots.txt`: a robots parser confirms every public API route and
  public page is allowed and every private path and `/api/inbox` is
  not.

**Vitest**:

- `Landing` renders for anonymous multi-user, redirects when
  authenticated, never renders in single-user mode.
- `EpisodeReader` anonymous: no Transcript tab, citations are text, no
  player, no read-on-view `POST`.
- `ProtectedRoute` carries `next`.

**Playwright**: logged-out episode page renders a summary at phone and
desktop widths; `@live` job fetches a production episode URL with a
Slackbot user agent and asserts the OG tags.

## Open questions

1. **Terms and Privacy copy.** The pages must exist for the login
   promise and for Google's consent-screen review. Who writes the real
   text, and does it mention that summaries are model-generated and may
   be wrong?
2. **Entity snippets.** Phase 2 publishes one transcript segment per
   mention as a quote. Is that an acceptable amount of verbatim publisher
   text, or should public entity pages show counts and episode links
   only?
3. **Orphaned podcasts.** Should a podcast nobody follows any more keep
   its public pages? Default here: yes, while `public_pages` is true.
4. **OG image for episodes.** Publisher artwork is square; previewers
   crop it. Is a generated 1200×630 card (artwork + title + "Summary on
   thestill") worth a small image route in Phase 3?

## Outcome (2026-10-15)

Phases A–E of the implementation plan are built on `feat/94-public-pages`:
the signed-out read API, per-page head tags with 404 + `noindex`,
`robots.txt` and `sitemap.xml`, the public frontend (landing, `/top`,
show, episode, person, entity, terms, privacy) in the Thestill Editorial
design system, sign-in that returns to the page it started from, and the
anonymous e2e contract. Transcripts stay behind sign-in.

### Deviations from the plan

| Planned | Built | Why |
|---------|-------|-----|
| Public quote SQL in the entity repositories (D1) | One shared module, `repositories/public_quotes.py`, run by both backends through `public_quote_queries()` | One SQL text for SQLite and Postgres; the contract test runs it on both |
| Quote gate: ≥ 12 words, skip the last 60 s | ≥ 60 characters, skip the first 90 s, sponsor phrases excluded, one row per segment | Characters are cheap in SQL; the noise on real data was at the start (cold opens, ad reads), not the end |
| Featured quote from the last 90 days only | Last 90 days first, then any age; prefers a sentence that stands alone | Quiet people would otherwise have no featured quote; mid-sentence openers ("He said…") read badly |
| Follow on a public page follows after sign-in | Sign-in returns to the page; the visitor presses Follow again | Auto-follow needs a signed intent token through OAuth; deferred |
| Separate `Person.tsx` / `Entity.tsx`, `layouts/` | One `EntityPage.tsx` (person vs thing by type), `components/public/PublicLayout.tsx`, `ui.tsx` + `helpers.ts` | The two pages share 80 % of their modules |
| Summary route with `allow_generate=False` | Calls only the stored-summary service methods | Same guarantee, no new flag |
| `?q=` quote-specific OG description | Not built | The page lands on and highlights the quote; the share preview uses the episode description |
| Playwright at phone and desktop; `@live` Slackbot OG check | Desktop only; checked phone width by hand (375 px, no horizontal scroll); no `@live` job | Follow-up |

### Added during implementation

- `idx_mentions_episode_segment` on `entity_mentions(episode_id, segment_id)`
  (migration 0015): the person page's quote queries went from 10 s to
  20–70 ms on an 800k-mention copy of production.
- `thestill backfill-summary-previews`: 1991 of 2060 local summaries had
  no stored preview, and the public predicate requires one. **Run it once
  after deploying**, or most episodes stay off the public pages.
- `/api/public/who-said-what` for the landing module.
- `display_name()` strips Wikidata disambiguators ("Amazon (company)").
- Review fixes: one `TtlCache` for every public payload, keyed on a
  fingerprint of the hidden sets, so a takedown run from the CLI reaches
  every cache within the 60 s visibility refresh;
  `stale-while-revalidate` cut from 3600 to 300; guest episodes use the
  full episode predicate; affiliations to hidden entities are dropped; the
  page shell never rate-limits a signed-in user and answers 503 on a
  failed lookup; login clears a stale `oauth_next`; the sitemap counts
  quotes exactly as the page does.

### Verified

- Backend: 884 web/security/contract tests pass;
  `tests/integration/test_public_queries_contract.py` passes on SQLite and
  Postgres (`TEST_DATABASE_URL`).
- Frontend: 640 vitest, `tests/public-pages.spec.ts` 5/5; the rest of the
  hermetic e2e suite passes (the Top Podcasts scroll test flaked once
  under load and passes alone).
- Visual check on a Postgres copy of production data (person, company,
  episode with `?q=`, landing, top, show; desktop and 375 px).
- Multi-speaker segments: 0 of 295k segments have more than one resolved
  speaker, so a quote is never attributed to the wrong person by the
  speaker join.

### Follow-ups

- Data quality seen on the person page: some "Scott" vocatives and Scott
  Bessent mentions are linked to Scott Galloway, so "What others say about"
  shows quotes that are not about him; the topic "Anthropic principle" is a
  mislink of Anthropic. Both are linker issues, not public-page issues.
- Phone-width Playwright run and the `@live` OG check.
- Move repeated inline styles in `pages/public/*` into `public.css`.
- Split `_build_entity_payload` into per-module helpers.
- Terms and Privacy are drafts; open question 1 still stands.
- From the second review (2026-10-15), not built: a compact sample
  briefing and a "Read a sample" action on the landing, before the long
  summary; the positioning line "Keep up with your favourite podcasts in
  minutes. Read the key ideas, check the source, and get your morning
  briefing."; curating launch examples and suppressing unreviewed entity
  links on public pages; Follow intent that survives sign-in.
