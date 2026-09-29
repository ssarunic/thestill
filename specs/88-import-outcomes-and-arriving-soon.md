# Import Outcomes and Arriving Soon

> **Status:** 🚧 Implemented on `feat/88-import-outcomes-and-arriving-soon` (2026-09-29), unmerged
> **Created:** 2026-09-29
> **Author:** Product & Engineering
> **Related:** [#29 per-user-inbox-fanout](29-per-user-inbox-fanout.md), [#31 import-arbitrary-episodes](31-import-arbitrary-episodes.md), [#36 per-user-digest-from-inbox](36-per-user-digest-from-inbox.md), [#52 inbox-reader-overlay](52-inbox-reader-overlay.md), [#85 inbox-search](85-inbox-search.md)

---

## Executive Summary

Importing a link is the inbox's only "add this episode" gesture. When the
episode already exists in the corpus the modal says "Already in your inbox",
links to `/inbox`, and the user lands at the top of a list in which the row
is nowhere to be seen. On 2026-09-29 that happened with a 20VC episode
published on 2026-05-11: the row had been delivered in May, so it sat far
below the first page, and the modal gave no way to reach it.

The heading is also wrong in the other direction. It fires on "the episode
already existed", not "your inbox already had it", so a fresh delivery of an
old, fully summarised episode reads as a duplicate even though a new row
was just created.

This spec does four things:

1. Makes the import modal answer *what now* for each of the three real
   outcomes (new episode, existing episode newly delivered, row already in
   the inbox), with a direct "Read now" / "Open episode" action instead of
   a jump to the list, and mail-style in-place actions (restore, save) for
   a row that is already there.
2. Gives saved and unread rows a home: an **All / Unread / Saved** view
   switch on `/inbox`, so "save it for later" leads somewhere.
3. Adds a read-only **Arriving soon** strip above the inbox for followed
   podcasts' episodes that are in the pipeline but not yet published, so
   "where is the episode from this morning?" has an answer that does not
   involve the queue viewer.
4. Puts a **Send to my inbox** button on every episode page. Non-admins
   currently see no action at all on an unprocessed episode; the admin-only
   "Transcribe" button is the wrong verb for a reader anyway. Delivering to
   the inbox already starts the pipeline on every other path, so the user
   states an intent to read and the system decides whether to process.
   The `ad_hoc` inbox source, reserved in the schema since #31 with no
   producer, is what this button writes.

Two things deliberately do not change. Delivery is immutable: a row's
`delivered_at` is when the episode reached the user and nothing the user
does later moves it, exactly as mail clients treat arrival time. And
follow fan-out still delivers on publish: a delivered row means a readable
summary, which briefings ([#36](36-per-user-digest-from-inbox.md)) and the
unread badge depend on.

## Problem

1. **One heading for two outcomes.** `POST /api/imports` returns
   `deduplicated = not episode_created`
   ([api_imports.py:84](../thestill/web/routes/api_imports.py#L84)). The
   modal maps it to "Already in your inbox"
   ([ImportEpisodeModal.tsx:133](../thestill/web/frontend/src/components/ImportEpisodeModal.tsx#L133)).
   The payload also carries `inbox_created`, which is the flag that
   actually answers the question, and nothing reads it.
2. **The only exit is the list.** "Go to inbox" closes the modal onto the
   top of `/inbox`. The row the user wants may be months down, or hidden
   because it was dismissed. The episode's identity is known at that point;
   nothing uses it.
3. **Saved has no view.** A saved row shows a bookmark glyph in the main
   list and nothing else. `GET /api/inbox?state=` exists and the client
   already passes `state`
   ([client.ts](../thestill/web/frontend/src/api/client.ts)), but the page
   never sets it. Marking an old episode saved does not make it findable.
4. **Importing an existing, unprocessed episode does not start the
   pipeline.** The enqueue in
   [import_service.py:894](../thestill/services/import_service.py#L894) is
   gated on `episode_created`. An episode that was discovered by feed
   refresh but never processed, or whose earlier import was dead-lettered,
   gets an inbox row with a "Downloading…" pill that never advances.
   Publish fan-out already guards against this with `_ensure_pipeline`
   ([inbox_service.py](../thestill/services/inbox_service.py)); import
   does not.
5. **Non-admins have no action on an unprocessed episode.** The pipeline
   button returns null for them
   ([PipelineActionButton.tsx:409](../thestill/web/frontend/src/components/PipelineActionButton.tsx#L409))
   on the premise that the pipeline advances automatically, which holds only
   for episodes already flowing. On a discovered episode of a podcast they
   do not follow, the only routes are following the whole show (which seeds
   the two newest episodes) or pasting the episode's Apple or YouTube link
   into Import from the inbox page.
6. **No signal for episodes on their way.** Followed podcasts deliver on
   publish only ([task_handlers.py:779](../thestill/core/task_handlers.py#L779)).
   Between refresh discovering an episode and summarize finishing, the
   inbox shows nothing, and the user cannot tell "not out yet" from "stuck"
   from "not followed" without opening the podcast page or the queue.

Imports are not affected by problem 6: an import row lands immediately and
`deriveProgress` in [Inbox.tsx](../thestill/web/frontend/src/pages/Inbox.tsx)
already renders a progress pill until it is summarised. The inbox is not
strictly post-transcription today; only the follow path is.

## Design

### Delivery is immutable

A row is created once, by fan-out, follow-seed or import, and its
`delivered_at` never changes afterwards. Every later user action is a
state transition on the row in place: read, unread, saved, dismissed.

This is the mail model. Marking a message unread, starring it or moving it
back from the archive leaves it at its arrival date; only a new arrival
lands on top. It is also what the rest of the system assumes:
`delivered_at` is the list's cursor and the briefing window key
([#36](36-per-user-digest-from-inbox.md)), so a row that could be re-dated
could be counted in two briefings and would make a past briefing's window
non-reproducible.

The inbox's unique `(user_id, episode_id)` key means an episode cannot
arrive twice, so "import it again to get it on top" is not available
either. The answer to "I want to get back to this" is therefore: open it
now, or save it and find it in the Saved view. "Send to my inbox"
stays consistent with this rule because it creates a row only where none
exists, which is a genuine arrival; on an existing row it shows the row's
state and offers the same in-place actions.

### Import outcome

The import response gains an explicit outcome and enough identity to link
straight to the episode. `deduplicated` is removed; its only consumer is the
modal and its tests.

```json
{
  "import": {
    "outcome": "new_episode | added_existing | already_in_inbox",
    "episode_id": "…",
    "episode_slug": "…",
    "episode_state": "summarized",
    "episode_failed": false,
    "inbox_entry": { "state": "read", "delivered_at": "2026-05-11T09:12:00+00:00", "…": "…" },
    "parent": { "id": "…", "title": "…", "slug": "…" }
  }
}
```

- `new_episode`: `episode_created` is true. Unchanged behaviour.
- `added_existing`: episode existed, `inbox_created` is true.
- `already_in_inbox`: neither is true. `inbox_entry` is the original row,
  including its `source`, `state` and `delivered_at`.

`episode_state` and `episode_failed` come from the `Episode` model's
computed `state` / `is_failed` so the modal and the inbox row derive the
same progress label from the same rule. They are read through the inbox
JOIN (`InboxRepository.get_item`), not `get_episode`: the latter builds a
full `Podcast`, whose `rss_url` validator rejects the synthetic
`audio-imports` parent's `synthetic://` URL.

### Modal copy and actions

The modal lives on `/inbox`, so "open" means the reader overlay above the
still-mounted list ([#52](52-inbox-reader-overlay.md)): navigate to the
episode href with `backgroundLocation` set to the current location, exactly
as `InboxRow` does. The href is
`/podcasts/{parent.slug || parent.id}/episodes/{episode_slug || episode_id}`;
imports under the synthetic `audio-imports` parent fall back to ids, which
the route already accepts.

| Outcome | Heading | Body | Primary | Secondary |
|---------|---------|------|---------|-----------|
| `new_episode` | Importing — this may take a few minutes | title, source handle, follow CTA (unchanged) | Go to inbox | Close |
| `added_existing`, summarised | Added to your inbox | "It's already transcribed and summarised." | Read now | Close |
| `added_existing`, in progress | Added to your inbox | progress pill from `episode_state` ("Transcribing…") | Go to inbox | Close |
| `added_existing`, failed | Added to your inbox | "Processing failed earlier. You can retry it from the episode page." | Open episode | Close |
| `already_in_inbox` | Good news — this is already in your inbox | "Delivered 11 May" plus the state sentence and action below | Open episode | per state |

The `already_in_inbox` sentence and secondary action depend on the row's
state. Every action is the existing
`POST /api/inbox/{episode_id}/state`; the row stays where it is.

| Row state | Sentence | Secondary action |
|-----------|----------|------------------|
| `unread` | "You haven't read it yet." | Save for later → `saved` |
| `read` | "You've read it." | Save for later → `saved` |
| `saved` | "It's in your saved items." | none; the sentence links to the Saved view |
| `dismissed` | "You dismissed it, so it's hidden from the main list." | Restore to inbox → `unread` |

"Restore to inbox" is Gmail's move-to-inbox for an archived thread: the
row reappears in the default view at its original date. After either
action the modal invalidates the `inbox` and `inbox/unread-count` queries
and closes; when the action was "Save for later" it closes onto the Saved
view so the user sees where the row went.

The follow-channel CTA stays for `new_episode` only, as today.

### All / Unread / Saved view

A three-way segmented control in the `/inbox` header, bound to a `?view=`
URL param (absent for All), following the URL-bound filter pattern from
[#85](85-inbox-search.md) so Back restores it.

- **All**: today's default, `state` unset, dismissed rows hidden.
- **Unread**: `state=unread`.
- **Saved**: `state=saved`.

The search box composes with the view (`state` and `q` already compose
server-side). `BriefingCard` renders on All only. The count copy names
the view: "12 unread", "5 saved". Empty states: "Nothing unread. Nice." and
"Save an episode from its page or the import dialog to find it here."

This is the "state filter tabs" item from #85 Phase 3, pulled forward
because "Save for later" needs a destination to be an honest offer. A
Dismissed view is not added; `state=dismissed` still works for anyone
who needs it.

### Send to my inbox

`POST /api/inbox/{episode_id}` (authenticated, not admin) finds or creates
the caller's row for the episode with `source='ad_hoc'`, then runs the same
ensure-pipeline guard as publish fan-out. It returns `201` with
`{ entry, created: true }` on a new row and `200` with
`{ entry, created: false }` when one already existed. `404` when the
episode does not exist, checked with a plain existence read before the
insert so a bad id never reaches the foreign key. The page already holds
the episode, and the send invalidates the episode query so the live
refresh picks up a newly started pipeline. `GET /api/inbox/{episode_id}`
returns `{ entry | null }` so the episode page can render the button's
state on load.

The import dialog's `added_existing` and `already_in_inbox` outcomes are
the same find-or-create with a different source tag. Both paths go through
one enqueue helper (`enqueue_pipeline_for` in the inbox service module),
and the two surfaces share one "inbox state" presentation
(`InboxStatePanel`):

| Row | Episode page | Import dialog |
|-----|--------------|---------------|
| none | **Send to my inbox** button | n/a (a row always exists after import) |
| `unread` / `read` | "In your inbox" + Save for later | same sentence and action |
| `saved` | "Saved" + link to Saved view | same |
| `dismissed` | "Dismissed" + Restore to inbox | same |

On the episode page the control sits below the header, where the admin
stage controls sit, rather than in the icon-only action row. After
a successful send the button flips to the in-inbox state and, when the
episode is not yet summarised, the page's existing live refresh picks up
the pipeline progress. Admins see this button too, ahead of the stage
controls: admins are also readers. The per-user import counter counts
`ad_hoc` rows alongside `import` so any later quota sees both.

Cost exposure is unchanged: import already lets any authenticated user
start a transcription. The pipeline endpoints themselves stay admin-only.

### Import ensures the pipeline

`ImportService.import_url` calls the same guard publish fan-out uses when
`episode_created` is false: `get_unqueued_unprocessed_episodes([episode_id])`
and `enqueue_full_pipeline` for any hit. The selector already excludes
episodes with artefacts, with a failure, or with a task row, so a
summarised or in-flight episode is a no-op and a failed one is left for the
explicit retry path (the modal now says so). Enqueue failures are logged
and never fail the import, matching `_ensure_pipeline`.

### Arriving soon

`GET /api/inbox/arriving?limit=5` returns, for the current user, episodes
that satisfy all of:

- the podcast is one the user follows (`get_followed_podcast_ids`);
- `published_at IS NULL` and no failure recorded;
- the episode has an active queue task (pending / processing / retry) for
  any stage;
- the user has no inbox row for it (an imported in-flight episode already
  shows in the list with its pill).

Ordered by `COALESCE(pub_date, created_at) DESC`, capped at `limit`
(max 20). Each row carries episode id, slug, title, image, podcast title
and slug, `episode_state`, and `pub_date`. The response also carries
`total` so the strip can say "and 4 more" without paging.

"Has an active task" is the in-flight test rather than "discovered in the
last N days" because it is the queue's own definition and needs no
tunable. An episode with no task and no failure is a discovered orphan,
which is a refresh or backlog concern
([#42](42-robustness-and-failure-mode-hardening.md)), not something to
advertise as arriving.

UI, in [Inbox.tsx](../thestill/web/frontend/src/pages/Inbox.tsx):

- A compact strip between `BriefingCard` and the list on the All view:
  "Arriving soon" with a count, collapsed by default to one line per
  episode showing artwork, podcast title, episode title and the progress
  pill from `deriveProgress`.
- Rows link to the episode page (plain navigation, not the overlay); that
  page already shows the pipeline stepper.
- Hidden on the Unread and Saved views, while a search filter is active,
  and when the response is empty.
- Polls every 30 s while non-empty, reusing the inbox's conditional-poll
  pattern; an episode disappears from the strip when publish fan-out
  delivers its real row, and the list refetch picks that row up.
- No read state, no unread count, no briefing eligibility, no search.

## Non-Goals

- Moving, re-dating or duplicating an existing inbox row. See "Delivery is
  immutable".
- Changing when follow fan-out delivers. Delivered still means readable.
- Scroll-to-row or highlight in the list after an import. The list is
  cursor paged and the row may not be loaded; a direct open is better.
- A per-episode search parameter on `GET /api/inbox`.
- Exposing pipeline stage controls to non-admins. "Send to my inbox" is the
  reader's verb; the stages stay behind `require_admin`.
- A Dismissed view. Restore from the import dialog covers the case that
  prompted this spec.
- Retrying failed episodes from the modal. The episode page owns retry.

## Failure Modes

Checked against the catalogue in
[#42](42-robustness-and-failure-mode-hardening.md).

- **Errors as empty results.** The arriving-soon request failing must not
  be rendered as "nothing arriving", and a failed Saved-view request must
  not render the "save an episode to find it here" empty state. Both hooks
  expose `isError`; the strip hides and the list shows its error state,
  with the failure logged server-side with `user_id`.
- **Silent degradation.** The ensure-pipeline call inside import logs at
  warning with `episode_id` when enqueue fails, never swallows silently.
- **Consistent-mock tests.** Modal tests drive the real client against a
  mocked `fetch` with the three real payload shapes, not a hand-built
  `result` object, so a payload rename cannot pass while the UI breaks.
- **Path drift.** The view param and the search param live on the same
  URL; a test asserts that switching view keeps `q` and that clearing the
  search keeps the view, so the two URL-bound filters cannot clobber each
  other.

## Implementation Plan

### Phase 1 — Import outcome and modal

- [x] `ImportResult` exposes `outcome`, `episode_slug`, `episode_state`,
      `episode_failed`; route emits them and drops `deduplicated`
      ([import_service.py](../thestill/services/import_service.py),
      [api_imports.py](../thestill/web/routes/api_imports.py)).
- [x] Ensure-pipeline guard on the `episode_created == False` path, with
      the warning log on enqueue failure.
- [x] `ImportPayload` type, three-way `ImportSuccess`, reader-overlay
      navigation for "Read now" / "Open episode", "Save for later" and
      "Restore to inbox" through the existing `setInboxState`
      ([types.ts](../thestill/web/frontend/src/api/types.ts),
      [ImportEpisodeModal.tsx](../thestill/web/frontend/src/components/ImportEpisodeModal.tsx)).
- [x] Tests: route test per outcome; service test that a discovered,
      unqueued episode is enqueued on dedup import and a summarised one is
      not; modal tests for the five outcome rows and the four state rows,
      replacing the current "dedup hit" test.
- [x] `docs/web-server.md` import row.

### Phase 2 — All / Unread / Saved view

- [x] `?view=` param bound with `useSearchParams` beside `?q=` (the #85
      hook debounces free text, which a segmented control does not need);
      `state` passed into `useInboxInfinite`, already in its query key
      ([useApi.ts](../thestill/web/frontend/src/hooks/useApi.ts)).
- [x] Segmented control in the header, count copy per view, empty states,
      `BriefingCard` and Arriving soon on All only.
- [x] Tests: view switch sends the right `state`; view survives Back and
      composes with `q`; empty states per view; error state is not the
      empty state.

No backend change: `GET /api/inbox?state=` and its contract tests already
exist.

### Phase 3 — Send to my inbox

- [x] `InboxService.deliver_to_user(user_id, episode_id, source)` owning
      find-or-create plus the pipeline guard; `ImportService` shares the
      enqueue helper rather than depending on `InboxService`.
- [x] `GET /api/inbox/{episode_id}` and `POST /api/inbox/{episode_id}`
      ([api_inbox.py](../thestill/web/routes/api_inbox.py)); import
      counter includes `ad_hoc`.
- [x] `InboxActionButton` (episode page action row, both page and overlay)
      sharing its in-inbox presentation with the import dialog; hooks for
      entry lookup, send, and state change.
- [x] Tests: service test that an ad-hoc send of a discovered episode
      enqueues and of a summarised one does not; route tests for 201 / 200
      / 404; component tests for the four row states and the send flow.

### Phase 4 — Arriving soon

- [x] Repository query joining followers, episodes and active tasks, on
      both backends; `InboxService.arriving(user_id, limit)`.
- [x] `GET /api/inbox/arriving`.
- [x] `useArrivingSoon` hook with conditional poll; `ArrivingSoon` strip in
      [Inbox.tsx](../thestill/web/frontend/src/pages/Inbox.tsx), hidden
      off the All view and while filtering.
- [x] Contract tests: followed-only, excludes published, excludes failed,
      excludes episodes with no active task, excludes episodes already in
      the user's inbox, ordering and cap. UI tests: strip absent on empty
      and on error, present with pills, hidden while `?q=` or `?view=` is
      set.
- [x] `docs/web-server.md` row.

## Known Gaps

- Bare-audio imports live under the synthetic `audio-imports` parent, whose
  `synthetic://` feed URL fails `Podcast` validation. Every lookup that
  builds a `Podcast` (`get_episode`, `get_episode_by_slug`) raises for
  those episodes. Confirmed on SQLite on 2026-09-29: `get_episode_by_slug`
  raises `ValidationError` for a fresh bare-audio import, so its episode
  page cannot load. Postgres not checked. This spec routes around it; the
  fix belongs in its own change.
- The `docs/web-server.md` import row claimed `201`; the route returns the
  default `200`. The row now links to `imports.md` instead of restating it.

## Open Questions

1. **Should "Save for later" on an unread row keep it unread?** The
   states are exclusive today (`saved` replaces `unread`), and the Saved
   view makes that acceptable. Splitting saved into a flag orthogonal to
   read state is a schema change and out of scope; noting it because
   mail keeps star and unread independent.
2. **Should the Unread view show the briefing card?** Proposal: no; the
   card is a summary of deliveries, and the view is a triage list.
3. **Arriving soon for imports with a failed pipeline.** Those rows sit in
   the main list with a red "Failed" pill already, so nothing extra here.
   Whether the inbox row should offer retry inline is a separate question.

## Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-09-29 | Replace `deduplicated` with a three-value `outcome` | The current flag conflates "episode existed" with "row existed"; the modal needs the distinction and the second flag already exists |
| 2026-09-29 | Open the episode from the modal instead of pointing at the list | The row may be unloaded pages down or hidden as dismissed; the episode identity is in hand |
| 2026-09-29 | Delivery is immutable; no "move to top" and no redeliver endpoint | Mail never moves a message on a later action; `delivered_at` is the cursor and briefing window key, and re-dating would let one episode into two briefings. An earlier draft of this spec proposed a redeliver verb and was reversed on review |
| 2026-09-29 | Already-in-inbox actions are existing state transitions, in place | Restore (dismissed → unread) and Save reuse `POST /inbox/{id}/state`; nothing new to secure or migrate |
| 2026-09-29 | Pull the All / Unread / Saved view forward from #85 Phase 3 | "Save for later" without a Saved view is not an honest offer |
| 2026-09-29 | Keep follow fan-out on publish; add a read-only strip instead | Delivered rows drive unread counts and briefings; early delivery would leak failed and stalled episodes into both |
| 2026-09-29 | In-flight means "has an active queue task" | The queue already defines it; no freshness window to tune, and orphans stay a refresh concern |
| 2026-09-29 | Import runs the same ensure-pipeline guard as publish fan-out | Symmetry with the existing safety net; fixes the frozen "Downloading…" pill on dedup imports |
| 2026-09-29 | "Send to my inbox" for every user instead of exposing Transcribe | The reader's intent is to read, not to run a stage; delivery already implies processing on every other path, and the admin gate on the pipeline stays intact |
| 2026-09-29 | Import and the episode-page button share one delivery operation | Same find-or-create, same guard, same in-inbox presentation; two implementations would drift |
