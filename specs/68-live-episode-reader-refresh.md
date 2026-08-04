# Live Episode Reader Refresh — level-triggered pipeline sync

> **Status:** ✅ Phase 1 + 2 + 3 implemented (2026-08-03)
> **Created:** 2026-08-03
> **Updated:** 2026-08-04 (v6 — browser-verified; see §Revision history)
> **Author:** Engineering
> **Priority:** Medium (visible correctness bug: the reader shows stale "not yet
> available" state indefinitely after the pipeline finishes)
> **Related:** [#16 full-pipeline-and-failure-handling](16-full-pipeline-and-failure-handling.md)
> (introduced the `onTaskComplete` edge callback this spec replaces),
> [#52 inbox-reader-overlay](52-inbox-reader-overlay.md) (the reader is shared;
> the fix lands in both surfaces at once),
> [#28 corpus-search-and-entities](28-corpus-search-and-entities.md) (the
> entity branch that keeps running after `summarized`),
> [#41 llm-prohibited-content-fallback](41-llm-prohibited-content-fallback.md)
> (`N/A` summaries — a legitimate `has_summary && !available` steady state),
> [#21 episode-processing-indicator](21-episode-processing-indicator.md) (same
> problem class on list pages — adjacent, not covered here),
> [#42 robustness](42-robustness-and-failure-mode-hardening.md) (FM-4 silent
> degradation; this spec proposes FM-8, below)

---

## Executive Summary

Sit on an episode page while the pipeline runs it from `discovered` to
`summarized` and the reader never catches up. Transcription finishes, cleaning
finishes, summarization finishes — and the Summary tab still reads
*"Summary not yet available / This episode hasn't been summarized yet."* Only a
manual browser reload fixes it.

The episode query itself is **already live** — it inherits a 5 s poll from the
app-wide `QueryClient`. The transcript and summary queries explicitly opt out of
that poll (`refetchInterval: false`, `staleTime: 60_000`), so the only thing
that can advance them is a bridge: an *edge-triggered* callback that fires only
if the client observes the exact moment the episode's task list goes from
"something active" to "nothing active". That bridge is broken in at least three
ordinary ways. When it fails, the live episode query keeps reporting
`state: 'summarized'` while the frozen summary query keeps serving a cached
`available: false` — the same server, two caches, disagreeing.

This spec replaces the bridge with a **level-triggered** one: content
invalidation derived from episode fields that the already-live query re-asserts
on every tick, rather than from a transition the client must witness. A missed
tick then costs latency, never correctness.

## Current behaviour

The app-wide client at [main.tsx:10-18](../thestill/web/frontend/src/main.tsx#L10-L18)
sets `staleTime: 5000` and `refetchInterval: 5000` for **every** query. Reader
queries landed as follows (line references are to the **current** file; the
cadences described are the pre-v4 behaviour this spec replaces):

| Query | Cadence before this spec | Source |
|---|---|---|
| `useEpisode` | **5 s poll** (inherits the global default; no local override) | [useApi.ts:176-194](../thestill/web/frontend/src/hooks/useApi.ts#L176-L194) |
| `useEpisodeTranscript` | **never** — local `refetchInterval: false`, `staleTime: 60_000` | [useApi.ts:196-205](../thestill/web/frontend/src/hooks/useApi.ts#L196-L205) |
| `useEpisodeSummary` | **never** — same overrides | [useApi.ts:241-253](../thestill/web/frontend/src/hooks/useApi.ts#L241-L253) |
| `useEpisodeTasks` | 2 s **only while its own last response had an active task**, else stopped | [useApi.ts:510-540](../thestill/web/frontend/src/hooks/useApi.ts#L510-L540) |

So `episode.state` tracks reality within ~5 s, and the content queries track
nothing at all. The sole bridge between them:

1. `PipelineActionButton`'s completion detector (deleted in v4) compared the
   current active task against a `useRef` of the previous one and called
   `onTaskComplete(stage)` on the `had → hasn't` edge.
2. `EpisodeReader.handleTaskComplete` (deleted in v4)
   invalidated `['episodes', podcastSlug, episodeSlug]` — a prefix of the
   transcript and summary keys, so all three refetch.

## Why the bridge fails

### F1 — The task poll wedges permanently in the gap between chained stages

`TaskWorker` marks a task complete at
[task_worker.py:671](../thestill/core/task_worker.py#L671), then performs two
more DB round-trips (`supersede_stale_tasks`,
`clear_episode_failure_for_stages`) before enqueueing the successor at
[task_worker.py:704](../thestill/core/task_worker.py#L704). The writes are not
atomic, so there is a real window in which the episode has **zero** active tasks
while the pipeline is still running.

A 2 s poll landing in that window returns an empty active set →
`refetchInterval` returns `false` → **the query stops and nothing restarts it**.
`handleTaskComplete` invalidates `['episodes', podcastSlug, episodeSlug]`, which
does *not* prefix-match `['episodes', 'tasks', episodeId]` (element `[1]`
differs), and `useQueuePipelineTask`'s `['episodes', 'tasks']` invalidation only
runs on a manual queue action. With a 5-stage chain there are four such windows;
any one of them ends the bridge for the session.

### F2 — The detector unmounts on the transition it exists to catch

[EpisodeReader.tsx:496](../thestill/web/frontend/src/components/EpisodeReader.tsx#L496)
renders `PipelineActionButton` only while
`!episode.is_failed && episode.state !== 'summarized'`. Because `useEpisode`
polls at 5 s independently, the new state routinely lands *before* the task poll
reports the completion — unmounting the component that holds
`prevActiveTaskRef`, along with any edge it had not yet reported.

### F3 — Background tabs pause the interval

React Query does not run `refetchInterval` for an unfocused tab unless
`refetchIntervalInBackground: true` (default `false`). Leave the tab during a
40-minute transcription — the expected behaviour — and the edge occurs with
nobody watching.

### The symptom confirms the diagnosis

`"Summary not yet available"` is the `default:` branch of
[SummaryViewer.tsx:49-54](../thestill/web/frontend/src/components/SummaryViewer.tsx#L49-L54),
reached when `episodeState` is not one of the in-flight states. Seeing it means
`useEpisode` holds `state: 'summarized'` — proof the episode poll is working —
while `useEpisodeSummary` still holds `available: false`. Exactly the shape F1–F3
predict.

### Proposed FM-8 for spec #42 — edge-triggered client state sync

> A client that syncs by detecting a *transition* (`had X` → `now !X`) is
> correct only if it observes every transition. Timers throttle, components
> unmount, tabs sleep, and the server's intermediate states are not atomic — so
> it will miss one. Sync instead on a level the server **re-asserts on every
> tick**, so a late or missed observation costs latency, not correctness.
> Two corollaries: (a) a self-referential poll predicate — *keep polling if the
> last poll said busy* — is a latch that can only fall open; (b) when two
> queries describe the same entity at different cadences, the slower one is not
> "cached", it is **wrong**, and something must reconcile them.

Worth adding to the [#42](42-robustness-and-failure-mode-hardening.md) catalogue
and review checklist; the pattern is not frontend-specific.

## Two terminal definitions

The pipeline does **not** stop at `summarized`.
[`_maybe_enqueue_next_stage`](../thestill/core/task_worker.py#L789-L847) always
chains the entity branch after a successful summarize, regardless of
`run_full_pipeline` or `target_state`
([task_worker.py:805-816](../thestill/core/task_worker.py#L805-L816),
[:830](../thestill/core/task_worker.py#L830)). That branch is **six** stages,
not three ([queue_manager.py:228-239](../thestill/core/queue_manager.py#L228-L239)):

```text
summarize → extract-entities → resolve-entities → reindex
          → rebuild-cooccurrences → compute-related → enrich-entities
```

`EntityBranchProgress`
([EpisodeReader.tsx:572](../thestill/web/frontend/src/components/EpisodeReader.tsx#L572))
consumes the *same* `useEpisodeTasks` query to render that progress, but
displays only the first three
([EntityBranchProgress.tsx:13-45](../thestill/web/frontend/src/components/EntityBranchProgress.tsx#L13-L45)).
The last three are corpus-global and coalesced — yet their rows still carry
`episode_id` (they are per-episode rows folded by
[`claim_pending_for_coalescing`](../thestill/core/queue_manager.py#L1386)), so
they *do* appear in this episode's task list.

Conflating the two definitions would either freeze the entity progress UI or
invalidate entity/related-episode data before those artifacts exist:

| Term | Definition | Governs |
|---|---|---|
| **Content-terminal** | `state === 'summarized'` (or `is_failed`) — transcript + summary are final for this pass | Whether the reader still expects transcript/summary changes |
| **Chain-terminal** | See below — deliberately *not* "no active task" | Whether `useEpisodeTasks` may stop polling |

Chain-terminal is strictly later than content-terminal. `useEpisodeTasks` keys
off chain-terminal only.

### Chain-terminal, precisely

**As implemented**, this collapses to a single clause:

```text
chain-terminal = 4 consecutive idle ticks
```

where an *idle tick* is a poll returning no `pending` / `processing` /
`retry_scheduled` task, and 4 ticks at the 15 s idle cadence ≈ 1 minute. Any
active task resets the counter to zero.

The `reindex`-seen and content-terminal qualifiers drafted below turned out to
be unreachable refinements: the only way to accumulate four consecutive idle
ticks is for there to be genuinely nothing queued, and the entity branch is
*chained* — its successor rows appear as `pending` within milliseconds of the
predecessor completing, so they hold the counter at zero even when workers are
backed up. Dropping the qualifiers also **bounds a case the three-clause
version did not**: an episode sitting unprocessed at `discovered` satisfies
neither qualifier and would have polled forever.

Rationale, kept because it is what sizes the 4-tick grace period:

- **Watch through `reindex`**, not `summarize`. Stopping at "summarized and no
  active task" lands in the non-atomic gap before `extract-entities` is
  enqueued and freezes `EntityBranchProgress` at empty — F1 relocated to the
  entity branch.
- **Do not watch to `enrich-entities`.** It is the true graph terminal, but it
  is network-bound Wikidata work, coalesced across episodes, and rendered by no
  per-episode surface. Keying on it would let an open tab poll for a long time
  on behalf of UI nobody can see.
- **The grace period is what covers `compute-related`** ([#46](46-related-episodes-scaling.md)),
  which repopulates the Related-episodes rail *on this page*. One extra minute
  of 15 s polls buys the rail; watching the whole tail does not buy
  proportionally more.
- **The third clause is required, not defensive.** Episodes summarized before
  [#28](28-corpus-search-and-entities.md) / [#46](46-related-episodes-scaling.md) /
  [#47](47-auto-entity-enrichment-stage.md) shipped have **no entity rows at
  all**. A definition keyed only on seeing a terminal entity task would poll
  those legacy episodes forever.

Worst case for any episode, legacy or current: ~1 minute of 15 s polls after
the last observable activity, then silence.

## Design

**Principle:** the reader's freshness derives from a query that is *already*
polling, evaluated on data each tick just fetched. No new state machine
mediates between "the pipeline advanced" and "the screen updates".

### D1 — Do not gate the episode query on task activity

`useEpisode` keeps the global 5 s poll, unconditionally, for the lifetime of
the reader. It is deliberately **not** made conditional on task activity: a
2 s task poll and a 5 s episode poll race, and an empty task tick arriving
before the terminal episode tick would latch the episode poll off while the
cache still said `cleaned` — recreating the original bug with extra steps. The
existing unconditional poll is the one part of today's machinery that works;
this spec builds on it rather than replacing it.

Add `refetchIntervalInBackground: true` to this query only (F3), so a
backgrounded tab stays current instead of depending on a focus refetch. Content
queries stay focus-gated.

**Every observer must agree.** `refetchInterval` is per-observer, so one
component switching its poll off does nothing while another observer of the same
query key still has a timer running. `EpisodeDetail` fetches the same episode for
its breadcrumb and must therefore pass `live: false` — it is a passive consumer of
a cache entry the reader keeps fresh. This is the same shape as the task-poll
counter split (D2); a shared cache entry with per-observer control needs a single
driver, always.

**Implemented in v5:** the poll stops once the episode is *settled* —
content-terminal **and** every present artifact has actually reported. Terminal
state alone is not enough: gating on it would stop the clock while the summary
was still catching up, which is the bug in miniature. Enabling background
polling without this gate would have cost ~17k requests/day for a reader left
open on a finished episode — a regression against the previous behaviour, where
background tabs paused. Reactivation is `refetchOnWindowFocus` plus any queue
mutation's invalidation.

One trap worth recording: `refetchInterval: undefined` does **not** mean
"inherit the default". React Query spreads query options over
`defaultOptions.queries`, so an explicit `undefined` wins and silently disables
the 5 s clock. The option is set only when stopping. The integration test in
§Testing is what caught this.

### D2 — The task query becomes a self-healing heartbeat

`useEpisodeTasks` stops latching (F1) and respects the chain-terminal
definition:

| Condition | Interval |
|---|---|
| Any task `pending` / `processing` / `retry_scheduled` | 2 s |
| No active task, **not chain-terminal** (see §Chain-terminal, precisely) | 15 s |
| No active task, past the grace period, episode **not** content-terminal | 60 s probe |
| No active task, past the grace period, episode content-terminal | `false` |

The idle-tick counter resets to zero the moment any active task appears, so a
gap between stages costs one slow tick rather than starting a countdown to
silence.

**The 60 s probe is not padding.** v4 stopped outright after the grace period
on the claim that "every path back to activity restarts it". That claim was
wrong: an externally started stage (scheduler, CLI, another client) produces
*no* local signal until it **completes** — episode state changes on completion,
not on start. A reader would therefore show "nothing running" for the entire
duration of someone else's transcription. The probe costs one request per
minute, only while the reader is open, and only for episodes that are not yet
terminal.

**Single owner.** `EpisodeReader` calls `useEpisodeTasks` once and passes the
rows to `PipelineActionButton` and `EntityBranchProgress` as props. Two
observers of one cache entry would each keep their own idle counter, and
whichever loses a collided fetch does not advance its own — so "4 consecutive
idle responses" would stop being a property of the query.

The 15 s idle tier closes F1: an inter-stage gap costs at most one slow tick
before the successor appears and the cadence returns to 2 s. This serves the
stepper and `EntityBranchProgress`; it is *not* what keeps the reader's content
fresh — D3 is.

### D3 — Reconcile content against the episode payload, every tick

The episode payload carries `state`, `has_transcript` and `has_summary`
([api_podcasts.py:343-345](../thestill/web/routes/api_podcasts.py#L343-L345)),
already used for the tab dots at
[EpisodeReader.tsx:628](../thestill/web/frontend/src/components/EpisodeReader.tsx#L628).
A `useEpisodeLiveRefresh(episode)` hook owned by `EpisodeReader` — which is
unconditionally mounted, unlike `PipelineActionButton` (F2) — does two things:

**(a) Invalidate on any change**, not on a `false → true` flip. The flags are
`bool(path)` and re-transcription clears both downstream paths
([task_handlers.py:472-473](../thestill/core/task_handlers.py#L472-L473)), so
they legitimately travel `true → false → true`. Comparing the *tuple*
`(state, has_transcript, has_summary)` against the previous render and
invalidating on any difference handles regeneration correctly in both
directions — a `true → false` transition should also clear now-stale content.
Summary invalidation targets the `[…, 'summary']` **prefix**, so every cached
language variant is refreshed, not just the active one.

**(b) Mount reconciliation with bounded retries**, symmetric across both artifacts, for
the case where the flag was already true before this reader mounted and so no
transition exists to observe (back-navigation into a 60 s-fresh cached
`available: false`):

- `has_transcript && transcriptData?.available === false` → invalidate once
- `has_summary && summaryData?.available === false` → invalidate once

**An attempt only counts once it has run.** v4 marked the artifact reconciled
at *request* time, which spends the single attempt on a refetch that fails or
briefly returns the same unavailable response — leaving the reader stale until
focus or reload, i.e. recreating the exact state this spec exists to remove.
Attempts are now made on a backoff chain (0 ms, +3 s, +9 s) and stop as soon as
the content arrives.

**The cap is what makes it terminate.** `available` is
`not summary.startswith("N/A")`
([api_podcasts.py:502](../thestill/web/routes/api_podcasts.py#L502)) — content-
derived, not path-derived. An `N/A` summary ([#41](41-llm-prohibited-content-fallback.md))
is a **legitimate permanent** `has_summary: true && available: false` state. A
naive "invalidate whenever mismatched" would spin forever on those episodes.
Three attempts per `(episodeId, artifact)`, reset when the artifact is
regenerated so a later pass gets a fresh budget.

### D4 — Retire the edge detector

`PipelineActionButton` loses `onTaskComplete` and its `prevActiveTaskRef`;
`EpisodeReader.handleTaskComplete` is deleted. The stepper, transcribe SSE, and
cancel affordances are untouched. Because the reader is shared by the standalone
page and the [#52](52-inbox-reader-overlay.md) overlay, both surfaces are fixed
by the same change.

### Deferred: server push

An episode-level SSE channel (`GET /api/episodes/{id}/events`) would remove
polling entirely; the precedent exists at
[PipelineActionButton.tsx:293](../thestill/web/frontend/src/components/PipelineActionButton.tsx#L293).
Deferred: it needs a cross-process fan-out story once workers and web run
separately ([#66](66-aws-single-ec2-hosting.md)), and polling already solves the
reported bug. If it lands later, D3's reconciliation survives unchanged — only
the trigger swaps.

## Acceptance criteria

1. Open an episode at `discovered`, choose **Run Full Pipeline**, touch nothing:
   transcript content appears within ~10 s of the clean stage completing, and
   the summary within ~10 s of the summarize stage completing. No reload, no tab
   switch, no click. (~10 s = one 5 s episode tick to observe the flag, plus one
   content refetch.)
2. Same run with the tab backgrounded throughout: returning shows the finished
   summary immediately, without a reload.
3. Same run started outside the UI (CLI, scheduler, another client) with the page
   already open: identical result — the reader never assumes it initiated the work.
4. **Scoped guarantee.** For a first-time forward pipeline producing a readable,
   non-`N/A` artifact, the state `episode.state === 'summarized'` **and** the
   Summary tab showing *"Summary not yet available"* is unreachable. This is
   deliberately not universal: `has_summary` is `bool(summary_path)`, so a
   present-but-unreadable file, or an `N/A` summary from
   [#41](41-llm-prohibited-content-fallback.md), yields a legitimate
   `has_summary && !available`. Making that case indistinguishable needs an
   artifact revision / readability contract in the API payload — out of scope
   here, noted in §Future work.
5. Once **chain-terminal**, the reader issues no further task-queue requests for
   the episode. (The 5 s episode poll continues by design — see D1; removing it
   is Phase 2.)

## Implementation phases

### Phase 1 — Level-triggered core ✅

- [x] **1.1** `useEpisodeLiveRefresh(episode)` — tuple-diff invalidation + one-shot symmetric mount reconciliation (D3)
- [x] **1.2** Mount it in `EpisodeReader`; delete `handleTaskComplete`
- [x] **1.3** Remove `onTaskComplete` from `PipelineActionButton` and its props (D4)
- [x] **1.4** `refetchIntervalInBackground: true` on `useEpisode` only (D1/F3)
- [x] **1.5** `useEpisodeTasks` three-tier cadence keyed on **chain-terminal** (D2)

### Phase 2 — Bounding ✅

- [x] **2.1** Stop the episode poll once *settled* (content-terminal **and** every present artifact has reported). Promoted from optional: enabling background polling without it was a net-new ~17k requests/day for an open reader
- [x] **2.2** Task-poll backoff to a 60 s probe past the grace period, `false` only when content-terminal

### Phase 3 — Follow-through ✅

- [x] **3.1** Add FM-8 to [#42](42-robustness-and-failure-mode-hardening.md) catalogue + review checklist
- [x] **3.2** File follow-ups for the other latches the same reasoning condemns:
      `useRefreshStatus` ([useApi.ts:383](../thestill/web/frontend/src/hooks/useApi.ts#L383)),
      `useAddPodcastStatus` ([:414](../thestill/web/frontend/src/hooks/useApi.ts#L414)),
      `usePipelineTaskStatus` ([:460](../thestill/web/frontend/src/hooks/useApi.ts#L460)).
      Filed as [#163](https://github.com/ssarunic/thestill/issues/163). Do not
      widen this spec.

## Testing

Per [#04](04-testing.md), and pointedly per [#42](42-robustness-and-failure-mode-hardening.md)
FM-5 (*tests that pass because the mocks are internally consistent*): a fixture
where exactly one task is always active until the pipeline ends passes against
today's broken code. The fixtures must reproduce the gaps and the races.

- [x] **Empty-tick-before-terminal ordering (the F1/D1 race).** Script, in this
      order: task response `[]` → *then* episode response `summarized` +
      `has_summary: true`. Assert the summary query refetches and content
      renders. Fails against any design that gates the episode poll on task
      activity.
- [x] `useEpisodeTasks` keeps polling on a **zero-active-task** response while
      not chain-terminal (F1 — fails on `main`).
- [x] **`summarized` + active `extract-entities` task**: task polling continues,
      `EntityBranchProgress` keeps updating, and entity/related queries are not
      invalidated as though complete.
- [x] **The entity-branch gap**: `summarized` + **empty** task list (summarize
      done, `extract-entities` not yet enqueued) does **not** stop polling — the
      idle-tick counter must not have reached its threshold yet.
- [x] **Grace period expiry**: terminal `reindex` + 4 idle ticks → polling stops;
      3 idle ticks → still polling. Assert the boundary, not just the endpoint.
- [x] **Idle-tick reset**: idle, idle, *active task appears*, idle → polling
      continues (the counter reset, so this is tick 1 of 4, not tick 3).
- [x] **Legacy episode**: `summarized`, entity branch never ran, zero entity rows
      ever → polling stops after the grace period rather than continuing forever.
- [x] Full-chain simulation with fake timers, empty ticks scripted between every
      stage — the empty ticks are the point of the fixture.
- [x] **Symmetric mount reconciliation**: `has_transcript: true` +
      cached `transcript.available: false` on mount → exactly one invalidation.
      Same for summary. Assert **exactly one**, not "at least one".
- [x] **`N/A` summary does not spin**: `has_summary: true` +
      `available: false` permanently → one reconciliation attempt, then quiet.
- [x] **Regeneration**: `has_summary` observed `true → false → true` (re-transcribe
      clears paths) invalidates on both transitions.
- [x] Task polling ceases once chain-terminal, and on `is_failed`.
- [x] Reader overlay ([#52](52-inbox-reader-overlay.md)) exercises the same hook —
      one parity test so the surfaces can't drift (FM-6).
- [x] **Non-terminal probe**: past the grace period, an externally queued task
      is still discovered, and the fast cadence resumes on discovery.
- [x] **Reconciliation retries**: a still-unavailable refetch schedules another
      attempt; the budget caps at 3; arrival stops the chain early.

**Integration level** (`useEpisodeLiveRefresh.integration.test.tsx`) — the unit
tests above assert that `invalidateQueries` is *called*, which is not the same
claim as "the summary reaches the screen". They would pass even if the episode
query had stopped polling or an invalidation targeted a key that refetched
nothing. These use a real `QueryClient` with main.tsx's defaults, mock only the
HTTP layer, and assert on rendered text:

- [x] Mid-pipeline → summarized with **no task-completion edge ever delivered**:
      the summary text appears on screen.
- [x] Mount into a cached `available: false` while the episode already says the
      summary exists: recovered without a reload.
- [x] A settled reader issues no further episode requests.

This file earned its place immediately: it caught `refetchInterval: undefined`
silently overriding the app-wide 5 s default (see D1), which every unit test
missed because they never exercised the real interval.

## Browser verification

`tests/reader-live-refresh.spec.ts` (Playwright, real Chromium, real timers,
against the built bundle). Every `/api/**` call is intercepted so the ordering
that produced the bug can be scripted exactly — in particular **the task list is
empty for the entire run**, so no completion edge is ever available to observe.

| Check | Result |
|---|---|
| Summary appears with no reload, no interaction, no task edge | ✅ |
| A settled reader stops polling (≤1 request per 20 s vs ~4 live) | ✅ |
| Task poll survives an empty list and keeps the idle tier running | ✅ |

Run against `main` for the comparison that makes these meaningful: **all three
fail**, the headline one timing out after 30 s waiting for a summary that never
arrives. A test that passes on both branches proves nothing.

```bash
PLAYWRIGHT_BASE_URL=http://127.0.0.1:8000 npx playwright test tests/reader-live-refresh.spec.ts
```

Assertions are deliberately rates, not absolutes: `refetchOnWindowFocus` is the
designed reactivation path, so demanding *zero* requests from a settled reader
would forbid the mechanism this design depends on to recover.

## Future work

- **Artifact readability contract.** `has_summary` / `has_transcript` are
  `bool(path)` and say nothing about whether the artifact is readable from
  `FileStorage` or is an `N/A` placeholder. An explicit
  `summary: { present, readable, revision }` shape would let the client
  distinguish "not produced yet" from "produced and empty" from "produced but
  storage is broken" — and would make acceptance criterion 4 universal instead
  of scoped. Also the natural carrier for a regeneration counter, removing the
  tuple-diff heuristic in D3(a).
- Server push (see §Deferred).
- List-page parity — [#21](21-episode-processing-indicator.md).

## Non-goals

- List pages (`Episodes`, `PodcastDetail`, Inbox rows) — [#21](21-episode-processing-indicator.md).
- Sub-stage progress percentages; the transcribe SSE stays as-is.
- Any backend or API change in Phase 1. The episode payload already carries
  every field this design reads.
- Changing pipeline chaining semantics in `TaskWorker`. The non-atomic
  complete-then-enqueue window at
  [task_worker.py:671-704](../thestill/core/task_worker.py#L671-L704) is
  legitimate; the client must tolerate it, not the reverse.

## Revision history

**v6 (2026-08-04)** — verified in a real browser, which found one more bug that
327 unit tests and three review passes had all missed.

`EpisodeDetail` also calls `useEpisode` (for the breadcrumb) with no options, so
**its** observer kept a 5 s timer alive and the reader's terminal gate never took
effect — a settled reader still made 5 requests per 23 s. `refetchInterval` is
per-observer state over a shared cache entry, exactly the defect three reviews had
already flagged for the task-poll counter; I fixed it there and then reintroduced
it one hook over. Fixed by having the passive consumer pass `live: false`.

The jsdom integration test could not have caught this: it exercised
`useEpisode(..., { live: false })` directly and so never ran the feedback loop
that makes `settled` take effect. The Playwright suite exercises the real
`EpisodeDetail → EpisodeReader` composition against the built bundle, and it
failed on the first run.

Confirmed the suite fails on `main` (all three tests; the headline one times out
waiting for a summary that never appears) and passes 9/9 across three repeats on
this branch.

**v5 (2026-08-03)** — post-review hardening. Five findings, all against v4's
implementation rather than its design:

1. **Reconciliation counted an attempt before it happened** — a failed or
   still-stale refetch consumed the one-shot budget and left the reader
   indefinitely stale. Now a backoff chain (0 ms, +3 s, +9 s), capped at 3, that
   stops the moment content arrives. See D3(b).
2. **The task poll could not discover externally queued work.** v4 stopped
   outright after the grace period, justified by "every path back to activity
   restarts it" — which was wrong: an externally started stage produces no local
   signal until it *completes*, so a scheduler-run transcribe was invisible for
   its whole duration. Added the 60 s non-terminal probe. See D2.
3. **The idle counter was observer-local over a shared query.** Both consumers
   called `useEpisodeTasks`, so whichever observer lost a collided fetch did not
   advance its counter. `EpisodeReader` now owns one task query and passes rows
   down as props; `EpisodeTask` was extracted as a named type.
4. **Background episode polling was unbounded** — enabling
   `refetchIntervalInBackground` without a terminal gate cost ~17k requests/day
   for an open reader, a regression against the prior behaviour where background
   tabs paused. Phase 2.1 promoted into this change, gated on *settled* rather
   than merely terminal.
5. **The regression tests stopped at the invalidation call.** Added
   `useEpisodeLiveRefresh.integration.test.tsx`: real `QueryClient`, HTTP layer
   mocked, assertions on rendered text. It immediately caught a live bug —
   `refetchInterval: undefined` overrides the app-wide default rather than
   inheriting it, which had silently disabled the 5 s clock.

Finding 5 is the one worth remembering: the unit tests were internally
consistent and complete-looking, and still could not tell whether the feature
worked. That is [#42](42-robustness-and-failure-mode-hardening.md) FM-5 landing
on this spec's own test suite.

**v4 (2026-08-03)** — implemented; Phase 1 + Phase 3 complete on
`feat/68-live-episode-reader-refresh`. Two deviations from v3, both recorded
inline above:

1. **Chain-terminal simplified to one clause** (see §Chain-terminal, precisely):
   4 consecutive idle ticks, unconditionally. The `reindex`-seen and
   content-terminal qualifiers were unreachable — chained successors appear as
   `pending` within milliseconds, holding the counter at zero — and dropping
   them bounds the un-queued-episode case the three-clause version left
   polling forever.
2. **Idle ticks are counted in `queryFn`, not in an effect.** The effect
   version was one render behind the interval computation, which bought a
   free extra poll (the boundary test caught it: 5 fetches where 4 were
   specified). `queryFn` runs exactly once per fetch and completes before
   React Query recomputes the interval, so the count is neither lagged nor
   double-incremented, and it costs no re-render per poll.

Phase 2 remains deferred as drafted. Follow-ups for the three other latched
pollers filed as [#163](https://github.com/ssarunic/thestill/issues/163).

**v3 (2026-08-03)** — chain-terminal made precise before implementation. v2
asserted a three-stage entity branch and defined chain-terminal as "no active
task and summarized". Both were wrong on the facts: the branch is six stages
([queue_manager.py:228-239](../thestill/core/queue_manager.py#L228-L239)), the
coalesced tail rows still carry `episode_id` and so appear in this episode's
task list, and "no active task" stops inside the pre-`extract-entities` gap —
F1 relocated. Replaced with the three-clause definition in
§Chain-terminal, precisely: watch through `reindex` plus a ~1 minute grace
period (which is what covers [#46](46-related-episodes-scaling.md)'s
`compute-related` and the Related-episodes rail on this page), with a
no-task-at-all fallback that is **required** — legacy episodes summarized
before [#28](28-corpus-search-and-entities.md) have no entity rows and would
otherwise poll forever. Added four boundary tests (entity-branch gap, grace
expiry at 3 vs 4 ticks, idle-tick reset, legacy episode).

**v2 (2026-08-03)** — review corrections, all five verified against the code:

1. **Episode cadence was wrong.** v1 claimed `useEpisode` had no poll; it
   inherits `refetchInterval: 5000` from
   [main.tsx:14](../thestill/web/frontend/src/main.tsx#L14). Diagnosis and
   baseline rewritten — the bug is a broken *bridge*, not a missing loop.
2. **Dropped the activity-gated episode poll (was v1 D2).** A 2 s task poll
   returning `[]` before the 5 s episode poll returned `summarized` would have
   latched the episode poll off with the cache still on `cleaned` — the original
   bug, reintroduced. Now an explicit non-goal with a dedicated regression test.
3. **Split terminal into content-terminal vs chain-terminal.** The entity branch
   always chains past `summarized`
   ([task_worker.py:805-816](../thestill/core/task_worker.py#L805-L816)) and
   `EntityBranchProgress` shares the task query; v1's single definition would
   have frozen it.
4. **Mount reconciliation made symmetric** — v1 covered summary only; the
   transcript query has identical frozen semantics.
5. **Acceptance criterion 4 rescoped.** Flags are `bool(path)` and non-monotonic
   across regeneration; `available` is content-derived, so `N/A` summaries
   ([#41](41-llm-prohibited-content-fallback.md)) are a legitimate permanent
   mismatch. Drove the one-shot requirement in D3(b) and the tuple-diff (rather
   than flip-detection) in D3(a).
