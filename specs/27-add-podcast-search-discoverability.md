# Add-Podcast Search & Discoverability

**Status**: 📝 Draft
**Created**: 2026-04-27
**Updated**: 2026-04-27
**Priority**: Medium (improves activation; preconditions a future paid-tier gate)

## Overview

The current "Add podcast" flow asks the user for an RSS URL — a hostile cold
start, especially for users who don't already know what feed they want.
This spec evolves the modal into a **single search-or-paste box** that
filters the user's regional top-500 chart in real time, while still
accepting an RSS URL when the user has one. The empty state shows the top
~10 podcasts in the user's region, replacing "blank input" with "here's
what's hot" as the default discoverability surface.

The redesign also pre-shapes the data path for a future free-tier gate
(spec'd separately when subscriptions land): top-500 entries are a finite,
curated set; arbitrary RSS URLs are not. Putting them on visibly separate
branches in the same modal makes the gate a one-line check later, with
zero UI restructure.

## Goals

1. **Discoverability.** When the modal opens with no input, the user sees
   the top ~10 podcasts for their resolved region, ready to follow with one
   click. Typing a partial name/artist filters the list live (debounced).
2. **Familiar power-user path preserved.** When the input contains
   something that looks like a URL, the modal flips to "Add this feed"
   mode and adds the typed URL via the existing pipeline.
3. **Region-aware.** The empty/search list reflects the user's saved
   region (spec #region introduced in `feat/user-region-top-podcasts`),
   with a "Change region" link to Settings — no per-modal region picker
   to keep the surface clean.
4. **Free-tier gate seam.** The backend exposes "is-this-in-the-top-list"
   as a boolean flag the gate can read later. No gate is enforced in this
   spec.
5. **De-dupe.** Already-followed podcasts in the search list show
   "Following" instead of "Follow" and skip the add flow.

## Non-goals

- A dedicated genre/category onboarding wizard (Apple-style first-run
  flow). The empty state on first follow already shows the same modal
  pre-loaded with top-10; that covers 80% of the value with 0% extra UI.
- Fuzzy / phonetic matching. SQL `LIKE '%term%'` over `name` and `artist`
  is enough at 500 rows per region. FTS5 / trigrams stay off the table
  until a metric says otherwise.
- A separate "Browse top podcasts" experience inside the modal — the
  existing `/top` page already covers deep browsing. The modal stays
  fast and shallow (max 10 visible, scroll for more, no pagination).
- Any tiering / payment gate enforcement. We expose the data needed to
  gate later; we do not gate now.
- Replacing the existing TopPodcasts page or its "Add" button. Both
  modal and page share the same backend search + add flows.

## User stories

- **New user, doesn't know what podcast to add.** Opens "Add podcast",
  sees the top 10 in their region, clicks Follow on two of them, done.
  Never typed a URL.
- **Returning user, heard a recommendation.** Opens "Add podcast", types
  "rest is", sees "The Rest Is History" and "The Rest Is Politics",
  clicks Follow.
- **Power user, has a niche feed.** Opens "Add podcast", pastes
  `https://feeds.example.com/show.xml`, sees a single "Add this feed" row
  underneath, clicks Add. Existing flow.
- **Returning user, accidentally tries to follow what they already follow.**
  Opens "Add podcast", types title, sees "Following" badge instead of
  the Follow button, no double-add.

## Background

### What already exists

- `top_podcasts` + `top_podcast_rankings` tables, region-keyed, ~500 rows
  per region ([sqlite_podcast_repository.py:725-749](../thestill/repositories/sqlite_podcast_repository.py#L725-L749)).
- `GET /api/top-podcasts?region=&limit=` endpoint
  ([api_top_podcasts.py](../thestill/web/routes/api_top_podcasts.py))
  returning `{rank, name, artist, rss_url, apple_url, youtube_url, category, source_genre}`.
- `POST /api/commands/add` accepting `{url}` to enqueue the existing
  add-podcast pipeline ([api_commands.py:380](../thestill/web/routes/api_commands.py#L380)).
- `User.region` and `region_locked` columns; users land here from
  IP-inferred or manually-set region.
- Existing `AddPodcastModal.tsx` ([components/AddPodcastModal.tsx](../thestill/web/frontend/src/components/AddPodcastModal.tsx))
  — the surface we're evolving.
- Follower service exposing `is_following_by_slug` /
  `get_followed_podcast_ids` ([follower_service.py:141](../thestill/services/follower_service.py#L141)).

### What's missing

1. Backend search: the top-podcasts endpoint has no `?q=` filter today.
2. "Already following" awareness on top-podcast rows: the response carries
   no `is_following` flag because top-podcasts and the user-followed
   `podcasts` table are decoupled (matched by `rss_url`).
3. Frontend: `AddPodcastModal` is single-input, URL-only, no list, no
   debounce, no follow-vs-add forking.

## Architecture

### Resolution order in the input box

The input is parsed each keystroke into one of three states:

- **`empty`**: input is blank → show top 10 by rank, no API call needed
  beyond the initial `/api/top-podcasts?limit=10`.
- **`url`**: input matches the URL heuristic (starts with `http://`,
  `https://`, or contains `://` after a non-whitespace prefix) → hide the
  list; show "Add this feed" row with the literal URL and an Add button
  that invokes the existing `addPodcast({url})`.
- **`query`**: anything else, length ≥ 2 → debounced (250 ms) call to
  `/api/top-podcasts?q=<text>&limit=10`, list re-renders with results.

The transition is purely client-side; the backend only ever sees `q`
when state is `query`.

### Backend changes

- **Extend `get_top_podcasts(region, limit, category)`** in
  `SqlitePodcastRepository` to accept `q: Optional[str] = None`. When
  provided, add `AND (LOWER(p.name) LIKE ? OR LOWER(p.artist) LIKE ?)`
  with `%lower(q)%` on both sides. Preserve `ORDER BY r.rank ASC` so
  rank order is what the user sees, not relevance — top podcasts that
  match come first.
- **Extend `GET /api/top-podcasts`** with `q: Optional[str] = Query(None,
  min_length=1, max_length=100)`. Pass through to the repo. Validation:
  trim whitespace; treat empty trimmed string as None.
- **Annotate each row with `is_following: bool`.** The endpoint resolves
  the current user (via `get_current_user`), fetches their followed
  podcast `rss_url` set once per request, and stamps each top-podcast
  row. Anonymous → all `false`. This is the gate seam too: future
  `can_follow` derives from `is_top_podcast OR user.is_paid` once the
  paid-tier ships.

### Frontend changes

- **Input parse helper** `parseAddInput(text): {kind: 'empty'|'url'|'query', value}`
  in `AddPodcastModal` or co-located helper.
- **Debounced query effect**: 250 ms, abortable via `AbortController` so
  fast typers don't get stale results.
- **List component**: virtualised? No — capped at 10 rows, plain `<ol>`.
  Each row: rank, name, artist, category, action button.
- **Action button states**: `idle` → `Follow`; `pending` → `Adding…`;
  `done` → `Following ✓` (disabled); `error` → `Retry` (red).
- **`is_following=true` rows**: show `Following ✓` directly (disabled),
  no add path.
- **Region badge**: "Top in 🇺🇸 US — Change region" linking to `/settings`,
  reuses the flag map already in [TopPodcasts.tsx](../thestill/web/frontend/src/pages/TopPodcasts.tsx).
  Extract to a small shared module to avoid duplication.

### Reused vs. new

| Concern | Status |
|---|---|
| Add-podcast pipeline | **Reused** (`POST /api/commands/add`) |
| Top-podcasts query | **Extended** (`?q=` + `is_following`) |
| Modal component | **Rewritten in place** — same file, same export |
| Flag/region helpers | **Extracted** from `TopPodcasts.tsx` to a shared module |
| Follow concept | **Naming clarification only** (UI says "Follow"; backend is the same `add` flow) |

## Data model

No schema changes. Everything we need is already keyed on `rss_url`,
which is `UNIQUE` in both `top_podcasts` and `podcasts`.

The `is_following` join is computed at query time:

```sql
-- Pseudocode shape; the actual query stays in get_top_podcasts(),
-- with a LEFT JOIN onto podcasts + a WHERE the user follows it.
SELECT r.rank, p.name, p.artist, p.rss_url, p.apple_url,
       p.youtube_url, c.name AS category, r.source_genre,
       CASE WHEN pf.user_id IS NOT NULL THEN 1 ELSE 0 END AS is_following
FROM top_podcast_rankings r
JOIN top_podcasts p ON p.id = r.top_podcast_id
LEFT JOIN categories c ON c.id = p.category_id
LEFT JOIN podcasts up ON up.rss_url = p.rss_url
LEFT JOIN podcast_followers pf
       ON pf.podcast_id = up.id AND pf.user_id = ?
WHERE r.region = ? AND (q-clause)
ORDER BY r.rank ASC LIMIT ?
```

`user_id = NULL` (anonymous) makes the LEFT JOIN miss for everyone →
`is_following = 0` for all rows. No special-casing needed.

## API

### `GET /api/top-podcasts`

**New query params**:

- `q: string` (optional, 1–100 chars after trim) — case-insensitive
  substring match against `name` and `artist`.

**New response field per row**:

```json
{
  "rank": 1,
  "name": "...",
  "artist": "...",
  "rss_url": "...",
  "apple_url": "...",
  "youtube_url": "...",
  "category": "...",
  "source_genre": null,
  "is_following": false
}
```

`is_following` is always present, defaulting to `false` for anonymous
callers. Existing top-podcast page consumers tolerate extra fields, so
the type extension is non-breaking.

### `POST /api/commands/add`

Unchanged. The modal calls it with `{url: rss_url}` whether the source is
the search list or a manually-typed URL.

## Free-tier gate seam (deferred)

Spec'd here for context, not implemented in this PR.

When subscriptions land:

1. Add `User.tier: 'free' | 'paid'` (or similar billing-driven flag).
2. In `POST /api/commands/add`, before enqueuing:

   ```python
   if user.tier == 'free' and not repository.is_top_podcast_url(url):
       raise HTTPException(403, "Upgrade to add custom feeds")
   ```

3. Frontend reads `user.tier` from `/api/auth/status` and either hides
   or visually demotes the URL-paste path for free users.

The modal redesign in this spec already keeps the search list and URL
input on visually distinct branches, so the gate-imposed UI variation
is small.

## UX details

- **Modal width**: matches the existing modal (no growth) — list rows
  fit in the same width.
- **Keyboard**: `Esc` closes; `↑/↓` cycles list rows; `Enter` follows
  the highlighted row, or in URL mode, submits the Add.
- **Loading**: skeleton placeholder rows for the first fetch on open;
  inline shimmer on `q` changes.
- **Errors**: search fetch errors render inline ("Couldn't load suggestions
  — paste an RSS URL above"); add errors render per-row (red `Retry`).
- **Region change link**: anchor to `/settings`, opens in same window;
  closes the modal first to avoid a stale list when the user returns.
- **Don't see it?**: persistent helper text under the list:
  *"Don't see it? Paste the RSS URL above."* — the URL field is the
  same input, so this is literal.
- **First-run empty state**: when the user navigates to `/podcasts`
  with zero follows, the page itself renders an empty-state card whose
  primary action opens this modal pre-loaded with top-10. No new modal,
  no separate wizard.

## Edge cases

- **Region has no data** (e.g. user picks `de` before German CSV ships).
  The endpoint already falls back to the first available region; the
  modal renders that region with the "Change region" link. The badge
  honestly says "Top in 🇺🇸 US" even if user picked DE — matches the
  Top page's existing behaviour.
- **User pastes a URL that's already in `top_podcasts`.** The URL-mode
  Add succeeds normally; the row in the list (if visible) flips to
  `Following ✓` on the next render. We don't try to be clever — adding
  via URL and clicking Follow on a top row are the same backend call.
- **User is already following everyone in the top 10.** Empty state
  still renders the list with all `Following ✓` badges. We could detect
  and show a different empty state ("You follow all the top 10!") but
  it's a 1-in-1000 state — skip until someone asks.
- **Whitespace-only `q`.** Treated as empty; we fall back to the
  rank-ordered top-10.
- **Very long `q`** (>100 chars). Rejected at the API layer with a 400.
- **Concurrent typing**. Old fetches are aborted via `AbortController`
  on each new keystroke after debounce.
- **Search match in non-current region**. Out of scope. We only ever
  search the resolved region. Cross-region discovery is what the
  region switcher in `/top` is for.

## Testing

### Unit (backend)

- `SqlitePodcastRepository.get_top_podcasts(region, q=...)` — case-insensitive
  match on `name`, `artist`, both, neither; respects `limit`; preserves
  rank order; returns empty for unknown region.
- `is_following` flag — true when the user follows a podcast whose
  `rss_url` equals a top-podcast's `rss_url`; false otherwise; `None` user
  → all false.

### Integration (HTTP)

- `GET /api/top-podcasts` with `q=` returns filtered list.
- Anonymous and authenticated callers see `is_following=false` /
  user-correct values respectively.
- `q` validation: empty → falls through; >100 chars → 400.

### Frontend

- `parseAddInput` correctly classifies `''`, `' '`, `'rest'`, `'http://x'`,
  `'feeds.com/x'`, `'rss://example'`.
- Debounce: typing 5 keys in <250 ms triggers 1 fetch.
- Already-followed rows render `Following ✓` and have no click handler.
- Keyboard navigation: `↑/↓/Enter` selects + follows.
- AbortController: stale responses don't overwrite newer ones.

### Manual smoke

- Empty modal → top 10 visible.
- Type "rest" → list narrows to "Rest Is History" / "Rest Is Politics".
- Click Follow → row turns to "Following ✓"; podcast appears on
  `/podcasts` page.
- Paste an RSS URL → list disappears, "Add this feed" row shows up,
  Add works.
- Change region in Settings → reopen modal → list reflects new region.

## Implementation phases

1. **Backend (`?q=` + `is_following`)**
   - Extend `get_top_podcasts` with optional `q` and the `is_following`
     join.
   - Extend `GET /api/top-podcasts` query params + response.
   - Tests for query, validation, follow-state.

2. **Frontend search modal**
   - Extract flag/region helper from `TopPodcasts.tsx`.
   - Rewrite `AddPodcastModal`: parse input, debounced fetch, list
     render, follow/add forking, keyboard nav.
   - Wire empty-state card on `/podcasts` to open modal pre-loaded.

3. **Polish**
   - Loading skeletons.
   - Error states (list-level + row-level).
   - Manual smoke + accessibility (focus management, ARIA on list).

## Out of scope (followups)

- **Genre/category filter chips** in the modal. Cheap to add later
  (data already there); decide once we see whether users actually
  search by genre vs. by name.
- **Recently followed-by-friends / collaborative-filter recs.**
  Requires multi-user social graph. Spec when needed.
- **Server-side typo tolerance.** Re-evaluate when search misses become
  a measurable activation bottleneck.
- **Free-tier gate enforcement.** Tracked above; ships with
  subscriptions.
