# Admin Analytics over MCP: `get_analytics` and Public Page Traffic

> **Status:** 🚧 Phase 1 implemented (2026-11-07); Phase 2 proposed
> **Created:** 2026-11-07
> **Updated:** 2026-11-07
> **Priority:** Medium. "What are the site metrics today?" takes eight
> PostHog queries and still can't answer signups, activation or the
> Scorecard, because those live in prod Postgres and an agent can't read
> prod.
> **Related:** [#95 product-analytics](95-product-analytics.md) (Layer A
> report, `public_page_view`), [#103 content-popularity](103-content-popularity.md)
> (`public_page_daily_views`), [#111 product-health-metrics](111-product-health-metrics.md)
> (Scorecard), [#78 remote-mcp-access](78-remote-mcp-access.md) (connector
> identity and scopes), [#109 mcp-oauth](109-mcp-oauth.md) (connector
> tokens).

---

## Overview

The operator reads product health from three places. The spec #95 Layer A
report (signups, activation, cohorts, the spec #111 Scorecard) is in prod
Postgres, behind the admin-only `/analytics` page and `thestill analytics`.
Signed-out traffic is in PostHog. Search traffic is in Search Console.
An agent asked for "today's metrics" can reach only the last two, so it
reports traffic without signups and hands the operator a
`docker compose exec` command for the rest.

This spec adds one admin-only MCP tool, `get_analytics`, that returns the
same Layer A report the `/analytics` page shows, plus a new **public
traffic** section built from the spec #103 per-page counters: views per
day by page kind and the most-viewed public pages with titles and URLs.
The connector the operator already uses in Claude can then answer the
question in one call.

Phase 2 proposes a fix for the scraper problem that makes every traffic
number need a hand-written filter: count a view as **engaged** only after
the page has been visible for 10 seconds.

## Goals

1. An admin's connector can read the Layer A report (Scorecard, signups,
   activation, cohorts, engagement, content) with one tool call, over the
   connection they already have. No reconnect and no new scope.
2. Readers who aren't admins never see the tool in `tools/list`, and a
   call from one is refused.
3. The report gains a `traffic` section from `public_page_daily_views`:
   per-day views by kind, and the top public pages with title, path and
   the previous window's views. The CLI prints it. The web page gets it
   in its JSON payload.
4. One code path: the tool, the CLI and the web route all call
   `AnalyticsService.report()`.

## Non-goals

- **Sign-in funnel and referrers.** `login_started` → `login_completed`
  and `referrer_host` are PostHog-only (spec #95 D3, and the counter keeps
  no referrer). The operator dashboard in PostHog covers them.
- **Search Console.** It needs Google credentials the server doesn't hold.
- **A `/analytics` page section for traffic.** The JSON carries it; the
  page can render it in a follow-up.
- **Per-visitor or per-IP data.** The counter is a daily count per page
  and stays one.

## Background findings

- **Connector scopes.** Remote MCP tools are filtered by the token's
  scopes (`mcp/identity.py` `filter_tools_for_identity`). `pipeline` is
  admin-only, but the operator's claude.ai connector token was granted
  without it: none of the pipeline tools appear in its `tools/list`.
  Gating the tool on `pipeline` would hide it from the one client it is
  for, and add a reconnect step.
- **The web rule is "read = what a signed-in web user can see".** An
  admin's web session can see `/analytics`. So `read` plus a runtime
  `is_admin` check matches what the token stands for (spec #78: a token
  is the web session narrowed by scopes). `get_status` already branches on
  `identity.is_admin` the same way.
- **The counters exist and are read only as a column.**
  `public_page_daily_views (day, kind, object_id, views)` has counted
  browser views of landing, top, show, episode and entity pages since
  spec #103 Phase 2. The content report reads it only to put
  `public_views` next to shows and episodes that signed-in readers read.
  Nothing ranks pages by public views, and entity pages never appear.
- **The counters include JS-running scrapers.** Since 2026-10-27 a
  scraper with rotating browser user agents and IPs has fetched entity
  pages in bursts of up to ~1.8k views an hour, with no referrer and a
  fresh visitor key per view. It passes `classify_user_agent`, so it
  inflates both PostHog `public_page_view` and these counters. In PostHog
  the human proxy is "drop entity views with no referrer"; the counter
  can't apply it, because it stores no referrer.
- **Today is partial.** The counters are per UTC day, so the newest day
  of any window is incomplete and can't be hour-aligned with yesterday.

## Clarifications

- **D1. Scope: `read`, admin at runtime.** Registered under `read` in
  `mcp/scopes.py` (so the fail-closed test passes). `list_tools` omits it
  unless `identity.is_admin`. The handler refuses non-admins with the
  same message an unknown tool gets, so the tool's existence isn't
  advertised. Admin status is re-read per request (spec #78 effective
  scopes), so a demoted admin loses it on the next call. On stdio the
  identity is admin and the tool is visible.
- **D2. Input: `range` and `detail`.** `range` is `7d` (default), `28d`
  or `90d`, the existing `ALLOWED_RANGES`. Default `7d` because the
  question is usually "how are we doing now"; the web page keeps its `28d`
  default. `detail` is `summary` (default) or `full` (D7).
- **D3. Output: the report as JSON, with `available`.** The web route's
  shape, `{"available": true, …report}`, plus `detail`. When analytics is
  off (single-user or SQLite) the tool adds a `reason` to the web route's
  bare `{"available": false}`, because the model has to explain it.
  Aggregates only, as on the web page; no emails or user ids.
- **D4. Traffic previous window.** `views_previous` covers the same
  number of whole UTC days ending the day before the window starts. Today
  is partial, so the newest day undercounts; a note says so.
- **D5. Top pages limit 20**, all kinds mixed, ordered by views then kind
  and id. Pages whose object no longer exists (deleted episode, merged
  entity) are kept with a null title, so totals still add up.
- **D6. Cached 10 minutes per range**, like the web route. The report is
  about a dozen statements, each on its own connection, and a
  conversation tends to ask twice. The cache is per MCP server, keyed by
  range; `detail` is applied to the cached report.
- **D7. `detail=summary` trims lists, never totals.** Top 10 shows,
  episodes and public pages, the last 6 signup cohorts, the activation
  funnel without its per-source split, and a `trimmed` line saying so.
  `detail=full` returns every row. Totals, series and notes are the same
  in both.
- **D8. Entity paths follow URL ownership.** A re-typed entity lives at
  its current-type URL only if it owns that URL; when another row does
  (`company:nvidia` re-typed to product next to a real `product:nvidia`),
  its page stays at the old-type URL. The SQL builds the current-type path;
  the service then resolves each listed entity through
  `api_entities.entity_page_path`, the same `url_owner` rule the public
  site and sitemap use, injected so the service doesn't import the web
  layer.

## Execution plan

### Phase 1: report section, tool, CLI

1. **Model.** `TrafficDay(day, total, by_kind)`, `TrafficPage(kind,
   object_id, title, parent_title, path, views, views_previous)`,
   `TrafficReport(days, total, total_previous, top_pages, notes)` in
   `models/analytics.py`. `AnalyticsReport.traffic: TrafficReport`.
2. **Repository.** `AnalyticsRepository.traffic(window, limit)`
   and its Postgres implementation: one query for the per-day series, one
   for the top pages joining `podcasts`, `episodes` and `entities` for
   titles and slugs. Paths: `/`, `/top`, `/podcasts/{slug}`,
   `/podcasts/{slug}/episodes/{slug}`, `/entities/{type}/{slug}` (type
   from the entity's current type, slug from its id, as
   `api_public.entity_slug`). The unavailable stub raises as today.
3. **Service.** `AnalyticsService.report()` fills `traffic`, previous
   window per D4 (whole days; separate from the elapsed-time
   `previous_window()` the content section uses), and resolves entity
   paths per D8 through an injected `entity_path` callable. The web app,
   the CLI and the MCP server all inject it.
4. **Tool.** `get_analytics` in `mcp/tools.py`, scope `read` in
   `mcp/scopes.py`, hidden and refused for non-admins per D1. Builds an
   `AnalyticsService` from the repositories `setup_tools` already makes,
   with D6's cache and D7's `detail`. Description tells the model
   what is and isn't in it (no sign-in funnel, no referrers, counters
   include scrapers until Phase 2).
5. **CLI.** `thestill analytics` prints a "Public traffic" block: total vs
   previous, per-kind totals, top 10 pages. `--json` carries it as is.
6. **Docs.** `docs/analytics.md` (the tool, the traffic section, the
   scraper caveat), `docs/mcp-usage.md` (the tool), spec #95 question
   table row for "top public pages".
7. **Tests.** Postgres contract test for `traffic()` (series zero-filled,
   previous window, titles and paths per kind, deleted object kept with
   null title); `entity_page_path` (plain, re-typed, re-typed next to an
   owner of the current URL, missing); service tests for the resolver;
   MCP tests: admin sees and calls it, non-admin doesn't see it and is
   refused, demoted admin loses it, unavailable shape, summary vs full,
   cache per range, scope registry; CLI render test.

### Phase 2 (proposed): engaged views, to filter scrapers

Not built in Phase 1; needs a decision.

**Problem.** Every traffic number today needs the "entity without
referrer" filter, which is a guess: it drops real readers who arrive
from apps, bookmarks or AI chats (no referrer), and misses scraper
bursts on episode pages.

**Proposal: count engagement, not page loads.**

1. The public site keeps sending the page-view beacon on load.
2. It sends a second, one-off beacon, `POST /api/public/pageview/engaged`
   with the same path, once the page has been **visible for 10 seconds**
   in total (Page Visibility API; time in a background tab doesn't
   count). A route change before that cancels it. Timing stays in page
   memory; no new identifier, cookie or storage.
3. The server applies the same checks (signed out, browser UA, known
   public path) and
   - adds one to a new nullable `engaged_views` column on the counter
     row for the day the engaged beacon arrives. Rows from before the
     migration stay NULL ("not measured"), not 0, and the report starts
     ratios from the first measured day;
   - sends a PostHog `public_page_engaged` event with `kind` and
     `referrer_host` through `capture_event`, so GPC, the visitor key
     rules and the property allowlist apply unchanged.
4. Engaged views are an **independent count**, not a subset of the same
   row's views: a page loaded at 23:59:55 and engaged at 00:00:05 lands on
   two days. Ratios are read over a week or more, never per day.
5. The traffic section reports `views` (labelled unfiltered) and
   `engaged_views` side by side. It keeps ranking by views until the
   validation below says engaged views separate scrapers from readers.

**Why this and not the alternatives.**

| Approach | Verdict |
|---|---|
| Referrer heuristic (today's PostHog filter) | Guess; wrong both ways, and the counter can't apply it |
| IP or ASN blocklists | The scraper rotates hundreds of IPs; needs an ASN database; residential proxies defeat it; Caddy strips the IP before PostHog by design |
| Rate limits per IP or visitor key | Fresh key per view, rotating IPs: nothing to key on |
| User-agent rules | Already applied; this scraper sends real browser UAs |
| CAPTCHA or challenge | Public pages are for search engines and readers; a challenge hurts both |
| **Visible ≥ 10 s** | Cheap; filters scrapers that run the page's own JS and leave, which is what this one does; raw views stay, so nothing is lost |

**Risks.** It is a dwell-time proxy, not a proof. Anyone can POST the
engaged beacon directly, with no load before it and no wait, so a
scraper that targets the beacon gets through; nothing here makes that
expensive. It filters only scrapers that run our JavaScript and move on,
which the 2026-10-27 scraper does. Readers who leave within 10 s aren't
counted as engaged, which is the point. The 10 s threshold is a guess:
Phase 2 ships it and compares `engaged_views / views` over scraper-burst
weeks with normal weeks before anything ranks by it.

## Review log

Codex (local, read-only) reviewed the first draft on 2026-11-07. Adopted:
entity URL ownership (P1, D8), a compact default and a cache (P2, D6 and
D7), Phase 2's honest risk wording, cross-midnight attribution and NULL
for unmeasured history (P2 ×3), and the unavailable shape (P3, D3). It
found no leak in D1: both remote doors reload the user per request and
share `is_effective_admin`, and `tools/list` is built per request.

## Alternatives considered

- **A new `analytics` scope.** Cleaner on paper, but every client needs a
  reconnect to get it, and `read` + admin already means "what this
  admin's web session can see".
- **Gate on `pipeline`.** Same reconnect problem (D1), and analytics
  isn't a pipeline action.
- **Daily digest email with the Scorecard.** Push, not pull; doesn't let
  an agent answer a question. Could reuse the same report later.
- **Mirror signups into PostHog.** Reverses spec #95 D3 and still
  couldn't join the anonymous sign-in id to the person.

## Files touched

- `thestill/models/analytics.py`: traffic models, `AnalyticsReport.traffic`
- `thestill/repositories/analytics_repository.py`: abstract `traffic()`
- `thestill/repositories/postgres_analytics_repository.py`: SQL + method
- `thestill/services/analytics_service.py`: fill `traffic`
- `thestill/mcp/analytics_tools.py` (new), `thestill/mcp/tools.py`,
  `thestill/mcp/scopes.py`: the tool
- `thestill/web/routes/api_entities.py`: `entity_page_path`,
  `entity_path_resolver`; `thestill/web/app.py`: inject it
- `thestill/cli.py`: traffic block
- `docs/analytics.md`, `docs/mcp-usage.md`, `specs/95-product-analytics.md`,
  `specs/README.md`
- Tests under `tests/unit/` and the Postgres contract suite

## Open questions

1. Phase 2: is 10 s visible the right threshold, and what
   `engaged_views / views` gap on burst weeks is enough to rank by it?

## Rollback plan

Phase 1 adds a read-only tool and a report field; no migration. Revert
the commit. Phase 2's column is additive with a default; the beacon can
be switched off by reverting the client change.
