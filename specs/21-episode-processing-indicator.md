# Episode Processing Indicator

**Status**: 📋 Planned (2026-04-19)
**Created**: 2026-04-19
**Updated**: 2026-04-19
**Priority**: Low (UX polish; no functional gap)

## Overview

Episode lists (the all-episodes browser and the podcast detail page) have no
visual indication that an episode is currently being worked on by the pipeline.
The only surface today where processing is visible is the Task Queue page,
where the "Currently Processing" section makes it obvious. Elsewhere, a user
who has just bumped an episode or is waiting on a transcript has to switch to
the Queue page to confirm anything is happening.

This spec adds a small "currently processing" indicator (pulsing dot + stage
label, matching the Queue page visual language) to every `EpisodeCard` that
has an active task in the queue.

## Goals

1. Give users at-a-glance feedback on which episodes are being processed,
   without leaving the episode list.
2. Reuse the existing Task Queue visual vocabulary (blue pulsing dot, pill
   shape) so the signal is instantly recognizable.
3. Keep the implementation entirely on the frontend; no changes to the
   `Episode` data model, `/api/episodes` payload, or backend services.
4. Avoid extra network cost: piggy-back on the already-polling
   `/api/commands/queue/tasks` endpoint that the Queue page uses.

## Non-goals

- Backend/API changes — the `/api/episodes` endpoint is not touched.
- A per-episode progress bar or percentage — "is it running right now?" is
  enough for this pass; richer progress belongs with spec #12
  (whisperx-chunk-progress-tracking).
- Reflecting `retry_scheduled` or `pending` tasks. Only `processing` is
  shown, to keep the signal tight and avoid noise on large queues.
- Changing how `EpisodeCard` renders on the Queue page itself (the Queue page
  already has obvious indicators, per the problem statement).

## Approach

### Data source

The `Episode` model tracks completed pipeline stages in its `state` field
(`discovered` → `downloaded` → `downsampled` → `transcribed` → `cleaned` →
`summarized`). It does **not** carry any "currently in-flight" flag — that
lives in the queue, in `QueueManager`, and is already exposed through
`/api/commands/queue/tasks` as `processing_tasks: QueuedTaskWithContext[]`.

Each `QueuedTaskWithContext` includes `episode_id` and `stage`. That's
everything the UI needs to label an episode as "processing: transcribe".

The frontend already has a hook for this:

- [useQueueTasks](../thestill/web/frontend/src/hooks/useApi.ts#L341) polls
  `/api/commands/queue/tasks` with adaptive cadence (5 s while there are
  active tasks, 15 s when idle).

Because React Query deduplicates by queryKey, mounting this hook on the
Episodes page in addition to the Queue page does **not** cause duplicate
requests.

### UI

Extend [EpisodeCard](../thestill/web/frontend/src/components/EpisodeCard.tsx)
with two optional props:

- `isProcessing?: boolean`
- `processingStage?: PipelineStage`

When `isProcessing` is true, render a new badge next to the existing state
badge (around [line 147](../thestill/web/frontend/src/components/EpisodeCard.tsx#L147)).
The badge mirrors the style used in
[QueueViewer:305-308](../thestill/web/frontend/src/pages/QueueViewer.tsx#L305):
a `w-2 h-2 bg-blue-500 rounded-full animate-pulse` dot plus the label
`Processing: <stage>`.

The existing state badge is kept — it still communicates the last completed
stage, which is useful context (e.g., state = `transcribed`, processing =
`clean` means cleaning is in flight).

### Wiring

- [Episodes.tsx](../thestill/web/frontend/src/pages/Episodes.tsx): call
  `useQueueTasks()`, build a `Map<episode_id, stage>` from
  `data.processing_tasks`, and pass `isProcessing` / `processingStage` to
  each `EpisodeCard`.
- [PodcastDetail.tsx](../thestill/web/frontend/src/pages/PodcastDetail.tsx):
  same treatment.

The Queue page is unchanged.

## Tradeoff

**Two requests per page load (episodes + queue) instead of one.**

The alternative is to join queue state server-side and have `/api/episodes`
return an `is_processing` / `processing_stage` field directly on each
episode. That would be cleaner on the wire (one request, one source of
truth), but it:

- couples the episode-listing endpoint to `QueueManager`, which currently
  it has no dependency on;
- requires a second read and a join in the episodes query path;
- still needs polling (or WebSocket push) to keep the indicator live, so it
  doesn't actually eliminate a poll — it just moves the join.

For a single-user, low-episode-count deployment, the frontend-only approach
is strictly cheaper to ship and just as responsive. The duplicate fetch is
the queue-tasks endpoint, which is small and already polled by the Queue
page; adding it on the Episodes / PodcastDetail pages is a negligible extra
cost.

## Future improvements

Promote to a backend join **if** any of the following becomes true:

1. The queue-tasks payload grows large enough that fetching it on every
   episode list view becomes a visible cost (e.g., many concurrent workers,
   long completed-tasks history).
2. We ship multi-user hosting (spec #07) and want each user's episode list
   to reflect only the processing state relevant to them, without the
   client filtering a shared queue payload.
3. We add a WebSocket push channel — at that point the "join on the
   backend, push on change" model strictly dominates polling.

When that time comes, add `is_processing: bool` and
`processing_stage: PipelineStage | null` to the episode list API response,
drop the `useQueueTasks` call from `Episodes.tsx` and `PodcastDetail.tsx`,
and keep the `EpisodeCard` props unchanged — the migration is one layer
deep.

Related: spec #12 (whisperx-chunk-progress-tracking) would let the
indicator display sub-stage progress (e.g., "Transcribing 40%") once chunk
callbacks land. That is an orthogonal upgrade on top of this spec, not a
replacement for it.

## Out of scope for this spec

- Surfacing processing state on the Dashboard recent-activity feed.
- Showing a processing indicator on search results or digests.
- Animations beyond the existing `animate-pulse` dot.
