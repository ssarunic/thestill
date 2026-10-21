# Web Server

FastAPI-based web server with REST API, React frontend, and webhook handlers.

## Starting the Server

```bash
thestill server                    # Start on localhost:8000
thestill server --host 0.0.0.0     # Expose to network
thestill server --port 8080        # Custom port
thestill server --reload           # Development mode with auto-reload
thestill server --workers 4        # Multiple worker processes
```

## API Endpoints

### Health & Status

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Serves the React SPA (`index.html` via the catch-all route) |
| `/health` | GET | Liveness check for load balancers (`{"status": "healthy"}` envelope; no dependency checks) |
| `/health/ready` | GET | Readiness probe (spec #66): one cheap DB round-trip; `200`/`"ready"` or `503`/`"unready"` |
| `/api/status` | GET | Detailed system statistics (same data as the CLI `status` command) |
| `/api/status/mcp` | GET | Whether the remote MCP endpoint is enabled (spec #78); admin-gated |
| `/docs` | GET | OpenAPI docs — only when `ENVIRONMENT=development` or `ENABLE_DOCS=true`; disabled in production |

Any path that doesn't match an API route (including bare `/status`) falls
through to the SPA catch-all and returns `index.html`.

### Podcasts (`/api/podcasts`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/podcasts` | GET | List followed podcasts (paginated; `?q=` filters by title/author) |
| `/api/podcasts/resolve` | POST | Resolve a podcast URL `{url}` to a local slug, creating the row if needed |
| `/api/podcasts/{slug}` | GET | Get podcast details |
| `/api/podcasts/{slug}/follow` | POST | Follow podcast |
| `/api/podcasts/{slug}/follow` | DELETE | Unfollow podcast |
| `/api/podcasts/{slug}/followers/count` | GET | Follower count for a podcast |

Adding a podcast and triggering a feed refresh are commands, not podcast
routes: `POST /api/commands/add` and `POST /api/commands/refresh` (see
Commands below).

### Episodes (`/api/episodes`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/episodes` | GET | List episodes across all podcasts (filterable, paginated) |
| `/api/episodes/failed` | GET | List episodes with pipeline failures |
| `/api/episodes/bulk/process` | POST | Queue full pipeline processing for multiple episodes |
| `/api/podcasts/{podcast_slug}/episodes` | GET | List episodes (filterable) |
| `/api/podcasts/{podcast_slug}/episodes/{episode_slug}` | GET | Get episode details, plus the caller's `listening` progress (spec #90; `null` when never played) |
| `/api/podcasts/{podcast_slug}/episodes/{episode_slug}/transcript` | GET | Get transcript content |
| `/api/podcasts/{podcast_slug}/episodes/{episode_slug}/summary` | GET | Get summary content |
| `/api/podcasts/{podcast_slug}/episodes/{episode_slug}/transcript/words` | GET | Word-level transcript timings |
| `/api/episodes/{id}/failure` | GET | Get failure details |
| `/api/episodes/{id}/retry` | POST | Clear failure and retry |
| `/api/episodes/{id}/reprocess` | POST | Re-run an episode from a stage onwards (spec #106) |

### Commands (`/api/commands`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/commands/refresh` | POST | Trigger feed refresh (all podcasts or one) |
| `/api/commands/refresh/status` | GET | Status of the last refresh task |
| `/api/commands/status` | GET | Status of all tracked command tasks |
| `/api/commands/add` | POST | Add new podcast `{url}` as a background task |
| `/api/commands/add/status` | GET | Status of the last add-podcast task |
| `/api/commands/download` | POST | Queue audio download for an episode |
| `/api/commands/downsample` | POST | Queue downsampling for an episode |
| `/api/commands/transcribe` | POST | Queue transcription for an episode |
| `/api/commands/clean` | POST | Queue transcript cleaning for an episode |
| `/api/commands/summarize` | POST | Queue summarization for an episode |
| `/api/commands/run-pipeline` | POST | Run full pipeline for episode |
| `/api/commands/episode/{id}/cancel-pipeline` | POST | Cancel remaining pipeline stages for an episode |
| `/api/commands/episode/{id}/tasks` | GET | List tasks for an episode |
| `/api/commands/task/{id}` | GET | Get queued task status |
| `/api/commands/task/{id}/progress` | GET | Stream task progress via Server-Sent Events |
| `/api/commands/task/{id}/progress/current` | GET | Latest progress snapshot for a task |
| `/api/commands/queue/status` | GET | Queue counts by state |
| `/api/commands/queue/tasks` | GET | List queued tasks |
| `/api/commands/queue/task/{id}/bump` | POST | Bump a queued task to the front |
| `/api/commands/queue/task/{id}/cancel` | POST | Cancel a queued task |
| `/api/commands/dlq` | GET | List dead letter queue tasks |
| `/api/commands/dlq/{task_id}/retry` | POST | Retry dead task |
| `/api/commands/dlq/{task_id}/skip` | POST | Skip/resolve dead task |
| `/api/commands/dlq/retry-all` | POST | Retry all dead tasks |

### Briefings (`/api/briefings`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/briefings` | GET | Paginated briefing history for the current user, newest first |
| `/api/briefings/latest` | GET | Current briefing for the current user, plus `previous` (the edition before it) and `upcoming` (next slot, episodes collected so far, episodes still processing). Lazy-generates when no schedule owns generation; a pending cut never hides the current edition |
| `/api/briefings/schedule` | GET | Current user's briefing schedule (spec #50) |
| `/api/briefings/schedule` | PUT | Upsert schedule (frequency, hour, weekday, timezone, enabled) |
| `/api/briefings/{briefing_id}` | GET | Briefing metadata + narration variants |
| `/api/briefings/{briefing_id}/script` | GET | Rendered script markdown |
| `/api/briefings/{briefing_id}/episodes` | GET | Episodes the briefing covers, grouped by podcast, with artwork and gist (the page's index) |
| `/api/briefings/{briefing_id}/narrate` | POST | Generate a narration variant (spec #33) |
| `/api/briefings/{briefing_id}/listened` | POST | Mark briefing listened (also sets `opened_at` if it is still empty) |
| `/api/briefings/{briefing_id}/opened` | POST | Spec #95: record the first time the owner opened the briefing. Idempotent; the first time is kept. Fired by the briefing page once it has loaded, never by a GET |

### Inbox (`/api/inbox`)

Per-user episode deliveries (spec #29). All endpoints operate on the authenticated user's rows.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/inbox` | GET | List inbox items, newest delivery first. Query: `state`, `limit`, `before` (cursor by `delivered_at`), `q` (spec #85: whitespace tokens ANDed, each a case-insensitive substring of episode title, podcast title or description; trimmed, max 200 chars, up to 8 tokens; composes with `state`/`before`). Each item carries the user's `listening` progress (spec #90), `null` when never played |
| `/api/inbox/unread-count` | GET | Lightweight unread count for badge rendering |
| `/api/inbox/arriving` | GET | Spec #88 "Arriving soon": episodes of podcasts you follow that have an active pipeline task, no `published_at`, no failure, and no row in your inbox. Query: `limit` (1–20, default 5). Returns `items` (`episode` + `podcast`), `count` and `total`. Read-only; these are not inbox rows |
| `/api/inbox/{episode_id}` | GET | Spec #88: your row for one episode, `{"entry": …}` or `{"entry": null}` when never delivered |
| `/api/inbox/{episode_id}` | POST | Spec #88 "Send to my inbox": creates an `ad_hoc` row only when none exists and starts the pipeline if the episode is unprocessed. `201` with `{"entry", "created": true}`; `200` with the untouched existing row and `created: false`; `404` when the episode does not exist. Delivery is immutable: an existing row's `delivered_at`, `source` and `state` never change |
| `/api/inbox/{episode_id}/state` | POST | Set row state explicitly. Body: `{"state": "read"\|"saved"\|"dismissed"\|"unread"}`. 404 when no row exists |
| `/api/inbox/{episode_id}/read` | POST | View-driven read tracking: transitions `unread → read` only, never touching `saved`/`dismissed`. Always 200 with `{"marked": bool}`; a missing row is a no-op. Fired by the episode page once a summary is available. Also records the first time the reader opened the episode (spec #103, `user_episode_views`) unless they opted out of usage analytics or sent `Sec-GPC: 1` |

### Dashboard (`/api/dashboard`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/dashboard/stats` | GET | Dashboard statistics |
| `/api/dashboard/activity` | GET | Recent activity feed (paginated) |
| `/api/dashboard/narration` | GET | Aggregated narration runs for the dashboard tile |
| `/api/dashboard/analytics` | GET | Spec #95 Layer A product analytics: signups by source, activation funnel, 12-week cohort retention, engagement, and (spec #103) content: top podcasts, episodes and categories by readers against the previous window. Query: `range` = `7d`/`28d`/`90d` (default `28d`). Aggregates only; admins and the single-user local account excluded; cached 10 min per range. Returns `{"available": false}` in single-user mode or on SQLite |

### Admin console (`/api/admin`, spec #104)

Admin only. Every write goes through the same services as `thestill user` / `admin` / `settings` and lands in the audit log as actor `web`. A refused action (acting on yourself, demoting the last admin, a missing reason) is a 409 `{"detail": {"error", "message"}}`. The user and audit endpoints are 409 `single_user_mode` in single-user mode; settings and alerts work in both.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/admin/users` | GET | 409 `single_user_mode` in single-user mode, like every `users` endpoint. Accounts with follows, feeds brought in (24h / all time), imports, translations and quota hits (24h). Query: `q` (email or name), `status`, `sort` = `attention` (default: quota hits, then feeds brought in, 24h) / `created` / `last-login` / `follows` / `email`, `limit`, `offset` |
| `/api/admin/users/{user_id}` | GET | One account: identity and status (with reason), 24h and 7-day usage per kind with daily counts, follows (brought in, moved aside, other followers), quota limits and overrides, audit history |
| `/api/admin/users/{user_id}/status` | POST | `{status: active / suspended / banned, reason, remove_introduced_feeds?}`; suspend and ban need a reason |
| `/api/admin/users/{user_id}/logout-everywhere` | POST | Revoke every session and the MCP token |
| `/api/admin/users/{user_id}/remove-introduced-feeds` | POST | Drop the user's follow of feeds they brought in that no other active user follows; returns `feeds_removed`, `tasks_cancelled` |
| `/api/admin/users/{user_id}/quotas` | PUT | `{quota: limit}` replaces the user's overrides (0 = unlimited); `{}` clears them |
| `/api/admin/users/{user_id}/admin` | POST | `{is_admin}` grants or revokes the admin role |
| `/api/admin/settings` | GET | Each runtime setting's effective value, `source` (`runtime` / `env`) and default; `non_default`; `alert_channels` |
| `/api/admin/settings/{key}` | PUT | `{value}`, validated per key (422 `invalid_setting`, 404 `unknown_setting`) |
| `/api/admin/settings/{key}` | DELETE | Revert to the env default |
| `/api/admin/alerts/test` | POST | Send a test alert to every channel; per-channel `results` |
| `/api/admin/audit` | GET | The audit log, newest first, with emails for the users it names and the `actions` it can filter by. Query: `action` (422 `unknown_action` for anything else), `target_user_id`, `limit`, `offset`. 409 `single_user_mode` in single-user mode |
| `/api/admin/entities/merge` | POST | `{keeper_id, loser_id, reason?, dry_run?}`: move every reference (mentions, overrides, host / recurring / guest lists, mention candidates) from the loser to the keeper, then delete the loser. Same type only (409 `type_mismatch`) |
| `/api/admin/entities/move-mentions` | POST | `{from_entity_id, to_entity_id, mention_ids, delete_emptied?, reason?}`: point chosen mentions at another entity; only mentions of `from_entity_id` move |
| `/api/admin/entities/split` | POST | `{from_entity_id, name, type, qid?, description?, entity_id?, mention_ids?, delete_emptied?, reason?}`: create an entity and move the chosen mentions (or every reference) to it. 409 `exists`, `qid_taken` |
| `/api/admin/entities/{entity_id}/relink` | POST | `{qid, description?, blacklist_old?, reason?}`: point the entity at another Wikidata item (`qid: null` unlinks); drops the old description, P31 classes and enrichment and blacklists its names for the old QID. 409 `qid_taken`, `unchanged`, `invalid_qid` |
| `/api/admin/entities/blacklist` | POST | `{surface_form, wrong_qid, reason?}`: refuse that link in every future resolution and forget the live linker's cached decision |
| `/api/admin/entities/prune-orphans` | POST | `{apply?}`: count entity rows nothing refers to (`sample` of 50); with `apply: true`, delete them |

### Search (`/api/search`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/search/corpus` | GET | Search the transcript corpus (`?q=`, `mode=lexical\|semantic\|hybrid`) |
| `/api/search/related` | GET | Episodes related to a source episode (`?episode_id=`) |
| `/api/search/quick` | GET | Grouped quick search across podcasts and episodes |

### Entities (`/api`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/episodes/{episode_id}/entities` | GET | Entities mentioned in an episode |
| `/api/entities/{entity_type}/{id_slug}` | GET | Entity page payload — record, aggregates, recent mentions grouped by episode (`recent_mention_counts`) |
| `/api/entities/{entity_type}/{id_slug}/mentions` | GET | Every moment the entity comes up in one episode (`?episode_id=`), in episode order (the entity page's "Show all N mentions") |
| `/api/entities/review-queue` | GET | Entities pending review |
| `/api/entities/corrections` | POST | Submit an entity correction |

### Narrations (`/api/narrations`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/narrations/{id}` | GET | JSON script + Markdown body for a stored narration |
| `/api/narrations/{id}/script.json` | GET | Raw JSON script body for downstream TTS consumers |

### Imports (`/api/imports`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/imports` | POST | Import a single episode by URL `{url}`. Returns the episode + inbox row with `outcome` (`new_episode` / `added_existing` / `already_in_inbox`, spec #88), `episode_slug`, `podcast_slug`, `episode_state` and `episode_failed`; see [imports.md](imports.md) |

### Top Podcasts (`/api/top-podcasts`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/top-podcasts` | GET | Top-podcasts chart (`?region=` ISO code, defaults to the user's region) |

### Public pages (`/api/public`, spec #94)

Signed-out read API behind the public pages. Mounted only when
`MULTI_USER=true` and `PUBLIC_PAGES_ENABLED=true`; no session needed,
per-IP rate limited (`RATE_LIMIT_PUBLIC_MAX`). Every response is built from
an allowlist: no pipeline state, feed URLs, transcripts or per-user flags.
Anonymous responses are `Cache-Control: public, max-age=300,
stale-while-revalidate=300`; a request carrying a credential gets
`private, no-cache`, and `Vary: Cookie, Authorization` is always set.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/public/podcasts/{slug}` | GET | Show metadata, hosts, recurring guests, summarised-episode count |
| `/api/public/podcasts/{slug}/episodes` | GET | Summarised episodes, newest first (`?limit=` ≤50, `?offset=`) |
| `/api/public/podcasts/{slug}/episodes/{episode}` | GET | Episode metadata, hosts/guests, platform links, `has_transcript` |
| `/api/public/podcasts/{slug}/episodes/{episode}/summary` | GET | Stored summary (`?lang=` only if already translated; never calls an LLM) |
| `/api/public/podcasts/{slug}/episodes/{episode}/quotes` | GET | Attributed quotes, 6 per page (spec #105 decision 2); `?q=<seconds>` returns the quote within 5 s as `linked` (the page's URL carries it as `?t=`, the reader's deep-link parameter); `?speaker=<slug>` and `?page=` page through the rest |
| `/api/public/recent-summaries` | GET | Latest summarised episodes across public shows (≤12) |
| `/api/public/top-podcasts` | GET | Apple chart; hidden shows lose their link |
| `/api/public/entities/{type}/{slug}` | GET | Person / company / product / topic page payload (stats, roles, featured quote, who's talking) |
| `/api/public/entities/{type}/{slug}/quotes` | GET | Paged quotes, `?kind=said\|about`, `?about=<type:slug>`, `?page=` |
| `/api/public/who-said-what` | GET | Landing module: the week's most-quoted entity and three quotes from different speakers and shows |
| `/api/public/pageview` | POST | Spec #95: the public site reports each route change as `{path, referrer}` (referrer only on the first view). The server derives the page kind, logs `public_page_view`, and sends browser views (not crawlers or link previews) to PostHog with a daily visitor key. Browser views of a show, episode, entity, top or landing page the public site would serve also go on a per-page daily counter (spec #103, `public_page_daily_views`; no visitor id, IP or referrer). Always 204; signed-in readers and unknown paths are ignored |

**Visibility.** An episode is public when it has a summary with a preview,
both slugs, and its show is public. Hide a show with
`thestill set-podcast-visibility --podcast-id X --private` and an entity with
`thestill set-entity-visibility --entity person:<slug> --hidden`. The
server re-reads the hidden sets every 60 s, and every cached payload
(pages, landing modules, page-shell tags, sitemap) is keyed on them, so the
server stops serving hidden content within about a minute; a browser or
proxy may keep a copy for up to 10 minutes more.
Episodes summarised before spec #94 need
`thestill backfill-summary-previews` once, or they stay off the public pages.

**Page shell.** The SPA catch-all injects per-page `<title>`, description,
canonical, Open Graph and JSON-LD into `index.html`, and answers 404 +
`noindex` for unknown or hidden pages. A person or entity page is indexed
(and listed in the sitemap) when it passes one rule, shared by the page
and the sitemap (spec #105 decision 3, `entity_is_indexable`): it speaks
(5+ quotes said, or a guest or host role), is discussed in depth (10+
quotes about it) or widely (2+ episodes and 5+ quotes), and its Wikidata
type agrees with its page type (a person must be a human, Q5, when
Wikidata says what it is). `thestill settings set entity_noindex
'["person:altruist"]'` keeps listed pages out of search. `/robots.txt` allows the public
paths and blocks the signed-in app; `/sitemap.xml` lists public shows,
episodes and indexable entities (a sitemap index past 40k URLs). In
single-user mode `robots.txt` disallows everything.

**Server-written content (spec #105).** For a signed-out request to a
public page, the shell also writes the page's main content inside
`#root` (episode: h1, summary lead, the first 6 quotes, people, links;
podcast: description, hosts, the first 10 summarised episodes; entity:
featured quote, who's talking or what they said, appearances, Wikipedia
and Wikidata facts; landing: the latest summary lead, who said what,
recently summarised; `/top`: the chart for the visitor's region, resolved
as `/api/public/top-podcasts` resolves it) and a
`<script type="application/json" id="thestill-bootstrap">` data block, both
built from the same cached payloads as `/api/public`
(`thestill/web/public_prerender.py`). The app seeds React Query from the
block and preloads the page's chunks before its first render, so
`createRoot` swaps the server HTML for the same page with no blank frame
and no repeat API calls. Every user agent gets the same bytes; signed-in
requests, a URL with a query string (header only), the runtime switch
(`thestill settings set public_prerender_enabled false`) and the
`PRERENDER_MAX_CONCURRENT` cap fall back to the head-only shell. To see
what a crawler without JavaScript reads:

```bash
./venv/bin/python scripts/body_word_count.py https://thestill.me/podcasts/<show>/episodes/<episode>
./venv/bin/python scripts/body_word_count.py --text --ua chrome <url>   # print the text too
```

With `INDEXNOW_KEY` set, the key is served at `/<key>.txt`, and IndexNow
(Bing, Yandex, Seznam, Naver) is pinged with an episode's page and its
show's page when the episode is first published with a public summary,
and with a show's pages when `thestill set-podcast-visibility` changes it
(`indexnow.submitted` in the logs; Bing Webmaster lists the submissions).

Fallback checklist when a public page changes:

- New visible text on a prerendered page: add it to the fallback builder
  if crawlers should read it, and to `UI_STRINGS` if it is fixed UI text
  (`test_public_prerender.py` fails on any text the page does not show).
- Content behind a click, sign-in or a later page never goes in the
  fallback, and neither do transcripts.
- A new public query: add its key to `publicKeys` in `api/public.ts`
  and, if the server seeds it, an entry name the seeder test covers.

**Frontend.** A signed-out visitor gets `PublicApp` (lazy-loaded), styled
with the scoped `.public-theme` tokens in `src/styles/public.css`. Signed-in
paths redirect to `/login?next=…`, and Google sign-in returns to `next`
after validation (`safe_next_path`). `tests/public-pages.spec.ts` fails if a
public page calls any `/api` endpoint outside `/api/public` and
`/api/auth/status`.

### Authentication (`/api/auth`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/auth/status` | GET | Get authentication mode and user info; also `usage_analytics_available` and `analytics` (`{key}` for a signed-in reader with usage events on, else null; spec #95). A suspended or banned session reads as signed out with `account_disabled: true`; `admin_contact_email` comes from `ADMIN_CONTACT_EMAIL` (spec #104) |
| `/api/auth/google/login` | GET | Initiate Google OAuth flow |
| `/api/auth/google/callback` | GET | OAuth callback handler. Redirects to `/login?error=account_disabled` or `/login?error=signups_closed` when spec #104 refuses the sign-in |
| `/api/auth/logout` | POST | Clear authentication cookie |
| `/api/auth/me` | GET | Get current user info (requires auth in multi-user mode) |
| `/api/auth/me` | PATCH | Update the fields sent: `region` (locks inference) and/or `usage_analytics_enabled` (spec #95 Settings switch) |
| `/api/me/mcp-token` | GET | The caller's remote MCP connector token state (spec #78 Phase 2): `enabled`, `state` (`none`/`active`/`expiring`/`expired`/`revoked`), masked prefix, scopes, created/expires/last-used/revoked timestamps, diagnostic last-used IP — never the plaintext |
| `/api/me/mcp-token` | POST | Create or rotate the caller's token with `{scopes: ["follows", "pipeline"]}` (`read` implicit; `pipeline` dropped for non-admins). Returns the full connector URL **once**; the old URL dies immediately |
| `/api/me/mcp-token` | DELETE | Revoke the caller's token (204, idempotent) |
| `/api/me/listening/{episode_id}` | PUT | Record the caller's listening position (spec #90): `{event, position_seconds, duration_seconds, session_id, seq}` with `event` one of `tick`/`pause`/`seek`/`ended`/`stop`/`mark_played`/`mark_unplayed`. Returns `{accepted, completed, listening}`; `accepted: false` means an older write from the same tab was ignored. 404 unknown episode, 400 non-finite or negative position. Playback writes also set the analytics-only `listened_at` (spec #103) unless the caller turned usage analytics off or sent `Sec-GPC: 1` |
| `/api/me/listening/{episode_id}` | GET | The caller's `{position_seconds, duration_seconds, completed_at, updated_at}` for one episode, or `{"item": null}` |
| `/api/me/listening/latest` | GET | Restore candidate: the most recently updated unfinished episode not stopped since, with the episode fields needed to build a player track, or `{"item": null}` |
| `/api/me/listening` | GET | `?state=in_progress&limit=3` — unfinished episodes written in the last 3 days, newest first, for the Continue listening strip |

**Refusals (spec #104).** Any route may answer with
`{"detail": {"error": <code>, "message": <text>}}`: 403 `account_disabled`
for a suspended or banned session, 503 `feature_paused` (plus `feature`) when
a runtime switch is off, 503 `read_only` for a non-admin write in panic
mode, 429 `quota_exceeded` (with `quota`, `limit`, `retry_after` and a
`Retry-After` header) when a per-user quota is used up, and 429
`rate_limited` when the per-user request budget is spent. The switches are managed with `thestill settings` (see
[configuration.md](configuration.md#abuse-prevention--runtime-switches-spec-104)).

### Webhooks

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/webhook/elevenlabs/speech-to-text` | POST | Receive transcription callback (HMAC-verified, no session) |
| `/webhook/elevenlabs/results` | GET | List webhook results (admin only) |
| `/webhook/elevenlabs/results/{id}` | GET | Get specific result (admin only) |
| `/webhook/elevenlabs/results/{id}` | DELETE | Delete result (admin only) |

### Unsubscribe

Root-level, signed-token routes used by briefing-email links (no auth cookie
required).

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/unsubscribe/briefings` | GET | Confirm page for a signed link (read-only; mail gateways prefetch GETs) |
| `/unsubscribe/briefings` | POST | Perform the unsubscribe (confirm button and RFC 8058 one-click) |

## Project Structure

```
thestill/web/
├── __init__.py              # Package init with create_app export
├── app.py                   # FastAPI application factory
├── dependencies.py          # Dependency injection (AppState, get_app_state)
├── responses.py             # api_response envelope helpers
├── task_manager.py          # Background task queue manager
├── background_server.py     # Background server runner
├── middleware/              # logging_middleware, security_headers, body_size, rate_limit
├── services/                # Web-layer services (webhook transcript processor)
├── routes/
│   ├── __init__.py
│   ├── health.py            # Health check endpoint
│   ├── webhooks.py          # ElevenLabs webhook handlers
│   ├── auth.py              # Authentication endpoints (OAuth, JWT)
│   ├── api_status.py        # System statistics
│   ├── api_dashboard.py     # Dashboard stats, activity, narration tile
│   ├── api_podcasts.py      # Podcast endpoints (resolve, follow, details)
│   ├── api_transcript_words.py  # Word-level transcript timings
│   ├── api_top_podcasts.py  # Top-podcasts chart
│   ├── api_episodes.py      # Episode content endpoints
│   ├── api_entities.py      # Entity mentions, review queue, corrections
│   ├── api_search.py        # Corpus, related, quick search
│   ├── api_inbox.py         # Per-user inbox
│   ├── api_briefings.py     # Briefing history, schedule, narration
│   ├── api_narrations.py    # Stored narration scripts
│   ├── api_imports.py       # Single-episode imports
│   ├── api_commands.py      # Processing commands (pipeline, queue, DLQ)
│   └── unsubscribe.py       # Signed one-click briefing-email unsubscribe
├── frontend/                # React SPA
│   ├── src/
│   │   ├── App.tsx
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx
│   │   │   ├── Podcasts.tsx
│   │   │   ├── Episodes.tsx
│   │   │   ├── EpisodeDetail.tsx
│   │   │   ├── BriefingDetail.tsx   # Briefing script + narration reader
│   │   │   ├── FailedTasks.tsx
│   │   │   └── Login.tsx            # Google OAuth login page
│   │   ├── contexts/
│   │   │   └── AuthContext.tsx      # Authentication state management
│   │   ├── components/
│   │   │   ├── Layout.tsx
│   │   │   ├── EpisodeCard.tsx
│   │   │   ├── PipelineActionButton.tsx
│   │   │   ├── FailureBanner.tsx
│   │   │   ├── FailureDetailsModal.tsx
│   │   │   ├── ProtectedRoute.tsx   # Route protection wrapper
│   │   │   └── UserMenu.tsx         # User avatar dropdown
│   │   └── api/
│   │       ├── client.ts
│   │       └── types.ts
│   └── package.json
└── static/                  # Built frontend assets
```

## Architecture

```
CLI (cli.py)                    Web (web/app.py)
     |                               |
     v                               v
  CLIContext                     AppState
     |                               |
     +--------> Services <-----------+
                   |
          PodcastService
          StatsService
          Repository
          PathManager
```

- **app.py**: Application factory with lifespan management
  - Initializes services once at startup (same pattern as CLI)
  - Stores `AppState` in `app.state` for route access
  - Registers route modules

- **dependencies.py**: FastAPI dependency injection
  - `AppState`: Dataclass mirroring `CLIContext` from CLI
  - `get_app_state()`: Dependency function for routes

## Authentication

The web server supports two authentication modes:

### Default-Deny API Access

Every `/api` router except `/api/auth` is registered in `app.py` with a
router-level `require_auth` dependency, so any new endpoint requires a
session unless it is consciously opted out. On top of that floor, the
operator-only surface requires an admin session (`require_admin`):

- Manual pipeline triggers: `POST /api/commands/{refresh,download,downsample,transcribe,clean,summarize,run-pipeline}` and `POST /api/commands/episode/{id}/cancel-pipeline`
- Queue and DLQ management: `/api/commands/queue/*`, `/api/commands/dlq*`
- Bulk processing, retries and reprocessing: `POST /api/episodes/bulk/process`, `POST /api/episodes/{id}/retry`, `POST /api/episodes/{id}/reprocess`
- Corpus-wide episode browser and failure details: `GET /api/episodes`, `GET /api/episodes/failed`, `GET /api/episodes/{id}/failure` (non-admins get `failure_reason: null` on the podcast episode routes too)
- Entity resolution surgery: `GET /api/entities/review-queue`, `POST /api/entities/corrections` (corrections change resolution state for every user)
- Operator dashboards: `GET /api/status`, `GET /api/dashboard/*` (system-wide activity, storage paths, provider config)
- Remote MCP feature flag: `GET /api/status/mcp` (spec #78 — reports only whether the endpoint is mounted; connector URLs are per user via `/api/me/mcp-token`)
- Stored webhook payload inspection: `GET`/`DELETE` `/webhook/elevenlabs/results*`

Ordinary users never drive the pipeline manually — it runs automatically
when they follow a podcast or import an episode. In single-user mode both
checks always pass (the local user is the operator). In multi-user mode,
`require_admin` needs a user whose `is_admin` flag is set. The frontend
mirrors these gates (`AdminRoute` pages plus `isAdmin`-conditional action
buttons), and `tests/integration/web/test_default_deny_auth.py` audits the
route table so an unauthenticated endpoint cannot ship unnoticed.

### Single-User Mode (Default)

When `MULTI_USER=false`:

- No login required, all routes accessible
- A default user is auto-created for data ownership tracking
- `UserMenu` shows "Single-user mode" indicator
- Best for personal/local deployments

### Multi-User Mode

When `MULTI_USER=true`:

- Google OAuth 2.0 authentication required
- Protected routes redirect unauthenticated users to `/login`
- JWT tokens stored in httpOnly cookies (30-day expiry by default)
- User data isolated by account

**Authentication Flow**:

1. User visits protected route → redirected to `/login`
2. User clicks "Sign in with Google" → redirected to Google OAuth
3. After Google approval → callback to `/api/auth/google/callback`
4. Server creates/updates user, issues JWT cookie
5. User redirected to dashboard

**Frontend Components**:

- `AuthContext`: Manages auth state, provides `login`/`logout` functions
- `ProtectedRoute`: Wrapper that enforces authentication
- `UserMenu`: Displays user avatar with logout option

## Webhook Security

### Dual-Layer Security

1. **HMAC Signature Verification** (Layer 1):
   - Validates `ElevenLabs-Signature` header
   - Uses `ELEVENLABS_WEBHOOK_SECRET` from config
   - Proves request actually came from ElevenLabs

2. **Metadata Validation** (Layer 2):
   - Requires `episode_id` in `webhook_metadata`
   - Verifies episode exists in database
   - Prevents processing webhooks from other apps sharing the same ElevenLabs account

### Configuration

```bash
# .env
ELEVENLABS_WEBHOOK_SECRET=your_secret_from_elevenlabs_dashboard
ELEVENLABS_WEBHOOK_REQUIRE_METADATA=true  # default: true
```

## Full Pipeline Execution

When "Run Full Pipeline" is triggered from the Web UI:

1. Task is created with `metadata.run_full_pipeline = true`
2. Each stage completion automatically enqueues the next stage
3. Pipeline continues until summarization or failure
4. Progress is tracked in real-time via polling

## Briefing Interface

Briefings are per-user readouts of the inbox (spec #36): each briefing covers
everything that landed in your inbox since the previous one. Generation is
lazy (opening the inbox creates one when eligible) or scheduled per user
(spec #50 — daily/weekly at a chosen hour and timezone, configured in
Settings).

### Inbox Card

The top of `/inbox` shows a "Today's briefing" card when a briefing with
unread coverage exists. It links to the briefing detail page.

### Briefing Detail Page (`/briefings/{id}`)

- **Hero**: the covered shows' artwork as one cover tile (a 2 × 2 mosaic
  past one show), a "From Show A, Show B and 3 more" line, and the blurred
  backdrop the episode page uses
- **Episode index**: inbox-style rows grouped by show (artwork leading,
  64 px, then title, gist, date and length), fed by
  `GET /api/briefings/{id}/episodes`; a row opens the episode in the
  reader overlay above the briefing
- **Script reader**: the rendered morning-briefing markdown, shown only
  when the index is empty (episodes deleted since) or unavailable
- **Narration variants** (spec #33): short/medium/long length switcher;
  generating a missing length calls `POST /api/briefings/{id}/narrate`
- **Mark listened**: advances the read state (the next briefing's window
  is cursor-based either way)

### Schedule Settings

The Settings page configures per-user scheduled generation: enable toggle,
daily/weekly frequency (with weekday), hour, and IANA timezone.
