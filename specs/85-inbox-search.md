# Inbox Search

> **Status:** 💡 Proposal
> **Created:** 2026-09-24
> **Author:** Product & Engineering
> **Related:** [#29 per-user-inbox-fanout](29-per-user-inbox-fanout.md), [#28 corpus-search-and-entities](28-corpus-search-and-entities.md), [#52 inbox-reader-overlay](52-inbox-reader-overlay.md), [#84 scheduled-only-briefings](84-scheduled-only-briefings.md)

---

## Executive Summary

The inbox is a single reverse-chronological list with no way to narrow it.
One local inbox on 2026-09-24 held 998 non-dismissed rows. Finding a specific
delivered episode means scrolling and paging through "Load older" until the
title happens to catch the eye, and the title is often not the thing the user
remembers: the Karpathy episode that prompted this spec is titled "Deep Dive
into LLMs like ChatGPT"; the name the user searched for lives on the podcast.

This spec adds a search box to `/inbox` that filters the user's own
deliveries by text, server-side, with the existing cursor pagination and
state semantics untouched. It is a **filter on my inbox**, not another
entry point to corpus search: the answer to "where is that episode I was
sent?" is a row in this list, and the result must be that row, in its inbox
state, with its inbox actions.

## Problem

1. **No narrowing at all.** `GET /api/inbox` takes `state`, `limit` and a
   `before` cursor. The UI exposes none of them; the page renders whatever
   the default view returns, newest delivery first, 50 at a time.
2. **The corpus search does not answer the question.** ⌘K and `/search`
   ([#28](28-corpus-search-and-entities.md)) search transcripts, quotes and
   entities across every podcast the deployment tracks. They will find the
   episode, but land on the episode page, not the inbox row, and they rank a
   quote about the subject above the episode the user was actually sent.
3. **Titles are not how people remember deliveries.** Podcast name, guest
   name and the gist of the description are at least as likely to be the
   recalled handle. A title-only match would have missed the motivating case.

## Design

### Scope of a match

A query matches an inbox row when **every whitespace-separated token**
appears, case-insensitively, as a substring of at least one of:

- the episode title
- the podcast title
- the episode description (plain text, not `description_html`)

Tokens are ANDed; fields are ORed within a token. `karpathy llm` matches the
motivating episode (podcast title carries "Karpathy", episode title carries
"LLMs"). Match is substring, not prefix, so `pathy` also hits — an inbox is
small enough that recall beats precision here.

Not matched: transcript or summary text (that is corpus search), entity
names unless they appear in the fields above, speaker labels.

### API

`GET /api/inbox` gains one parameter:

| Param | Type | Rules |
|-------|------|-------|
| `q` | string | Optional. Trimmed; empty after trim = no filter. Max 200 chars (400 → `bad_request`). Split on whitespace into at most 8 tokens; extra tokens are ignored, not rejected. |

Everything else is unchanged: `state` still filters, `before` still pages on
`delivered_at`, `limit` still caps, the default view still excludes
`dismissed`, and the response shape (`items`, `count`, `next_before`) is the
same. Search is a `WHERE` clause on the existing query, so it composes with
the cursor for free and `next_before` keeps working across a filtered list.

`GET /api/inbox/unread-count` is untouched: the badge counts the inbox, not
the search.

### Repository

`InboxRepository.list_items` gains `query_tokens: Sequence[str] = ()`.
Both backends add, per token:

```sql
(e.title ILIKE %s OR p.title ILIKE %s OR COALESCE(e.description, '') ILIKE %s)
```

with the token wrapped in `%…%` and `%`, `_` and `\` escaped (`ESCAPE '\'`).
SQLite uses `LOWER(x) LIKE LOWER(?)` because its `LIKE` is only
case-insensitive for ASCII. The join to `episodes` and `podcasts` already
exists in the query; no new tables, columns or indexes.

**Why no full-text index.** A user's inbox is bounded (hundreds to low
thousands of rows) and already narrowed by `i.user_id` before the `LIKE`
runs. Sequential `ILIKE` over that set is sub-millisecond territory.
Postgres `tsvector` or `pg_trgm` would add a migration and a SQLite
divergence for no measurable gain. Revisit if a p95 over 100 ms shows up in
the request log.

### Frontend

- A search input in the inbox header, left of the Import button, placeholder
  "Search your inbox". On phones it collapses to an icon that expands the
  field full-width above the list, matching the Import button's
  `iconOnlyMobile` pattern.
- Input is debounced 250 ms before it hits the query; Escape clears it.
- The query lives in the URL as `?q=` so Back restores it and the scroll
  position, per the list-page convention (`useSearchParams`, not local
  state). This also makes a filtered inbox linkable.
- `useInboxInfinite` takes `q` and includes it in the query key, so
  changing the search resets pagination rather than appending pages from
  two different filters.
- Polling (the 5 s refetch while an episode is processing) is disabled while
  `q` is non-empty. A search is a lookup, not a live view.
- Count line reads "N matching" instead of "N delivered" while filtering.
- Empty state while filtering: "Nothing in your inbox matches *q*" with two
  links: **Clear search** and **Search everything →** (`/search?q=…`), the
  hand-off to corpus search for the case where the episode was never
  delivered to this user.
- `BriefingCard` is hidden while filtering. It is not a search result.
- Rows are the existing `InboxRow`: same state styling, same reader overlay
  on click ([#52](52-inbox-reader-overlay.md)), same mark-read-on-view.

### MCP and CLI

None. The remote MCP server ([#78](78-remote-mcp-access.md)) has no inbox
tools today and this spec does not add one.

## Non-Goals

- **Semantic or transcript search inside the inbox.** Corpus search owns
  that; the empty-state link hands off to it.
- **Podcast or date filter chips.** Worth doing, but a separate small spec:
  they change the header layout more than a text box does, and `podcast:`
  can be typed into this box once the parser from `CommandBar` is shared.
- **State filter UI** (Unread / Saved / Dismissed tabs). The API already
  supports it; exposing it is orthogonal and can ride on the same header
  change later.
- **Saved searches, highlighting of matched terms.**

## Failure Modes

| # | Mode | Guard |
|---|------|-------|
| 1 | `LIKE` wildcards in the query (`50%`, `_`) match everything | Escape `%`, `_`, `\` and pass `ESCAPE '\'`; contract test with a literal `%` in a title |
| 2 | Long or token-bomb query | 200-char cap → 400; tokens beyond 8 ignored; both tested |
| 3 | A request per keystroke | 250 ms debounce; query key includes `q` so React Query dedupes identical strings |
| 4 | Paging across a changed filter | `q` in the query key resets `pages`; `next_before` is only ever consumed with the `q` it was issued for |
| 5 | Search silently narrowing the unread badge | Badge endpoint untouched; test asserts `unread-count` ignores `q` |
| 6 | Non-ASCII case folding differs between backends | Postgres `ILIKE` and SQLite `LOWER()` both fold ASCII; document that SQLite is ASCII-only, add a contract test with an accented title that passes on both by using the lower-cased literal |
| 7 | Filter lost on Back | `?q=` in URL; `useSearchParams`; covered by the list-page scroll/filter convention test |

## Implementation Plan

### Phase 1 — API + repository

- [ ] `list_items(query_tokens=…)` on `InboxRepository`, Postgres and SQLite
      ([inbox_repository.py](../thestill/repositories/inbox_repository.py),
      [postgres_inbox_repository.py](../thestill/repositories/postgres_inbox_repository.py),
      [sqlite_inbox_repository.py](../thestill/repositories/sqlite_inbox_repository.py)).
- [ ] `InboxService.list(q=…)` tokenises, caps, escapes
      ([inbox_service.py](../thestill/services/inbox_service.py)).
- [ ] `GET /api/inbox?q=` ([api_inbox.py](../thestill/web/routes/api_inbox.py)).
- [ ] Contract tests on both repositories: title / podcast / description
      hit, multi-token AND, case fold, wildcard escape, composes with `state`
      and `before`, no match → empty. Route test for the 400 and the trim.
- [ ] `docs/web-server.md` and `specs/02-api-reference.md` entry.

### Phase 2 — Inbox UI

- [ ] Search input + mobile collapse in [Inbox.tsx](../thestill/web/frontend/src/pages/Inbox.tsx).
- [ ] `getInbox({ q })` and `useInboxInfinite({ q })` with `q` in the key
      ([client.ts](../thestill/web/frontend/src/api/client.ts),
      [useApi.ts](../thestill/web/frontend/src/hooks/useApi.ts)).
- [ ] URL-bound query, debounce, Escape, polling off, count copy, empty
      state with the two links, `BriefingCard` hidden while filtering.
- [ ] `Inbox.test.tsx`: typing updates the URL and the hook args; empty
      state renders both links; Back restores `q`.

### Phase 3 — optional follow-ups (separate specs)

- State filter tabs; podcast / date chips; shared `podcast:` token parser
  with `CommandBar`.

## Open Questions

1. **Should dismissed rows be searchable by default?** The default view
   hides them and the motivating case did not involve one. Proposal: no;
   `state=dismissed&q=` still works for the rare case.
2. **Match on guest entity names?** `episodes.guest_entity_ids` exists.
   Joining entity names into the match would catch "the one with Karpathy
   as a guest" when neither title mentions him. Cheap to add later as a
   fourth `OR` branch once the field is reliably populated (see the entity
   data-quality notes in [#81](81-live-wikidata-entity-linking.md)).

## Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-09-24 | Filter the inbox server-side rather than client-side over loaded pages | The row the user wants is usually not on the pages already loaded; client-side filtering of 50 rows would look like "no results" |
| 2026-09-24 | Substring `ILIKE` over three fields, no FTS index | Per-user set is small and already indexed by `user_id`; avoids a migration and a SQLite divergence |
| 2026-09-24 | Include podcast title and description in the match | The motivating episode is unfindable by episode title alone |
| 2026-09-24 | Hand off to corpus search from the empty state, not merge results | Keeps inbox search a filter with inbox semantics; corpus search already exists for the other question |
