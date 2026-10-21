# Server-Rendered Content for Public Pages

> **Status:** 🚧 Phase 0 deployed in v1.28.0 (PR #395, 2026-10-21); 7-day baseline pending. Phases 1–4 (episode, podcast, entity, landing and `/top` pages; decision 3 entity rule; IndexNow) implemented 2026-10-21; production checks and the decision 3 count pending
> **Created:** 2026-10-21
> **Updated:** 2026-10-21
> **Priority:** Medium. Google already indexes the public pages. This spec is for Bing, DuckDuckGo and AI answer engines, which today see an empty page body.
> **Author:** Product & Engineering
> **Related:** [#94 public-pages](94-public-pages.md) (the pages, the head injection, the `/api/public/*` read API and the visibility predicate this builds on), [#95 product-analytics](95-product-analytics.md) (referrer measurement), [#96 prompt-injection-hardening](96-prompt-injection-hardening.md) (summary text is unsanitised LLM output), [#100 quote-ranking-and-featured-quotes](100-quote-ranking-and-featured-quotes.md) (the quote gate), [#102 sign-in-page](102-sign-in-page.md), [#103 content-popularity](103-content-popularity.md) (public view counts), [#104 abuse-prevention-and-user-admin](104-abuse-prevention-and-user-admin.md) (runtime `app_settings` switches), [#25 security-audit-and-hardening](25-security-audit-and-hardening.md) (CSP), [#42 robustness-and-failure-mode-hardening](42-robustness-and-failure-mode-hardening.md) (FM-6 one definition, FM-7 untrusted output). Also related: the separate PR that moves public quote deep links from `?t=` to `#t=`. This spec assumes it has landed.

---

## Executive summary

The public pages from spec #94 are a React SPA. For each URL the server
writes a real `<head>` (title, description, canonical, Open Graph,
JSON-LD), but the `<body>` is only `<div id="root"></div>`. A crawler
that does not run JavaScript sees a title and one sentence. Googlebot runs
JavaScript, and Search Console shows the pages indexed. Bingbot (and
DuckDuckGo, which uses Bing's index) renders JavaScript less reliably.
OAI-SearchBot, PerplexityBot, ClaudeBot and the other answer-engine
crawlers mostly do not run it at all.

This spec puts the page's main content into the HTML the server already
sends:

1. **Semantic HTML inside `#root`.** The server writes headings, the
   summary lead, the first page of attributed quotes, guest and entity
   names, and internal links. It builds them from the same payload
   functions that the `/api/public/*` routes and the head injection use.
   React does not hydrate this HTML. `createRoot` replaces it on mount.
2. **Data handoff, not a second fetch.** The same payloads go into a
   `<script type="application/json">` data block. Before mounting, the
   app puts them into the React Query cache. The first React render then
   shows the same content, with no skeleton and no repeat API calls. CSP
   does not apply to data blocks, so `script-src 'self'` stays as it is.
3. **One response for every user agent.** The shell never reads
   `User-Agent`. Googlebot, Bingbot, GPTBot and a person in Safari get the
   same bytes. A test enforces this, and that is the guardrail against
   cloaking. A **parity rule** (server text must be a subset of what the
   anonymous reader sees) has its own tests.

Full SSR with hydration (a Node renderer next to FastAPI) and bot-only
prerendering are compared and rejected below. This spec adds no SSR
framework.

## Problem and evidence

Measured against production (`https://thestill.me`) on 2026-10-21:

| Measurement | Value |
|---|---|
| Episode page as `bingbot` (`/podcasts/the-product-experience/episodes/rob-ferguson-vp-fireworks-ai`) | 2,842 bytes; `<body>` is the empty `#root`; about 50 words in total, all of them in `<title>`, the meta description and JSON-LD; 0 words of body text |
| Same page as Googlebot or Chrome | Identical bytes (no prerendering for bots, no UA branching) |
| What the same page shows a person after JavaScript runs | Summary lead 159 words; 12 quotes (262 words) out of 53; 45 mentioned entities; a 762-word publisher description; 3 "More from" links |
| Sitemap | 5,767 URLs: 56 podcasts, 866 episodes, 4,843 entities (1,974 person, 1,271 topic, 836 company, 762 product), `/`, `/top` |
| Search Console, 5 days after launch | 34 clicks, 2,495 impressions, 730 URLs with impressions |
| Search Console URL Inspection | Every sampled episode and podcast page: "Submitted and indexed", with rendered content |

What this does and does not mean:

- **Google is not the problem.** Google renders the SPA and indexes the
  summary. Server-rendered body text may let Google index on the first
  pass instead of after the render queue, but this spec expects no
  ranking change from Google and does not count one as a goal.
- **Bing and DuckDuckGo are the open question.** Bingbot renders some
  JavaScript, but not reliably for every URL at scale. Bing Webmaster
  Tools will show what it saw (Phase 0).
- **AI answer engines see nothing useful.** The Vercel and MERJ crawler
  study (December 2024) found that the major AI crawlers fetch JavaScript
  files but do not run them. The exceptions are Gemini, which uses
  Google's infrastructure, and Applebot. User-initiated fetches
  (ChatGPT-User, Claude-User, Perplexity-User), made when someone pastes a
  thestill link into a chat, have the same limit. They read the HTML
  they get.
- **Link previews are mostly covered.** Slack, iMessage and X unfurl from
  Open Graph tags, which spec #94 already serves. The new body text helps
  tools that summarise the fetched page, not the cards.

## Current state

- **Shell.** The catch-all `serve_spa` in
  [app.py:931](../thestill/web/app.py#L931) serves `index.html` for every
  non-API path, GET and HEAD, with `Cache-Control: no-cache,
  must-revalidate`. For public paths it calls
  `ShellRenderer.head_for` ([public_shell.py:167](../thestill/web/public_shell.py#L167)),
  renders `HeadTags` with `render_head`
  ([public_shell.py:119](../thestill/web/public_shell.py#L119)) and
  replaces the `<!--thestill:head-->` marker with `inject`
  ([public_shell.py:147](../thestill/web/public_shell.py#L147)).
- **Head lookups.** `ShellRenderer._podcast_or_episode`
  ([public_shell.py:229](../thestill/web/public_shell.py#L229)) reads the
  podcast or episode row directly. `_entity`
  ([public_shell.py:279](../thestill/web/public_shell.py#L279)) already
  reuses the API's cached `public_entity_payload`
  ([api_public.py:787](../thestill/web/routes/api_public.py#L787)). The
  episode and podcast paths do not reuse the API: they read rows and
  build their own description from `summary_preview`.
- **Guards.** Before any query a slug must match
  `^[a-z0-9][a-z0-9-]{0,110}$`. Hits and misses are cached in a 120 s
  `TtlCache` (4,096 entries) keyed on the visibility fingerprint. A cache
  miss costs an anonymous caller one token from the per-IP `PUBLIC_LIMIT`
  bucket (120 per 60 s,
  [rate_limit.py:90](../thestill/web/middleware/rate_limit.py#L90)); an
  empty bucket gives 429. A failed lookup gives 503 with `Retry-After`.
  Unknown or hidden paths give 404 with `noindex`.
- **Body.** [index.html:16](../thestill/web/frontend/index.html#L16) is an
  empty `#root`. [main.tsx:21](../thestill/web/frontend/src/main.tsx#L21)
  calls `createRoot(...).render(...)`, with no hydration.
  `AuthContext` keeps `isAnonymous` false until `/api/auth/status`
  returns ([AuthContext.tsx:170](../thestill/web/frontend/src/contexts/AuthContext.tsx#L170)),
  and then [App.tsx:65](../thestill/web/frontend/src/App.tsx#L65)
  lazy-loads `PublicApp`, whose pages are lazy chunks too.
- **Public data.** Pages read `/api/public/*` through
  [api/public.ts](../thestill/web/frontend/src/api/public.ts) and
  [hooks/usePublicApi.ts](../thestill/web/frontend/src/hooks/usePublicApi.ts).
  The episode page ([Episode.tsx](../thestill/web/frontend/src/pages/public/Episode.tsx))
  makes four calls: the episode, its summary lead, the first page of
  episode quotes (an infinite query) and the show's episode list (an
  infinite query).
- **CSP.** `script-src 'self' https://www.youtube.com https://s.ytimg.com`,
  with no inline scripts
  ([security_headers.py:52](../thestill/web/middleware/security_headers.py#L52)).
  The JSON-LD `<script type="application/ld+json">` already works under
  it, because browsers do not execute data blocks.
- **Bot visibility.** `classify_user_agent`
  ([user_agent.py:39](../thestill/utils/user_agent.py#L39)) sorts
  requests into `browser`, `bot` and `preview`. It runs only on the
  client-sent `POST /api/public/pageview`, which a crawler that does not
  run JavaScript never sends. **Nothing records a crawler fetching the
  shell**, apart from Caddy's access log.
- **Hosting.** Caddy (`encode gzip`) proxies to uvicorn on one EC2 box.
  There is no CDN, and every cache is in-process.

## Goals

| # | Goal | Verified by |
|---|---|---|
| G1 | A crawler that does not run JavaScript gets the page's main content (h1, summary lead, attributed quotes, people, internal links) in the HTML | `curl -A bingbot` word count per page kind; Bing Webmaster URL Inspection |
| G2 | The server HTML never says anything the anonymous reader cannot see on the same URL (parity) | pytest parity tests; Playwright no-JS vs JS text comparison |
| G3 | Every user agent gets the same response | pytest UA-blindness test |
| G4 | No regression for people: no blank frame, no duplicate content, no worse LCP or CLS, no extra API calls | Playwright handoff test; Lighthouse before and after |
| G5 | A crawl burst cannot degrade the app or Postgres | Concurrency cap with shedding; load test; runtime off switch |
| G6 | We can tell whether it worked | Phase 0 baseline; Bing impressions; AI referrers in PostHog |

## Non-goals

- **No SSR framework migration** (Next.js, React Router framework mode,
  Vite SSR with a Node server). See option B.
- **No user-agent detection** for rendering, and no bot-only snapshots.
  See option C.
- **No new public content.** The full summary, transcripts, quote pages
  past the first, and anything signed-in stay where spec #94 put them.
  This spec only makes the content that is already public readable
  without JavaScript.
- **No changes for signed-in users.** A request with a valid session
  gets today's head-only shell.
- No `llms.txt` and no AI-specific markup. There is no evidence any
  answer engine relies on them.
- No change to which episode and podcast pages are indexable. The
  stricter entity rule (decision 3) is the one exception, and it lands
  before Phase 2.

## Options

### A. Server-written semantic HTML inside `#root`, replaced on mount (recommended)

The shell adds a second marker inside the root:

```html
<div id="root"><!--thestill:body--><!--/thestill:body--></div>
```

For an anonymous request to a public content path, the server swaps the
marker for a fallback and a data block:

```html
<div id="root">
  <div class="public-theme" data-prerendered="episode">
    <header>…site name, Top podcasts, Sign in…</header>
    <main class="ts-container">
      <article>
        <p><a href="/podcasts/the-product-experience">The Product Experience</a> · 14 Oct 2026 · 52 min</p>
        <h1>Rob Ferguson (VP, Fireworks AI)</h1>
        <p>With <a href="/entities/person/rob-ferguson">Rob Ferguson</a> and …</p>
        <section><h2>Summary</h2>…the lead, rendered from Markdown…</section>
        <section><h2>Quotes from this episode</h2>
          <figure><blockquote>…</blockquote>
            <figcaption><a href="/entities/person/…">Name</a>,
              <a href="/podcasts/…/episodes/…#t=1834">30:34</a></figcaption></figure> …
        </section>
        <section><h2>People in this episode</h2>…links…</section>
        <section><h2>From the publisher</h2><p>…first ~80 words…</p></section>
        <section><h2>More from The Product Experience</h2>…3 links…</section>
      </article>
    </main>
    <footer>…Terms, Privacy…</footer>
  </div>
  <script type="application/json" id="thestill-bootstrap">{"v":1,"queries":[…]}</script>
</div>
```

- **One source of data.** The handler bodies of the public routes become
  payload functions: `public_episode_payload`,
  `public_episode_summary_payload`, `public_episode_quotes_payload`,
  `public_podcast_payload` and `public_podcast_episodes_payload`, next to
  the existing `public_entity_payload`. The route, the head tags and the
  fallback all call them (FM-6). The head builder's own episode and
  podcast lookups go away.
- **No hydration.** React never has to reproduce the server markup, so
  there are no hydration mismatches (dates in local time, `matchMedia`,
  `sessionStorage` in `useSignIn`). `createRoot` clears the container on
  its first commit.
- **Handoff.** `main.tsx` reads `#thestill-bootstrap` before mounting. It
  seeds React Query with each payload under the key its hook uses (for
  infinite queries, `{pages: [payload], pageParams: [first]}`) and
  passes an anonymous-viewer hint to `AuthContext`, so `isAnonymous` is
  true from the start and `/api/auth/status` only confirms it. It then
  awaits `PublicApp` and the page chunk before `render()`. The first
  commit is therefore the full page, not a `Suspense` fallback. Without
  this, the fallback would be swapped for a spinner, then a blank frame,
  then skeletons.
- **Looks like the page before JavaScript loads.** The fallback uses the
  same `ts-*` classes inside `.public-theme`, and the stylesheet is
  already in `<head>`. The swap is close to invisible. Text parity is the
  hard rule; visual parity is best effort.
- **Signed-in requests get nothing new.** When `has_valid_session` is
  true, the marker stays empty, as today. Signed-in users never see the
  public tree, and their shell stays the same.

Effort: about two weeks across the phases. Most of it is the payload
refactor, a small HTML builder, and tests. No new process, no new
dependency except an explicit pin on `markdown-it-py`, which is already
in the lock through `rich`.

### B. Full SSR with hydration

Render the React public tree on the server (`renderToString` or
`renderToPipeableStream` from a Vite SSR build) and hydrate it in the
browser.

- **Needs a JavaScript runtime.** The backend is Python. That means a
  Node sidecar container that FastAPI proxies public paths to (or that
  sits in front of FastAPI). It also means a second deploy surface and a
  second health check and log stream, plus memory on the one EC2 box.
  Embedding a JS engine in Python is not a serious option for React 19
  with lazy chunks.
- **Every public component must be SSR-safe.** `window`, `matchMedia`,
  `sessionStorage`, `scrollIntoView` effects and locale-dependent date
  formatting each become a hydration-mismatch bug. Data loading moves to
  route loaders with React Query dehydrate and hydrate. The state still
  goes through a JSON data block, because CSP forbids the usual inline
  `window.__STATE__` script.
- **Benefit.** Exact parity by construction, and the best first paint.

Rejected for now. Too much new infrastructure to fix a gap that option A
closes. Reconsider if the public site becomes the main product surface,
or if the frontend moves to a framework that brings SSR anyway.

### C. Bot-only prerendering (dynamic rendering)

Detect crawler user agents and serve them a snapshot from headless
Chromium (self-hosted Playwright or Rendertron, or a paid service such as
Prerender.io).

- Google's documentation calls dynamic rendering "a workaround and not a
  long-term solution" and recommends server-side rendering, static
  rendering or hydration instead
  ([Search Central](https://developers.google.com/search/docs/crawling-indexing/javascript/dynamic-rendering)).
  Different content for bots and people is cloaking. Even with honest
  snapshots, a stale snapshot or a failed render can look like it.
- AI crawlers change user agents often, and new ones appear every month.
  A UA allowlist is never complete, and one crawler that misses it gets
  the empty shell.
- Headless Chromium needs about 300–500 MB per instance on a small box.
  A paid service sends every page to a third party and adds a vendor.
- Snapshots go stale when a summary is re-written or a podcast is hidden.
  That could make a takedown lag.

Rejected.

### D. Others, briefly

- **`<noscript>` content.** It is ignored or given little weight by
  search engines and is a known spam pattern. People with JavaScript
  never see it, so parity is unverifiable. Rejected.
- **Periodic static snapshots served to everyone.** Render the sitemap
  URLs with headless Chromium on a schedule. This is not cloaking, but it
  costs 5,767 renders per cycle, adds staleness and the same Chromium
  infrastructure as C. Rejected.

### Comparison

| | A. Fallback + handoff | B. SSR + hydrate | C. Dynamic rendering |
|---|---|---|---|
| Effort | ~2 weeks | Weeks, plus a new service | ~1 week, plus a service or vendor |
| Content drift risk | Medium; tests enforce parity | None | Low content drift, high staleness drift |
| Cloaking risk | None (UA-blind) | None | Inherent |
| Ops | None new | Node sidecar | Chromium or vendor |
| Per-request cost | Payload builds (cached) + string rendering | Node render | Chromium render (cached) |
| CSP | Unchanged (data block) | Unchanged if state uses a data block | Unchanged |
| First paint for people | Better (content in HTML, no API round trips) | Best | Unchanged |

**Recommendation: A.** It gives a crawler the same text a person reads.
It needs no new process and changes no CSP. It removes four API round
trips from every anonymous page load. Its weakness, keeping two
renderings of the same data in step, is limited by building both from
one payload and by the parity tests below.

## Design

### 1. Content budget per page kind

The rule is simple. **The fallback holds at most what an anonymous reader
sees on that URL without clicking anything.** It is often less. It never
holds anything that sits behind sign-in, a click or a later page.

| Page | In the fallback | Out |
|---|---|---|
| Episode | Show link, date, duration; h1; hosts and guests (links); Listen-on links; the summary lead (gist and key takeaways, exactly as `_summary_lead` cuts it) and the "Also:" titles of the held-back sections; the first 6 quotes (decision 2; each with speaker link and a `#t=` episode link); mentioned entities by type with counts; the first ~80 words of the publisher description; 3 "More from" links | Full summary; transcript; quote pages after the first; the speaker filter; the linked-quote card (a fragment the server never sees) |
| Podcast | h1, author, category; the publisher description, clipped as on the page; hosts and recurring guests (links); Listen-on links; the first 10 summarised episodes with their one-line `summary_preview` (links) | Later episode pages; Follow (a sign-in link only) |
| Entity | h1, headline or description; the featured quote; Who's talking or Topics; the first quote page per stream shown on load; Appears on and Guest on lists; Most discussed on; Related; the Wikidata facts shown in the rail | Quote pages after the first; the `?about=` filtered views |
| Landing | Masthead copy; the latest summary lead; Who said what this week (up to 3 quotes); Fresh this week (6 links) | Nothing extra |
| Top | h1; the chart rows for the resolved region (rank, name, artist, link when the show is public) | Other regions and categories |

**Decision: summaries and quotes yes, transcripts no.** This keeps the
spec #94 line between "a document thestill wrote, plus short attributed
excerpts" and "the publisher's show in text form":

- **Summary lead: in.** It is thestill's own writing and the main reason
  anyone should land on the page.
- **Quotes: in, first page only.** These are short verbatim excerpts with
  named speakers, timestamps and links back to the episode and to the
  publisher's platforms. They are already public through
  `/api/public/.../quotes` and allowed in `robots.txt`. The fallback
  makes them readable without JavaScript but does not make more of them
  public. Six excerpts of about 20–40 words, from an episode that runs
  close to an hour, are the kind of short quoting that keeps relations
  with podcasters workable. All 53 would not be. To keep the parity rule
  without a flash, the public episode page's first quote page also drops
  from 12 to 6 for everyone (the rest stay behind "More quotes"), so the
  fallback, the bootstrap and the page agree. This is the one visible
  change for readers.
- **Publisher description: clipped.** Show notes are often long (762
  words in the sample) and appear word for word on Apple, Spotify and
  the publisher's site. The full text adds duplicate content and no
  value. The page shows a five-line clamp. The fallback shows the first
  ~80 words as plain text, which is a prefix of what the reader sees.
- **Transcripts: out.** Unchanged from spec #94. Putting them in HTML
  would hand the whole show to every scraper and training crawler, and
  a podcaster would reasonably object.
- **Takedown applies the same way.** Every fallback is built through
  `PublicVisibility`. A hidden podcast or entity disappears from the
  fallback within the same 60 s visibility refresh as from the API.

On the sample episode this budget gives roughly 600–700 words of body
text, where today there are none.

### 2. Parity rule (cloaking guardrail)

1. **UA-blind.** The shell never reads `User-Agent`, and no code path
   that builds the response may branch on it. Logging (§5) reads the
   user agent after the response is built.
2. **Subset.** Every text node in the fallback, after whitespace is
   normalised, comes from a payload string the page renders, or from a
   short allowlist of shared UI strings (section headings, "With",
   "Read the full summary", "Sign in", footer links). The test
   allowlist is a constant in the fallback module, so a new string fails
   the test until someone adds it on purpose.
3. **Key blocks.** For each page kind, the h1, the summary lead and the
   first quote must be present when the page shows them. This keeps the
   fallback from becoming so thin that it misrepresents the page in the
   other direction.
4. **Query strings.** A URL with any query parameter (`?speaker=`,
   `?view=transcript`, `?about=`, `?region=`) gets only the header block
   (h1 and the byline). The canonical URL has no query string, so
   nothing of value is lost, and the subset rule holds for every filtered
   view. `#t=` fragments never reach the server, so they need no rule.
5. **Same lifecycle as the API.** The fallback reads the same
   `TtlCache` entries as the matching API routes. The bootstrap and the
   fallback come from the same payload object in the same request, so
   the first React render cannot disagree with the fallback.

### 3. Rendering and safety

- New module `thestill/web/public_prerender.py`. For each page kind,
  one function turns the payloads into an HTML string with a small
  element helper that escapes every text and attribute value with
  `html.escape`, like `render_head`. No Jinja2: it is in the `web` extra,
  which the production image does not install, and a template language
  is more than five small builders need.
- **Markdown.** The summary lead is rendered with `markdown-it-py`, which
  becomes an explicit dependency. Use the `commonmark` preset with tables
  and strikethrough enabled (matching `remark-gfm` in `SummaryViewer`),
  and `html: False`, because summary text is unsanitised LLM output
  (spec #96, FM-7). Links pass a scheme allowlist (`https`, `http`,
  site-relative). Anything else, such as `javascript:` or `data:`, is
  rendered as text.
- **Internal links** use canonical paths only: `/podcasts/<slug>`,
  `/podcasts/<slug>/episodes/<slug>`, `/entities/<type>/<slug>`, and
  `#t=<seconds>` for quotes. No `?t=` and no `?page=`, so the fallback
  creates no new URL variants for Google to treat as separate canonicals.
  External links (Listen on, website) get `rel="noopener"`.
- **Bootstrap block.** The JSON uses the same escaping as
  `json_ld_script` (`<`, `>`, `&`, U+2028 and U+2029), and the same
  helper serialises both. Its top-level `v` is a format version, so a
  stale cached page that meets a new bundle is ignored, not misread.
- **Shared query keys.** A key factory (`publicKeys.episode(p, e)` and
  similar) moves into `api/public.ts`. The hooks and the bootstrap seeder
  both use it, and a Vitest checks that each bootstrap entry seeds the
  key its hook reads.
- **Failure handling.** If building the fallback raises, log
  `public_prerender.failed` at warning and serve the head-only shell with
  a 200, which is what everyone gets today. The page still works. A
  lookup that fails before the head is built keeps today's 503.

### 4. Performance and crawl protection

**Cost per uncached page.** Episode: about 8 repository calls (episode by
slug, podcast anchors, episode anchors, entity refs, platform links, the
episode quote set, episode entities, the episode list) plus one summary
read from storage (S3 in production). Today a browser makes most of these
calls anyway through four API requests. A JavaScript-rendering crawler
also made them. With the handoff, both make one request instead of five.
A crawler that does not run JavaScript goes from one head lookup to the
full set. Entity pages cost more (the quote queries measured at 20–70 ms
in spec #94) and already have a 120 s payload cache.

**Caching.**

- Cache payloads, not HTML. Rendering from payloads takes a few
  milliseconds. An HTML cache for 5,767 pages at ~45 KB each would be
  ~260 MB per process.
- The new episode and podcast payload caches use the existing `TtlCache`.
  They are keyed on the visibility fingerprint like the others, with a
  120 s TTL and a bound of 1,024 entries each. One cache per payload
  serves the API, the head tags and the fallback.
- The shell keeps `Cache-Control: no-cache, must-revalidate`. It now
  varies by session, so it also sends `Vary: Cookie, Authorization`. No
  shared cache sits in front today, but this keeps a future CDN honest.

**Response size.** Budget: ≤ 60 KB raw and ≤ 15 KB gzipped for an
episode page (fallback plus bootstrap). Caddy already gzips. The sample
episode's four payloads come to ~25 KB of JSON.

**Crawl spikes.**

- The per-IP `PUBLIC_LIMIT` bucket stays as it is and is charged on
  cache misses. Bing and the AI crawlers fetch from many addresses, so a
  per-IP limit does not cap total load.
- **Global cap.** A process-wide semaphore,
  `PRERENDER_MAX_CONCURRENT` (default 4), covers fallback builds that
  miss the cache. The acquire never waits. When the cap is reached, the
  request gets the head-only shell with a 200 and
  `public_prerender.shed` is logged. This sheds on load, not on who is
  asking, so it is not cloaking: anyone may get the head-only shell
  under load. The shell is a sync route in the threadpool, so the cap
  also stops a crawl burst from starving the API's threads.
- **Off switch.** A new runtime key, `public_prerender_enabled` (bool,
  default true), goes in the spec #104 `app_settings` table, with
  `PUBLIC_PRERENDER_ENABLED` as the environment default. An operator can
  turn the fallback off during an incident with `thestill settings`,
  without a deploy.
- `Crawl-delay` in `robots.txt` (Bing honours it, Google ignores it) and
  Bing Webmaster's crawl control are kept in reserve. Neither is set
  unless Phase 0 or 1 numbers call for it.

### 5. Measurement

**Shell log line (Phase 0).** Each shell response on a public content
path logs:

```text
public_shell.served  kind=episode|podcast|entity|landing|top  status=200|404|429|503
                     ua_family=googlebot|bingbot|oai-searchbot|chatgpt-user|gptbot|claudebot|
                               claude-user|perplexitybot|perplexity-user|ccbot|applebot|
                               duckduckbot|other-bot|preview|browser
                     prerendered=true|false|shed  cache=hit|miss  ms=…
```

`ua_family` is a finer classifier next to `classify_user_agent`, used for
logging only (§2.1). The line carries no IP and no full user agent. Two
saved CloudWatch queries go into
[docs/logging-cloudwatch-queries.md](../docs/logging-cloudwatch-queries.md):
shell fetches per `ua_family` per day, and `prerendered` and `shed`
rates.

**Outcomes.**

| Signal | Source | Expectation |
|---|---|---|
| Bing impressions and clicks; indexed page count | Bing Webmaster Tools (Search Performance, Index) | Up from the Phase 0 baseline within 4–6 weeks |
| Crawler fetches per family | `public_shell.served` | Answer-engine crawlers appear and keep crawling once pages have content |
| Referrals from answer engines | PostHog `public_page_view.referrer_host` (spec #95): `chatgpt.com`, `perplexity.ai`, `claude.ai`, `copilot.microsoft.com`, `bing.com`, `duckduckgo.com`, `gemini.google.com`; plus `utm_source=chatgpt.com`, which ChatGPT adds to its links | A saved PostHog insight, "Search & AI referrals", grouped by host. Any non-zero AI series is new |
| Google | Search Console | No change expected. A drop is a regression to investigate |
| Reader experience | Lighthouse on three sample URLs; Playwright handoff test | LCP no worse, CLS ≤ 0.1, no blank frame |

## Phases

| Phase | Scope | Ships alone? |
|---|---|---|
| 0 | Baseline: `public_shell.served` log line and `ua_family`; CloudWatch queries; `robots.txt` blocks AI training crawlers (decision 1); sitemap submitted in Bing Webmaster Tools (already verified, decision 4); a "Search & AI referrals" PostHog insight; the measured word count and Bing numbers recorded in this spec | Yes, no behaviour change |
| 1 | Episode pages: payload functions out of the episode, summary, quotes and episode-list handlers; `public_prerender.py` with the episode builder; body marker in `index.html`; bootstrap block; `main.tsx` handoff (seed, viewer hint, awaited chunks); global cap, shedding and runtime switch; `markdown-it-py` pin; `Vary` on the shell | Yes |
| 2 | Podcast and entity pages: `public_podcast_payload` and episode list builder; entity builder on `public_entity_payload` and the first quote pages | Yes |
| 3 | Landing and `/top`: builders over `recent-summaries`, `who-said-what` and the top chart (region resolved as the API does, so the fallback matches what that visitor's chart shows) | Yes |
| 4 (optional) | IndexNow: a key file at the root and a ping to `api.indexnow.org` when a summary is first written or a podcast's visibility changes; Bing and others pick up new episodes in minutes instead of at the next sitemap crawl | Yes |

### Acceptance criteria

#### Phase 0

- `public_shell.served` appears for every public content path with the
  right `ua_family` for fixture user agents of each family (unit test).
- Bing Webmaster Tools shows the site verified and the sitemap
  processed. A dated baseline for impressions, clicks, indexed pages and
  crawler fetches per family, after 7 days, is added to this spec.

#### Phase 1 (episode pages)

- `curl -s -A 'Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)' <episode URL>`
  piped through a tag stripper (a `scripts/` one-liner documented in
  `docs/web-server.md`) shows at least 400 words of body text on 5
  sampled episodes with a summary and 3 or more quotes. Today the same
  pipeline shows 0.
- The same command with a Chrome user agent and with no user agent
  returns identical bytes (pytest asserts the same in-process).
- Bing Webmaster URL Inspection, "Live URL", shows the summary lead and
  quotes on 3 sampled episodes.
- Search Console URL Inspection, "Test live URL" then "View tested page",
  shows the rendered HTML with **one** copy of the content: React
  replaced the fallback and did not render next to it.
- Response ≤ 60 KB raw and ≤ 15 KB gzipped. Uncached p95 shell time on
  production ≤ 250 ms, from the `ms` field.
- Playwright: no blank frame and no duplicate h1 during the handoff; no
  `/api/public/*` request for the four seeded queries on first load;
  CLS ≤ 0.1.
- All tests in the Testing section pass. The existing
  `test_public_shell.py` and `public-pages.spec.ts` suites pass
  unchanged, apart from the shared-builder refactor.

#### Phase 2 (podcast and entity pages)

- Same `curl` check on 5 podcasts and 10 entities with `indexable` true:
  podcasts ≥ 200 words, entities ≥ 150 words.
- A podcast hidden with `thestill set-podcast-visibility --private`
  disappears from its episodes' fallbacks and from its entities' quote
  lists within 60 s (integration test with the fingerprint refresh).

#### Phase 3 (landing and top)

- Landing fallback shows the latest summary lead and 6 Fresh links. The
  `/top` fallback shows the chart rows the API returns for the same
  request's resolved region.

## Testing

**pytest** (`tests/integration/web/test_public_prerender.py`, new, and
additions to `test_public_shell.py`):

- **UA-blindness.** For one fixture of each page kind, the response
  bodies for Googlebot, Bingbot, GPTBot, Chrome and no user agent are
  byte-identical.
- **Subset parity.** Parse the fallback. Every normalised text node
  appears among the strings of the bootstrap payloads, or in the
  allowlist constant.
- **Key blocks.** The h1, the summary lead's first sentence and the first
  quote are present for the episode fixture.
- **Budget.** With a fixture summary that has Timeline and Drama
  sections, neither section's text appears in the fallback or the
  bootstrap. At most 6 quotes. The description is clipped. No transcript
  text other than quote excerpts appears.
- **Query strings.** `?speaker=x`, `?view=transcript` and `?about=y`
  render only the header block.
- **Escaping.** A podcast title of `</h1><script>alert(1)</script>`, a
  summary containing raw `<img onerror>` HTML and `[x](javascript:…)`,
  and a quote containing `</script>` all come out inert. The bootstrap
  JSON parses back to the payload.
- **Links.** Every `href` in the fallback is a canonical internal path,
  a `#t=` quote link or an allowlisted external scheme. No `?t=`.
- **Session.** With a valid session cookie or bearer token, the marker is
  empty and the response carries `Vary: Cookie, Authorization`.
- **Visibility.** A hidden podcast gives 404 with an empty marker. An
  episode with an `N/A` summary gives 404.
- **Shedding.** With the semaphore held, the response is 200 with an
  empty marker, and `public_prerender.shed` is logged (asserted with a
  stubbed logger, not structlog's `capture_logs`).
- **Switch.** `public_prerender_enabled = false` in `app_settings`
  empties the marker without a restart.
- **Failure.** A builder that raises gives the head-only shell with a 200.

**Vitest:**

- The bootstrap seeder puts each entry under the key its hook reads,
  including the infinite-query shape. A `v` mismatch is ignored. A
  missing or malformed block falls back to today's fetches.
- `AuthContext` with the anonymous hint is `isAnonymous` on its first
  render and reconciles when `/api/auth/status` says otherwise.

**Playwright** (`tests/public-prerender.spec.ts`, new, on a private
`PLAYWRIGHT_BASE_URL` port):

- **No-JS vs JS text.** Open each fixture page in a context with
  `javaScriptEnabled: false` and collect `main`'s text. Open it normally,
  wait for idle, and collect the visible text. Every normalised sentence
  of the first must appear in the second.
- **Handoff.** A `MutationObserver` registered before the bundle runs
  records that `#root` always contains an `h1` and never two.
- **No refetch.** No request to the four seeded `/api/public/*` paths
  happens during first load.
- Phone (375 px) and desktop widths.

**`@live` (manual or scheduled):** the `curl -A bingbot` word count
against three production URLs, failing below the Phase 1 threshold.

## Risks and negative consequences

| Risk | Effect | Mitigation |
|---|---|---|
| Fallback drifts from the React page | Looks like cloaking; misleading snippets | One payload for both; UA-blind; subset and key-block tests; Playwright text comparison |
| More of the corpus is easy to scrape and train on | Quotes and summaries in plain HTML reach crawlers that skip JSON APIs | Budget keeps transcripts and later quote pages out; `robots.txt` blocks training crawlers (decision 1); takedown flags apply |
| Thin or wrong entity pages become more visible | 4,843 of 5,767 URLs are entities, and data quality varies. For example, `/entities/person/altruist` is the custodian firm Altruist typed as a person, with a painting as its image from the Wikidata link. Bing may judge the site by its weakest pages | Entity fallback ships in Phase 2, after episode pages show results, and after the stricter `indexable` rule (decision 3) |
| Crawl bursts | CPU, Postgres and storage reads; threadpool starvation | Payload caches; global cap with shedding; runtime switch; per-IP bucket unchanged |
| Handoff regressions | Blank frame, double content, layout shift for people | Awaited chunks; Playwright handoff test; CLS budget |
| A stale cached HTML meets a new bundle | Seeder misreads the payload | `v` version field; seeder ignores unknown versions and refetches |
| XSS through feed metadata or summary text | Script injection on a public page | Escape everything; Markdown `html: False`; scheme allowlist; fixtures |
| Two renderings to maintain | Each new public module needs a fallback decision | A short "fallback checklist" in `docs/web-server.md`; the allowlist constant makes new UI strings a deliberate change |
| Google behaviour changes | Possible, not expected | Watch Search Console; Phase 1 alone is easy to turn off |

## Rollback

Every phase is additive. Setting `public_prerender_enabled` to false in
`app_settings`, or `PUBLIC_PRERENDER_ENABLED=false`, empties the marker,
and the site behaves exactly as it does today. The payload refactor
changes no API response shape, and the existing API tests enforce that.
An old bundle meeting a new shell ignores the marker content after its
first commit and fetches as today. A new bundle meeting an old shell
finds no bootstrap block and fetches as today.

## Implementation notes

### Phase 1 (2026-10-21)

- Payload functions live in `api_public.py` (`public_podcast_payload`,
  `public_podcast_episodes_payload`, `public_episode_payload`,
  `public_episode_summary_payload`, `public_episode_quotes_payload`, plus
  `public_episode_models` for the slug lookup), each with its own 120 s
  `TtlCache` of 1,024 entries. The route and the fallback call them. The
  head tags use only the cached slug lookups they share
  (`public_episode_models`, `public_podcast_row`), so a request shed by
  the cap costs one lookup, not a payload build. An entity page's head
  needs the full entity payload (its quote counts decide `noindex`), so
  an uncached one is built under the same cap, and over it the page
  answers 503 with `Retry-After: 120` rather than guessing `noindex`;
  with the switch off the cap does not apply.
- `TtlCache` gained a cache-only mode (`public_access.cache_only`): the
  shell first builds from cache and only an uncached build takes one of the
  `PRERENDER_MAX_CONCURRENT` slots, so a cached page is never shed.
- The data block names a `publicKeys` factory and its arguments
  (`{"query": "episodeQuotes", "args": [...], "data": ..., "page_param": 1}`)
  instead of carrying React Query keys, so each key has one definition, in
  `api/public.ts`.
- `React.lazy` suspends on its first render even for a loaded chunk, which
  would flash a blank frame over the server HTML. The public pages, the
  public app and `SummaryViewer` are now `preloadable()` components that
  `main.tsx` preloads before `createRoot`.
- `public.css` moved into the entry stylesheet, so the fallback is styled
  before any JavaScript runs (it was a lazy chunk's CSS).
- The episode page's "Summary" tab label stands in for a heading; there is
  no visually hidden text in the fallback.
- Measured locally on 5 random episodes with 3 or more quotes (in-process
  app against the dev database): 579–731 words of body text, 27–35 KB raw,
  6–9 KB gzipped, 114–171 ms uncached and 3–7 ms cached, identical bytes
  for Bingbot and Chrome.

### Phase 2 (2026-10-21)

- Podcast and entity builders on `public_podcast_payload`,
  `public_entity_payload` and the new cached
  `public_entity_quotes_payload` (first page of each stream the page
  loads). Entity quote streams keep the page's "two per episode" fold;
  the rest stays behind "+N more from this episode".
- Decision 3 is `public_access.entity_is_indexable` over an
  `EntityIndexFacts` record. The page builds the record from its payload;
  the sitemap from one SQL pass (`PublicQuoteQueries.entity_index_facts`,
  which replaced `indexable_entities`) plus roles from public episodes'
  guests and public shows' anchors; a role holder with no resolved
  mention (so absent from that SQL) is a candidate too, judged by the
  same rule and checked for URL ownership like a re-typed row. Thresholds
  are module constants
  (`INDEX_MIN_SAID` 5, `INDEX_MIN_ABOUT` 10, `INDEX_WIDE_EPISODES` 2,
  `INDEX_WIDE_QUOTES` 5).
- The type test applies only when Wikidata's `instance of` is known:
  locally 1,568 person entities with a QID have none fetched yet, and
  failing them would drop pages for want of data, not because they are
  wrong.
- The manual list is the runtime setting `entity_noindex`
  (`thestill settings set entity_noindex '["person:altruist"]'`).
- Local dev database (71 public episodes, so a small sample): 348 entity
  URLs under the old rule, 210 under the new one (60%; persons 181 → 130,
  companies 101 → 55, products 35 → 14, topics 31 → 11). The type test
  removed 11, among them `person:americans`, `person:child`,
  `person:muse-band` and `product:claude-ai`. **The production count by
  type (open question 3) is still to be checked**; after deploy,
  `curl -s https://thestill.me/sitemap.xml | grep -o '/entities/[a-z]*/' | sort | uniq -c`
  gives it.
- Local measurements: 10 indexable entity pages 180–673 words (bar 150),
  174–565 ms uncached (the existing entity payload cost), 2–7 ms cached;
  5 podcast pages 197–435 words (bar 200; the local shows have few
  summarised episodes, production shows list 10).

### Phase 3 (2026-10-21)

- Landing and `/top` builders over `public_recent_summaries_payload`,
  `public_who_said_what_payload` and the new cached
  `public_top_chart_payload` (300 s). The shell resolves the chart region
  as the API does (the visitor's country by IP, else the US) through
  `shell_visitor_country`, which reads the same per-IP cache; an uncached
  IP counts as an uncached build, so the lookup sits under the cap.
- `/top` leaves out the standfirst's first sentence: the page names the
  country in the browser's locale ("for United Kingdom"), which the
  server cannot reproduce.
- Local: landing 667 words, 18 KB raw, 221 ms uncached; `/top` 1,332
  words, 74 KB raw and 16.5 KB gzipped for 100 chart rows (the data block
  carries the API payload, artwork and Apple links included), over the
  episode-page budget, which was set for episode pages.

### Phase 4 (2026-10-21)

- `thestill/services/indexnow.py`: off unless `INDEXNOW_KEY` is set (and
  the install serves public pages); the key is served at `/<key>.txt`.
- Pings: the summarize task, on an episode's first publish with a public
  page, sends the episode and its show (on a background thread; a failure
  is logged, never raised); `thestill set-podcast-visibility` sends the
  show and its public episodes, read before a takedown and after a
  restore, and prints the HTTP status.
- Not done: pings for re-summaries (the URL is unchanged) and for entity
  pages (they change with every episode; the sitemap covers them).

### End-to-end check (2026-10-21)

A scratch server on the branch code and the local dev data (no task
worker), driven by Playwright:

- The `@live` test (`PRERENDER_SAMPLE_PATHS`) passed on 8 pages across all
  five kinds: every server-written text node of more than 20 characters
  appears in the page React renders. It compares text nodes, leaves
  `<time>` out (the browser formats dates in the reader's locale) and
  splits on `·` (the page renders separators as their own spans).
- On the landing page, an episode, a podcast, a person, a company and
  `/top`: one `h1` in `#root` at every DOM change after parsing, and no
  `/api/public` data request during the first load.

## Decisions

Decided by the product owner on 2026-10-21.

1. **AI training crawlers: blocked; search and user-fetch agents stay
   allowed.** Ships in Phase 0 as a `robots.txt` change. Disallow `/` for
   GPTBot, ClaudeBot, CCBot, Google-Extended, Applebot-Extended,
   Meta-ExternalAgent and Bytespider. Keep Bingbot, OAI-SearchBot,
   ChatGPT-User, PerplexityBot, Perplexity-User, Claude-SearchBot and
   Claude-User under the existing `User-agent: *` rules. Training
   crawlers send no visitors, and the quotes are the publishers' words.
   A test asserts each name appears in the right group.
2. **Quote count: 6.** It halves the verbatim text in the HTML. The
   public episode page's first quote page drops to 6 for everyone so the
   fallback, the bootstrap and the page agree (see the content budget).
   The cost is about 130 fewer words per episode fallback (an estimated
   470–570 words on the sample instead of 600–700), still above the
   Phase 1 bar of 400.
3. **Entity `indexable` rule: stricter, before Phase 2.** See below.
   The rule is proposed. The thresholds get confirmed against a
   production count before they ship.
4. **Bing Webmaster Tools: already set up**, owned by the product
   owner's Google account. Phase 0 submits the sitemap there and records
   the baseline.

### Proposed entity rule (decision 3)

Today an entity page is indexable (`index` robots meta and a sitemap
entry) when its said plus about quotes reach 3. A random sample of 120
live entity payloads (2026-10-21) shows that bar passes 98% of the
4,843 entity URLs, so it filters almost nothing. The sample also shows
why a Wikidata link is the wrong test:

- **Guest pages are the most valuable entity pages and mostly lack a
  QID.** 14 of the 17 sampled people with a guest or host role have no
  Wikidata link (Alex Kendall, Andrew McAfee, Jay Parikh, David
  Krakauer…). Search Console already shows guest-name queries such as
  "dhaval adjodah" landing on thestill. Requiring a QID would drop them.
- **Wrong-type pages often have a QID.** `person/adolescence`,
  `person/person` ("Individual being", 19 episodes),
  `person/nancy-grace-roman-space-telescope` and
  `person/harry-flashman` (fictional) all link to Wikidata and all pass
  the quote bar.
- **The thin tail is generic topics.** Most pages at 3–4 quotes are
  topics such as commerce, healthy-diet or public-relations.

Proposed rule: a page is indexable when it passes the **value** test
and the **type** test.

- **Value** (any one):
  - the entity speaks: 5 or more quotes said, or a guest or host role;
  - it is discussed in depth: 10 or more quotes about it;
  - it is discussed widely: 2 or more episodes and 5 or more quotes in
    total.
- **Type**: a person page whose entity has a Wikidata QID must have
  `wikidata_instance_of` containing Q5 (human). A topic, company or
  product page whose entity is Q5 fails the same way. A manual
  `noindex` list covers what the rule misses.

On the sample the value test keeps 77 of 120 pages (64%), so about
3,100 of today's 4,843 entity URLs. Every kept guest page stays. The
dropped pages are mostly 3–4-quote generic topics and one-episode
mentions. Non-indexable pages stay reachable by link but get
`noindex` and leave the sitemap.

The sitemap and the page must use the same predicate. The sample found
3 URLs (2.5%, so about 120 site-wide) listed in the sitemap whose own
page reports 0 quotes and `indexable: false`
(`topic/islam`, `topic/theoretical-physics`, `product/medication`).
`indexable_entities_sql` and `_build_entity_payload` count differently.
One function should decide both (FM-6, one definition).

## Open questions

1. **Snippet controls.** Should quotes carry `data-nosnippet`, or pages a
   `max-snippet` limit, so search results and AI answers cite the page
   but quote less of the publisher's text? Bing also documents
   `noarchive` and `nocache` values that limit how Copilot uses a page.
   This needs checking against Bing's current documentation before
   relying on it. Default: no controls in Phase 1. Measure first.
2. **IndexNow.** Adopt in Phase 4? It is cheap and Bing-specific (also
   Yandex, Seznam, Naver). It needs a key file at the root and one
   outbound call per new summary.
3. **Entity rule thresholds.** Confirm the decision 3 numbers against a
   production count by type before shipping.
