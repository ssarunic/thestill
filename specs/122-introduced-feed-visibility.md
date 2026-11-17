# Introduced-Feed Visibility

> **Status:** 📝 Draft (2026-11-17)
> **Created:** 2026-11-17
> **Updated:** 2026-11-17
> **Priority:** High (anti-abuse series, severity #5 of 6). Any signed-in user can bring in any RSS feed, and that feed gets indexable public pages on thestill.me at the operator's expense.
> **Author:** Product & Engineering
> **Related:** [#94 public-pages](94-public-pages.md) (the visibility predicate this narrows), [#105 server-rendered-public-pages](105-server-rendered-public-pages.md) (404 + `noindex`, sitemap, entity indexing rule, IndexNow), [#104 abuse-prevention-and-user-admin](104-abuse-prevention-and-user-admin.md) (`feed_introduced` ledger, audit log, settings), [#96 prompt-injection-hardening](96-prompt-injection-hardening.md) (summaries are untrusted LLM output), [#99 top-chart-movement-and-topics](99-top-chart-movement-and-topics.md) and [#57 eea-top-podcast-regions](57-eea-top-podcast-regions.md) (chart data), [#28 corpus-search-and-entities](28-corpus-search-and-entities.md) (search, related rails), [#117 follow-from-episode-page](117-follow-from-episode-page.md). Siblings: [118-per-feed-processing-budget.md](118-per-feed-processing-budget.md) (consumes the same feed-trust predicate), [119-audio-duration-cap.md](119-audio-duration-cap.md), [120-spend-guardrails.md](120-spend-guardrails.md), [121-signup-trust-and-account-farming.md](121-signup-trust-and-account-farming.md) (defines probation, which "established" here depends on), [123-abuse-triage-sweep.md](123-abuse-triage-sweep.md)

---

## Executive summary

A show is public on thestill.me by default. `podcasts.public_pages` defaults to true, and the only other test is "not a synthetic import container". So a feed anyone brings in gets LLM-written summaries, quote pages, entity pages, sitemap entries, IndexNow pings and server-rendered HTML that search engines and answer engines read. That is free SEO hosting for spam, and it puts infringing, abusive or prompt-injected text under the operator's domain.

This spec makes public visibility earned. A show is public only once it is **vetted**: it has been on a top chart, or it is followed by at least N established users, or an admin approved it. Until then its pages are visible only to its followers and to admins, and its content does not reach other users through search, related rails, entity pages or MCP tools.

The vetting test is one shared predicate, `FeedTrust`, in a new `thestill/services/feed_trust.py`. Spec #118 uses the same predicate to set a feed's processing budget. Its result is stored on the podcast row, so request paths and SQL gates read a column, not a follower count.

## Current state

Every surface below decides "public" from `public_pages` and `synthetic` alone. None asks who brought the feed in or whether anyone else wants it.

| Surface | Today | Reference |
|---|---|---|
| Column | `public_pages boolean NOT NULL DEFAULT true`; the migration describes it as "the publisher takedown switch" | [postgres_schema.py:268](../thestill/repositories/postgres_schema.py#L268), [0015_public_visibility.py:43](../thestill/migrations/versions/0015_public_visibility.py#L43), SQLite [sqlite_podcast_repository.py:2061](../thestill/repositories/sqlite_podcast_repository.py#L2061) |
| Predicate | `public_pages AND NOT synthetic` | Postgres [postgres_podcast_repository_podcasts.py:658](../thestill/repositories/postgres_podcast_repository_podcasts.py#L658), SQLite [sqlite_podcast_repository.py:6019](../thestill/repositories/sqlite_podcast_repository.py#L6019) |
| Hidden set | `list_hidden_podcasts` returns rows with `public_pages = false`, "small by construction (takedowns only), so callers filter in memory" | [postgres_podcast_repository_podcasts.py:665](../thestill/repositories/postgres_podcast_repository_podcasts.py#L665), [podcast_repository.py:426](../thestill/repositories/podcast_repository.py#L426) |
| Visibility cache | `PublicVisibility` caches the hidden sets and per-podcast answers for 60 s; `fingerprint()` keys every public payload cache | [public_access.py:171](../thestill/web/public_access.py#L171), [:190](../thestill/web/public_access.py#L190), [:223](../thestill/web/public_access.py#L223), TTL [:56](../thestill/web/public_access.py#L56) |
| Public episode list (episode pages, landing "recent summaries", sitemap) | SQL gate `p.public_pages AND NOT p.synthetic` | [postgres_podcast_repository_episodes.py:2058](../thestill/repositories/postgres_podcast_repository_episodes.py#L2058), [sqlite_podcast_repository.py:6486](../thestill/repositories/sqlite_podcast_repository.py#L6486), [api_public.py:493](../thestill/web/routes/api_public.py#L493) |
| Public podcast and episode pages | `podcast_is_public` / `episode_is_public` | [api_public.py:245](../thestill/web/routes/api_public.py#L245), [:261](../thestill/web/routes/api_public.py#L261) |
| Quotes, who-said-what, trending, entity index facts | SQL gate in `_gate()` and the facts and presence queries | [public_quotes.py:72](../thestill/repositories/public_quotes.py#L72), [:231](../thestill/repositories/public_quotes.py#L231), [:257](../thestill/repositories/public_quotes.py#L257), [api_public.py:1162](../thestill/web/routes/api_public.py#L1162) |
| Public entity page | Returns 404 only for a missing or `public_hidden` entity. Served (noindex) even when no mention is on a public show; `related` comes from `entity_cooccurrences` over **all** episodes | [api_public.py:692](../thestill/web/routes/api_public.py#L692), [:748](../thestill/web/routes/api_public.py#L748), [:830](../thestill/web/routes/api_public.py#L830), [postgres_entity_repository.py:905](../thestill/repositories/postgres_entity_repository.py#L905) |
| Sitemap | Built from `list_public_episode_rows` plus the entity indexing rule | [public_shell.py:581](../thestill/web/public_shell.py#L581) |
| Top chart (public) | Links a row to its show page unless the slug is in the hidden set | [api_public.py:557](../thestill/web/routes/api_public.py#L557) |
| IndexNow | Pinged on first publish when `is_podcast_public` | [task_handlers.py:976](../thestill/core/task_handlers.py#L976) |
| Signed-in corpus search, quick search | Unscoped: any user's search returns chunks, episodes and entities from every feed | [api_search.py:207](../thestill/web/routes/api_search.py#L207), [:384](../thestill/web/routes/api_search.py#L384); `SearchFilters` has no viewer field [base.py:75](../thestill/search/base.py#L75) |
| Related-episodes rail | Reads `episode_related` with no visibility join | [api_search.py:304](../thestill/web/routes/api_search.py#L304) |
| Signed-in entity pages and mentions | Unscoped across feeds | [api_entities.py:580](../thestill/web/routes/api_entities.py#L580), [:709](../thestill/web/routes/api_entities.py#L709) |
| MCP `search_corpus`, `find_mentions`, `list_quotes_by`, `get_entity`, `list_episodes_by_entity` | Unscoped; handlers get no caller identity | [search_tools.py:107](../thestill/mcp/search_tools.py#L107), [entity_tools.py:266](../thestill/mcp/entity_tools.py#L266), [tools.py:1753](../thestill/mcp/tools.py#L1753) |
| Signed-in podcast page by slug | Any signed-in user can open any show | [api_podcasts.py:205](../thestill/web/routes/api_podcasts.py#L205) |
| Takedown CLI | `thestill set-podcast-visibility --public/--private`; pings IndexNow; writes **no** audit entry | [cli.py:477](../thestill/cli.py#L477) |
| Who brought a feed in | `feed_introduced` ledger row on the 0→1 follow | [follow_policy.py:93](../thestill/services/abuse/follow_policy.py#L93) |
| Chart membership | `top_podcasts` rows persist (`first_seen_at`, `last_seen_at`); `top_podcast_rankings` is replaced per region on each seed; charts match shows by exact `rss_url` | [postgres_schema.py:835](../thestill/repositories/postgres_schema.py#L835), [postgres_podcast_repository_podcasts.py:388](../thestill/repositories/postgres_podcast_repository_podcasts.py#L388), [:796](../thestill/repositories/postgres_podcast_repository_podcasts.py#L796) |
| Audit actions | Fixed tuple; no `podcast.*` actions | [audit_service.py:31](../thestill/services/abuse/audit_service.py#L31) |

## Threat

1. **SEO spam hosting.** An attacker brings in a feed they control, with episodes stuffed with brand names and links. The pipeline writes summaries, quotes and entity pages. The sitemap and IndexNow hand them to search engines under thestill.me's reputation. Spec #104's quota (10 new feeds a day per user) slows this but does not stop it, and sock-puppet accounts multiply it (spec #121).
2. **Reputational and legal risk.** Infringing re-uploads, harassment or extremist content get an LLM summary and a canonical URL on the operator's domain.
3. **Prompt-injected summaries.** Spec #96 hardens the prompts, but a feed written to steer the summarizer is still the attacker's chosen input. Public pages turn a partial success into a published one.
4. **Cross-user leakage.** Even with public pages off, the attacker's content reaches other signed-in users through corpus search, related rails, entity pages and MCP tools, and its entity mentions shape public entity pages (`related`, a 200 response for a name only the spam feed mentions).

## Goals

1. A show is public only when vetted: on a top chart, followed by at least N established users, or approved by an admin.
2. An unvetted show's content reaches only its followers and admins: no public page, sitemap entry, IndexNow ping, public quote, public entity contribution, cross-user search hit, related-rail card or MCP result for anyone else.
3. One predicate. The vetting test lives in one service and spec #118 uses the same one.
4. Request paths stay cheap: they read a stored column, never count followers.
5. No legitimate show that is public today disappears without an operator seeing it on a list first.
6. Every manual change of a show's visibility is audited, including the existing takedown command.

## Non-goals

- Deciding processing budgets. That is spec #118; this spec only supplies the predicate.
- Detecting fake accounts. Spec #121 defines probation; this spec only excludes probation accounts from the follower count.
- Content moderation of vetted shows. A chart show with bad content is handled by the existing takedown.
- Changing the entity indexing rule of spec #105 decision 3. It keeps working; it just sees fewer shows.
- Single-user installs. They have no public pages (`multi_user` is required, [app.py:945](../thestill/web/app.py#L945)) and no other users.

## Design

### 1. Feed trust: one shared predicate

New `thestill/services/feed_trust.py`:

```python
class TrustTier(str, Enum):
    ADMIN = "admin"        # an admin approved the show
    CHART = "chart"        # rss_url appears in top_podcasts (ever charted)
    FOLLOWED = "followed"  # >= feed_trust_min_followers established followers
    NONE = "none"

class FeedTrust:
    def evaluate(self, podcast_id: str) -> TrustTier: ...      # computes and stores
    def tier(self, podcast_id: str) -> TrustTier: ...          # reads the stored column
    def is_trusted(self, podcast_id: str) -> bool: ...         # tier != NONE
    def reevaluate_many(self, podcast_ids: Iterable[str]) -> int: ...
```

Spec #118 calls `FeedTrust.tier()` for the budget; this spec calls it for visibility. Neither re-derives the rule.

**The rule**, first match wins:

1. `admin` when `podcasts.public_pages IS TRUE` (an explicit approval, §2).
2. `chart` when `EXISTS (SELECT 1 FROM top_podcasts t WHERE t.rss_url = p.rss_url)`. This uses `top_podcasts`, not `top_podcast_rankings`, so a show that drops off the chart stays trusted. The match is the same exact `rss_url` join the chart page already uses.
3. `followed` when the count of **established** followers is at least `feed_trust_min_followers` (default 2). Established means `users.status = 'active'`, account at least `feed_trust_min_account_age_days` old (default 14), and not in probation once spec #121 adds it. The introducer counts, so the default means "one other real person".
4. Otherwise `none`.

**Stored, not computed per request.** New columns on `podcasts`: `trust_tier text NOT NULL DEFAULT 'none'` and `trust_evaluated_at timestamptz NULL`. `evaluate()` writes them and, when the tier crosses the trusted/untrusted line, sets `visibility_changed_at = now()` (§3) and logs `feed_trust.changed` with `podcast_id`, `old`, `new`.

**When it re-evaluates:**

| Event | Call site |
|---|---|
| Follow or unfollow | after the follow transaction in the follower service |
| Chart seed finishes for a region | after the rankings replace ([postgres_podcast_repository_podcasts.py:388](../thestill/repositories/postgres_podcast_repository_podcasts.py#L388)), for shows whose `rss_url` is in the batch |
| Admin approve, hide or reset (§5) | in the same command |
| User suspended, banned or reinstated | for every podcast the user follows |
| Daily sweep | spec #104's housekeeping job, for all podcasts; catches accounts ageing into "established" and probation ending |

Settings `feed_trust_min_followers` and `feed_trust_min_account_age_days` join the spec #104 registry, so the operator can tune them without a deploy. Changing either triggers a full sweep.

### 2. Representation: tri-state `public_pages`

`public_pages` becomes nullable, with three values:

| Value | Meaning | Set by |
|---|---|---|
| `NULL` | **auto**: public when `trust_tier <> 'none'` | default for new shows; `thestill podcast reset-visibility` |
| `TRUE` | **approved**: public regardless of trust (and feeds `admin` tier) | `thestill podcast approve` |
| `FALSE` | **hidden**: the existing publisher takedown, wins over everything | `thestill podcast hide`, `set-podcast-visibility --private` |

A separate `visibility` enum was considered. The tri-state keeps the existing meaning of `false` (takedown), keeps `list_hidden_podcasts` small, and keeps every existing `--public/--private` call valid. Its cost is that every SQL gate must change, which §3 handles in one place.

**Effective visibility**, as one SQL fragment and one Python function:

```sql
-- PUBLIC_PODCAST_SQL(p)
NOT COALESCE(p.synthetic, FALSE)
AND COALESCE(p.public_pages, p.trust_tier <> 'none')
```

```python
def podcast_is_effectively_public(public_pages, trust_tier, synthetic, mode) -> bool
```

In `legacy` mode (§7) the second line becomes `COALESCE(p.public_pages, TRUE)`. The repository builds the fragment from the current setting, so the mode never appears in SQL.

### 3. One gate for every surface

Add `public_podcast_sql(alias)` to a shared module (next to `_gate()` in [public_quotes.py](../thestill/repositories/public_quotes.py)) and replace each literal `p.public_pages AND NOT p.synthetic` with it: `is_podcast_public` (both backends), `list_public_episode_rows` (both backends), and the three gates in `public_quotes.py`. A test greps the repositories for the literal so a new query cannot reintroduce it.

`PublicVisibility` changes:

- `list_hidden_podcasts` keeps its meaning: explicit `FALSE` rows only. It stays small. The top chart's in-memory slug filter stays correct, since a charted show is trusted by definition and only a takedown hides it.
- `fingerprint()` adds a **visibility epoch**: `SELECT max(visibility_changed_at) FROM podcasts`, loaded through the same 60 s `_TtlValue`. Any trust crossing, approval or hide changes the epoch, so every cached public payload and the sitemap drop the show on the next refresh, as a takedown does today.
- `podcast_is_public` keeps its per-podcast 60 s cache; the repository answers from the new fragment.

### 4. Surface-by-surface behaviour

| Surface | Unvetted show (`auto`, tier `none`) |
|---|---|
| Public podcast, episode, summary, quotes API | 404, as for a hidden show today |
| Public shell | 404 + `noindex` (spec #105 rule for hidden paths) |
| Sitemap | Absent (follows `list_public_episode_rows`) |
| IndexNow on publish | Not sent (already checks `is_podcast_public`) |
| IndexNow on becoming public | **New:** when `evaluate()` makes a show public, submit its public paths with the same helper as [cli.py:508](../thestill/cli.py#L508); when it makes one private, submit the paths read before the change |
| Landing recent summaries, who-said-what, trending | Excluded by the SQL gate |
| Public top chart | Unchanged (a charted show is never unvetted) |
| Public entity page | See §6 |
| Signed-in podcast and episode page by slug | **Reachable** by any signed-in user, so a follower can share a link and the friend can follow (spec #117). `noindex`, no public URL, and no inbound links from discovery surfaces |
| Signed-in corpus and quick search (REST) | Hits only from shows that are public, followed by the caller, or any show for an admin |
| Related-episodes rail | Same filter at read time in `_read_related`, over-fetching 3× the limit before filtering |
| Signed-in entity pages and mention lists | Same filter on mentions, quotes and episode lists |
| MCP search and entity tools | Same filter; the dispatcher passes the caller's user id (remote identity) to the handlers; anonymous or stdio callers see public shows only in multi-user mode |
| Briefings, inbox | Unchanged: they are already per-user and follower-scoped |

The signed-in filter is one helper, `visible_podcast_scope(user) -> Optional[ViewerScope]`, returning `None` for admins (no filter) and otherwise the caller's followed podcast ids. `SearchFilters` gains `viewer_scope`; backends add `AND (<public fragment> OR podcast_id = ANY(followed))`. Followed-id lists are small (spec #104 caps follows).

### 5. Admin review: CLI, API and queue

New commands in the existing `podcast` group ([cli.py:5554](../thestill/cli.py#L5554)), in the style of `set-show-link` ([cli.py:396](../thestill/cli.py#L396)):

```bash
thestill podcast review [--json] [--limit 50]          # the queue: unvetted shows with a public-ready episode
thestill podcast approve --podcast-id X --reason "..."  # public_pages = TRUE
thestill podcast hide --podcast-id X --reason "..."     # public_pages = FALSE (takedown)
thestill podcast reset-visibility --podcast-id X        # public_pages = NULL (auto)
thestill podcast trust --podcast-id X [--json]          # tier, why, established followers, introducer
thestill podcast vetting-report [--json]                # backfill preview, §7
```

`set-podcast-visibility --public/--private` stays as an alias of `approve`/`hide`, and now writes an audit entry too.

The **review queue** lists shows with `public_pages IS NULL`, `trust_tier = 'none'`, and at least one summarised episode, oldest first. Each row shows title, feed host, introducer (from the `feed_introduced` ledger) and their account age and status, established follower count, episode count, and the first summary preview. Summary previews are rendered as plain text (spec #96 rule 4).

Web: `GET /api/admin/feeds/review` and `POST /api/admin/feeds/{podcast_id}/visibility` (body `{visibility: "approved"|"hidden"|"auto", reason}`), under `require_admin`, with a "Feeds" tab beside `/admin/users`. The user detail page's "feeds this user brought in" list (spec #104 §1.5) shows each feed's tier and visibility.

**Audit.** New actions in `AUDIT_ACTIONS`: `podcast.approve`, `podcast.hide`, `podcast.reset_visibility`, `podcast.visibility_backfill`. Details carry `podcast_id`, title, old and new `public_pages`, tier, and reason. The introducer goes in `target_user_id`, so a user's audit history shows actions on feeds they brought in. Automatic trust crossings are logged (`feed_trust.changed`), not audited: they are not admin actions and would flood the log.

**Alerts.** The spec #104 daily digest adds "N shows waiting for review". Spec #123's sweep may also feed this queue.

### 6. Entity pages must not leak private-feed mentions

Quote counts, presence and the indexing facts already go through the SQL gate, so they will follow §3. Three paths do not:

1. **The page itself.** Today an entity mentioned only by an unvetted show still gets a 200 (noindex) with its name, description and enrichment. New rule in `_build_entity_payload`: 404 unless the entity has at least one resolved mention on a public episode, or a host, recurring or guest role on a public show. The check is one `EXISTS` query using the shared fragment. Its result is cached with the page payload, which is already keyed on the fingerprint.
2. **`related`.** `entity_cooccurrences` counts all episodes. Filter the co-occurring list to entities that pass the same check, in one batched query (`entities_with_public_presence(ids)`). Counts shown are capped to what is public. The precise fix (co-occurrence counted on public episodes only) is an open question.
3. **Enrichment affiliations** that link to another entity: same batched filter as `related`.

Signed-in entity pages (`api_entities.py`) use the viewer scope from §4 instead of the public fragment.

An entity that is also mentioned on a vetted show keeps its page. Only the unvetted show's mentions, quotes and roles are invisible.

### 7. Rollout switch

Runtime setting `public_visibility_mode` (spec #104 registry):

- `legacy`: `auto` shows are public whatever their tier (today's behaviour). The default at deploy.
- `vetted`: `auto` shows are public only when trusted.

The switch is read where the fragment is built and is part of the fingerprint, so flipping it reaches every cache within a minute. It is also the rollback.

## Migration/backfill

Alembic `0029` (or the next free number if a sibling lands first):

1. `ALTER TABLE podcasts ALTER COLUMN public_pages DROP NOT NULL, ALTER COLUMN public_pages SET DEFAULT NULL`.
2. `UPDATE podcasts SET public_pages = NULL WHERE public_pages = true`. The schema cannot tell a default `true` from one set by `set-podcast-visibility --public`, so all become auto. The few explicit ones reappear in the vetting report and are re-approved there. Rows set by `--private` stay `false`.
3. Add `trust_tier`, `trust_evaluated_at`, `visibility_changed_at`.
4. Mirror the same in the Postgres bootstrap schema ([postgres_schema.py:284](../thestill/repositories/postgres_schema.py#L284)) and the SQLite migration block ([sqlite_podcast_repository.py:2060](../thestill/repositories/sqlite_podcast_repository.py#L2060)).

Because the mode starts at `legacy`, step 2 changes nothing visible. Then:

1. `thestill podcast trust --all` (or the first daily sweep) fills `trust_tier`.
2. `thestill podcast vetting-report` lists every show that would go private under `vetted`: title, tier, established followers, introducer, public episode count, and public page views over 28 days from `public_page_daily_views` (spec #103, [postgres_schema.py:542](../thestill/repositories/postgres_schema.py#L542)).
3. The operator approves the keepers (`approve`, or `approve --from-file ids.txt`, one audit entry per show plus one `podcast.visibility_backfill` summary).
4. `thestill settings set public_visibility_mode vetted`.

We do not grandfather all current shows: that would keep every spam feed already public. We do not hide unreviewed shows silently: the report is the operator's checkpoint, and pages with real traffic are at the top of it.

## Rollout

1. **Phase 1:** migration, `FeedTrust`, re-evaluation hooks, CLI `trust`, `vetting-report`, `approve`, `hide`, `reset-visibility`, audit actions. Mode stays `legacy`. No user-visible change.
2. **Phase 2:** the shared SQL fragment, epoch in the fingerprint, entity page rules (§6), IndexNow on crossings. Flip to `vetted` after the report is worked through.
3. **Phase 3:** signed-in and MCP viewer scoping (§4). Separate because it touches search backends and the MCP dispatcher.
4. **Phase 4:** admin "Feeds" tab, digest line.

Watch after the flip: `public_shell.served status=404` rate, sitemap URL count, Search Console coverage drops, and the `feed_trust.changed` log volume.

## Testing

- **FeedTrust unit:** each tier; the order (approved beats chart beats followed); chart match survives the show leaving `top_podcast_rankings`; suspended, banned, young and (once #121 lands) probation followers do not count; the introducer counts; settings changes take effect.
- **Re-evaluation:** a second established follow makes a show public and bumps `visibility_changed_at`; an unfollow or a ban takes it back to `none`; the daily sweep promotes a show whose follower aged past the threshold.
- **Gate:** for both backends, `is_podcast_public` and `list_public_episode_rows` across the 3 × 2 grid of `public_pages` × trusted, in both modes; synthetic is never public. A guard test fails if any repository file contains the literal `public_pages AND NOT`.
- **Public API and shell** (extend `test_public_api.py`): unvetted show → API 404, shell 404 + `noindex`, absent from sitemap, absent from recent summaries and who-said-what; flipping to vetted or approving makes it appear after cache invalidation.
- **Entity leaks:** an entity mentioned only on an unvetted show → public 404; an entity on both → 200 with no quote, role, `most_discussed_on` or `related` entry from the unvetted show; `related` omits entities whose only presence is private.
- **Viewer scope:** a non-follower's corpus search, quick search, related rail and MCP `search_corpus` and `find_mentions` exclude the unvetted show; its follower and an admin see it.
- **CLI and audit:** each command writes the expected action with old and new values; `set-podcast-visibility` now audits; `vetting-report --json` lists exactly the shows that `vetted` would hide.
- **IndexNow:** a trust crossing submits the right paths (mocked client), and nothing when disabled.

## Open questions

1. **N and account age.** Is 2 established followers at 14 days right? A higher N protects more but keeps legitimate niche shows private longer. Spec #121's probation may make the age test redundant.
2. **Signed-in direct links.** This spec keeps an unvetted show reachable by slug for any signed-in user, so sharing works (spec #117). The stricter reading, "followers and admins only", would 404 for everyone else. Is the sharing path worth the exposure to signed-in users?
3. **Chart match by `rss_url`.** Exact URL match misses feeds whose URL changed (http/https, redirects). Should the chart test also match `apple_track_id` or the Apple link stored on the show (spec #87)?
4. **Co-occurrence counts.** Filtering `related` by the public check hides private entities but the `episode_count` still includes private episodes. Do we compute public-only co-occurrence, or drop the count from the public payload?
5. **Entity text written from private feeds.** An entity's description or enrichment could have been drafted from an unvetted show's context before a vetted show mentioned it. Does entity enrichment need a provenance check, or is Wikidata-sourced text enough?
6. **Trust loss.** Should a show that was public and loses its second follower go private at once, or after a grace period, to avoid indexed pages flapping?
7. **Spec #118 coupling.** #118 may want more tiers (for example `followed` split by count). The service should own the tier enum so both specs extend it together.
