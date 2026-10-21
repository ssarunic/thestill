# Reprocess an Episode From a Chosen Stage

> **Status:** 🚧 Phases 1–2 implemented (2026-10-21), unmerged
> **Created:** 2026-10-21
> **Updated:** 2026-10-21
> **Priority:** Medium. Every prompt or provider fix (the v1.27.2 cleaner ad rule, summary profile bumps) only reaches new episodes today. Getting an existing episode onto the fix means SQL on the prod database.
> **Author:** Product & Engineering
> **Related:** [#18 segment-preserving-transcript-cleaning](18-segment-preserving-transcript-cleaning.md) (clean stage, `CleaningProvenance`), [#20 parallel-task-queues](20-parallel-task-queues.md) (worker, per-episode mutex), [#28 corpus-search-and-entities](28-corpus-search-and-entities.md) (entity branch, chunks), [#29 per-user-inbox-fanout](29-per-user-inbox-fanout.md) (publish and fan-out happen once), [#42 robustness-and-failure-mode-hardening](42-robustness-and-failure-mode-hardening.md) (FM-2 checkpoint before durability), [#98 genre-aware-summaries](98-genre-aware-summaries.md) (summary profiles and their versions), [#104 abuse-prevention-and-user-admin](104-abuse-prevention-and-user-admin.md) (admin role).

---

## Executive summary

An admin can run any pipeline stage for an episode that has not reached
it yet, and can retry a stage that failed. There is no way to run a stage
again on an episode that **succeeded**. That is the case after every
prompt or provider fix: the fix only reaches episodes processed after the
deploy.

This spec adds one admin action, **Reprocess from…**. The admin picks a
starting stage (Transcribe, Clean, Summarize or Entities). That stage and
every stage after it run again, through the entity branch. It is
available from the episode page and from a CLI command.

Today's stages were written for the first pass, so re-running them as
they are is not safe. Five of them need a change:

1. **Transcribe** reuses the old transcript file instead of calling the
   provider. It then blanks the clean transcript and the summary, so the
   episode disappears for readers until the later stages finish.
2. **Clean** reuses the old speaker mapping and guest list from the facts
   file.
3. **Reindex** skips any episode that already has chunks, so search keeps
   the old text.
4. **Retry** of a failed reprocess would lose the reprocess settings.
5. **The episode page** stops polling once an episode is summarized, so
   the admin sees no progress.

The rule throughout: **readers keep the current version until its
replacement is written.** No stage clears an output before its new one
exists.

## Problem and evidence

- **v1.27.2 changed the cleaner's ad rule.** The Rest Is Politics #581
  still shows a drama trailer as conversation. To re-clean it you would
  have to null `summary_path` and `clean_transcript_path` in Postgres
  over SSM and queue a clean, and that hides the episode while it runs.
- **The CLI does not help.** `thestill clean-transcript --force` has no
  episode filter and re-cleans every transcript. `thestill summarize
  --force` likewise. Both run in-process on the operator's machine, not
  through the queue, and the CLI summarize does not update
  `summary_preview`.
- **Every route through the API is closed.**
  - The single-stage admin endpoints (`POST /api/commands/clean` and so
    on) require the episode to be in the exact state before that stage,
    so a summarized episode gets a 400.
  - `POST /api/commands/run-pipeline` answers 400 "already fully
    processed".
  - `POST /api/episodes/{id}/retry` needs a failure.

## Current state

### Stages, state and chaining

- **State is derived, not stored.** `Episode.state`
  (`models/podcast.py:294`) is FAILED when `failed_at_stage` is set.
  Otherwise it is the last stage whose path is set: `summary_path`, then
  `clean_transcript_path`, then `raw_transcript_path`,
  `downsampled_audio_path`, `audio_path`. Moving state backwards means
  blanking paths.
- **Stages run in one line.** `STAGE_SUCCESSORS`
  (`core/queue_manager.py:271`) is download → downsample → transcribe →
  clean → summarize → extract-entities → resolve-entities → reindex →
  rebuild-cooccurrences → compute-related → enrich-entities.
- **The worker queues the next stage when one succeeds**
  (`TaskWorker._maybe_enqueue_next_stage`, `core/task_worker.py:900`):
  - The entity branch always chains.
  - A main-chain stage chains only when the task metadata has
    `run_full_pipeline`, and stops at `target_state` if one is set.
  - Successors inherit the task's metadata.
- **The single-stage endpoints don't chain.** They queue with no
  metadata, so a queued clean stops after clean. `/summarize` is the
  exception: it reaches the entity branch, because that always chains.

### What each stage does when run again

| Stage | Behaviour on an episode that already has its output | Safe to re-run? |
|---|---|---|
| Transcribe (`task_handlers.py:335`) | Skips the provider when the raw JSON parses (`:438`). Needs the downsampled WAV, which is deleted after every transcription (`:507`), except under Dalston with a fetchable URL. After writing, sets `clean_transcript_path=""` and `summary_path=""` (`:498`). | **No.** It is a no-op that hides content, and it often fails outright. |
| Clean (`:521`) | Overwrites the `_cleaned.md` and `.json` in place. The DB update comes after the write. Reuses episode facts when the file exists (`transcript_cleaning_processor.py:237`). Leaves `summary_path` alone. | Yes, but the speaker mapping is stale. |
| Summarize (`:656`) | Overwrites the summary, citations and manifest in place, then the DB update, then `summary_preview`. `mark_episode_published` is conditional, so there is no second inbox fan-out (`:783`). | Yes. |
| Extract entities (`:1030`) | Deletes the episode's mentions, then inserts new ones as `pending` (`:1131`). Returns before deleting when GLiNER is unavailable. | Yes. Quotes and the entity strip vanish until resolve finishes. |
| Resolve entities (`:1381`) | Links pending mentions. When the linker is unavailable it leaves them pending. | Yes. |
| Reindex (`:805`) | Calls `write_episode` without `force`, so it skips when chunks exist (`postgres_chunk_writer.py:79`). | **No.** It is a silent no-op. |
| Co-occurrences, related, enrich (`:853`–`:1027`) | Recompute from the current mentions and chunks. Enrich is staleness-gated. | Yes. |

### Guards

- **Duplicate tasks.** `add_task` merges a request into an active row for
  the same episode and stage (PR #345), and the **existing row keeps its
  metadata**. A reprocess merged into a plain pending clean loses
  `run_full_pipeline`, and the chain stops there.
- **Per-stage check only.** `has_pending_task` looks at one stage. Nothing
  rejects a request while another stage of the same episode is queued or
  running.
- **Retry.** `POST /api/episodes/{id}/retry` re-queues `failed_at_stage`
  with `{"run_full_pipeline": true, "initiated_by": "episode-retry"}`.

### Episode page

- **No action for summarized episodes.** `PipelineActionButton` hides on
  summarized episodes, and `EpisodeMoreMenu` has no admin entries.
- **Admin check.** Admins are detected with `useAuth().isAdmin`.
- **Polling stops at summarized.** `useEpisodeLiveRefresh` treats
  `summarized` as terminal (`useApi.ts:531`) and stops, and so does the
  task poll.

## Goals

1. An admin can reprocess one episode from Transcribe, Clean, Summarize
   or Entities, from the episode page or the CLI.
2. Readers never see the episode lose its transcript or summary because a
   reprocess is running or has failed. They see the old version until the
   new one is written.
3. Reprocessing from a stage really redoes it: the provider is called
   again, chunks are re-embedded, and facts are re-extracted when asked.
4. The whole chain runs from the chosen stage through the entity branch,
   and a retry after a failure carries on the same reprocess.
5. Each run is recorded: who asked, from which stage, and with which
   options.

## Non-goals

- **Bulk reprocess** (a podcast, a date range, "everything cleaned before
  v2"). It needs a count, a cost estimate and throttling. That is a
  follow-up spec, which can reuse this endpoint per episode.
- **Re-downloading changed audio.** When the publisher's file changes
  (dynamic ad insertion), that is spec #31's and the drift detector's
  job. Reprocess from Transcribe re-downloads audio only when no usable
  audio is left.
- **Refreshing podcast facts.** They are user-edited. A reprocess may
  refresh episode facts only.
- **Rewriting snapshots.** Inbox rows, briefings and narrations keep what
  they had. Inbox rows read the episode live, so they show the new
  summary anyway. Briefings and narrations are records of what was sent.

## Design

### 1. One endpoint

`POST /api/episodes/{episode_id}/reprocess` (admin only, `require_admin`):

```json
{ "from_stage": "clean", "refresh_episode_facts": false }
```

- **`from_stage`** is one of `transcribe`, `clean`, `summarize`,
  `entities`. The value `entities` starts at extract-entities.
- **Preconditions:**
  - the episode exists (404 otherwise);
  - it has the input for `from_stage` (400 otherwise): a raw transcript
    for clean, a clean transcript for summarize, a clean JSON sidecar for
    entities;
  - no task for this episode is pending, processing or retry-scheduled
    **in any stage** (409 otherwise).
- **The episode-wide active-task check is new:**
  `add_task_if_episode_idle`, on both the SQLite and the Postgres
  queue managers. The check and the insert are one transaction (SQLite
  `BEGIN IMMEDIATE`; Postgres an advisory lock on the episode, which
  every task writer of the episode takes), so concurrent requests cannot
  both pass it. It also keeps a reprocess from merging into a plain
  queued task and losing its metadata. `has_active_task_for_episode`
  remains for the dry run.
- **The pipeline hand-off is atomic:** `complete_task(...,
  enqueue_next=...)` queues the next stage in the same transaction that
  completes the current one, so the episode never looks idle between two
  stages of a running chain.
- **It queues one task** with this metadata:

  ```json
  {
    "run_full_pipeline": true,
    "initiated_by": "reprocess",
    "reprocess": {
      "from_stage": "clean",
      "refresh_episode_facts": false,
      "requested_by": "<user id>",
      "requested_at": "<ISO-8601 UTC>"
    }
  }
  ```

  No `target_state`, so the chain runs through the entity branch.
  Successors inherit the metadata, so every stage in the run knows it is
  part of a reprocess.
- **Starting stage for Transcribe:** if the episode has no usable audio
  for the configured transcription provider, the run starts at download
  instead. It uses the same rules as `_get_starting_stage`: a fetchable
  URL under Dalston counts as usable.
- **The response** is `{task_id, starting_stage, episode_id}`.
- **Logging:** the endpoint logs `episode_reprocess_requested` with
  `episode_id`, `from_stage`, `starting_stage`, `user_id` and the options.

`initiated_by: "reprocess"` is an operator tag, so `_unwanted()` never
stops the chain. That is the same treatment `api` and `episode-retry`
get.

### 2. Stage changes, keyed on `metadata["reprocess"]`

The changes below apply only when the task carries `reprocess`. First-pass
behaviour does not change.

- **Transcribe:**
  - Skip the `_existing_transcript_is_valid` short-cut for a file written
    before `requested_at`, so the provider is called. A file written
    after it came from this run (a retry after a failed DB update) is
    still reused, as on a first pass. An unreadable modification time
    counts as older.
  - Write the new raw JSON over the old one only after the provider
    returns, as today.
  - **Don't blank `clean_transcript_path` or `summary_path`.** The old
    clean transcript and summary stay readable until clean and summarize
    overwrite them in place a few minutes later.
- **Clean:**
  - With `refresh_episode_facts`, run facts extraction even when the
    episode facts file exists. The new facts are written with the usual
    save and replace the old file only on success.
  - Podcast facts are never refreshed.
  - The role linker then runs on the new facts, as it already does.
- **Reindex:** call `write_episode(..., force=True)`. The writer already
  embeds outside the transaction, then deletes and inserts in one
  transaction (`postgres_chunk_writer.py:104`). Search sees the old
  chunks until the new ones commit.
- **Extract and resolve:** no change. A short gap between the delete of
  the old mentions and the end of resolve is accepted. Open question 1
  covers removing it.
- **Summarize, co-occurrences, related, enrich:** no change. They already
  overwrite or recompute.

### 3. Failure and retry

- **Failure** marks `failed_at_stage` as usual. The episode then reads as
  FAILED, and admins get the existing banner and Retry button.
- **No content is lost.** Paths are never blanked, so readers keep the
  last good version. Public pages gate on `summary_path` and
  `summary_preview`, not on the failure columns.
- **Retry continues the reprocess.** `POST /api/episodes/{id}/retry`
  copies `reprocess` from the metadata of the failed task. The run is
  then still forced at transcribe and reindex.

### 4. Episode page

- **Menu item.** An admin-only **Reprocess…** item in `EpisodeMoreMenu`.
  It is hidden for non-admins and for episodes that have not finished
  their first pass, where `PipelineActionButton` applies instead.
- **Dialog:**
  - A stage picker, with one line per stage on what re-runs after it.
  - A **Refresh speaker names and guests** checkbox, shown for Transcribe
    and Clean.
  - For Transcribe, a cost warning showing the episode's duration, and a
    confirm button that names the action ("Re-transcribe 1 h 12 min").
- **After queueing:**
  - The mutation invalidates the episode's task query. That restarts the
    task poll, which keeps polling while any task is active, even when
    the state is `summarized`.
  - An admin-only status line (`ReprocessStatus`) reports the run while
    it is in the user chain ("Reprocessing from clean: summarizing").
    `EntityBranchProgress` takes over in the entity branch.
  - When a reprocess's `clean` or `summarize` task completes, the
    transcript or summary query is refetched
    (`useEntityBranchRefresh`). The state never changes during a
    reprocess, so the state-diff refresh does not fire.
  - A refusal (409 and the rest) is shown inside the dialog, which stays
    open with the choice intact.

### 5. CLI

`thestill reprocess --episode <id-or-slug> --from <stage>
[--refresh-episode-facts] [--dry-run]` queues the same task through the
same service function, for the web worker to pick up. It does not run the
stages in-process, unlike `clean-transcript`. It prints the starting
stage and the task id.

### 6. Provenance

- **Clean** already stamps `CleaningProvenance` with provider, model and
  `prompt_version` in the JSON sidecar.
- **Summarize** records the profile and its version in `SummaryManifest`,
  but not the model. Add `provider` and `model` to `SummaryManifest`. It
  is optional and defaults to none for old manifests.
- **Task rows** keep the `reprocess` metadata. Together with the log
  event, that answers "when was this episode last reprocessed, from
  where, and by whom".

## Phases

| Phase | Scope |
|---|---|
| 1 | Backend: endpoint, `has_active_task_for_episode`, stage changes (transcribe, clean, reindex), retry carries `reprocess`, `SummaryManifest` provider/model, CLI `thestill reprocess` |
| 2 | Episode page: menu item, dialog, polling while a task is active |
| 3 (optional) | Remove the mentions gap (open question 1) |

### Acceptance criteria

- [ ] Reprocess from Clean on a summarized episode re-cleans, re-summarizes
      and runs the whole entity branch. The new sidecar carries the
      current `CLEANUP_PROMPT_VERSION`.
- [ ] While that run is in progress, the episode page and the public page
      show the old transcript and summary, never an empty episode.
- [ ] Reprocess from Transcribe calls the provider even when a valid raw
      transcript exists, and re-downloads audio when none is usable.
- [ ] After a reprocess, the episode's chunks come from the new
      transcript (`chunks.text` matches the new sidecar).
- [ ] `refresh_episode_facts` replaces the episode facts file. The
      podcast facts file is unchanged.
- [ ] A second request while any task of the episode is active gets 409.
- [ ] A reprocess that fails at summarize and is retried still
      force-reindexes.
- [ ] Non-admins get 403 from the endpoint and never see the menu item.

## Testing

- **Unit tests:**
  - endpoint validation: 403, 404, 400 for missing input, 409 for any
    active task;
  - task metadata and starting-stage selection, including Transcribe
    without audio;
  - each stage's branch on `reprocess` (transcribe skips the short-cut
    and keeps paths, clean refreshes facts, reindex passes `force`);
  - retry copying `reprocess`.
- **Queue contract tests:** `has_active_task_for_episode`,
  `add_task_if_episode_idle` (concurrent requests, a second same-stage
  request) and the atomic hand-off on SQLite and Postgres
  (`TEST_DATABASE_URL`).
- **Worker test:** a reprocess task chains clean → summarize → the whole
  entity branch, and successors carry `reprocess`.
- **Frontend tests:**
  - the menu item is visible only to admins;
  - the dialog sends the right body;
  - polling continues while a task is active on a summarized episode;
  - the 409 message.
- **Manual check on prod:** reprocess The Rest Is Politics #581 from
  Clean, and confirm the trailer at 35:04 is tagged `ad_break`.

## Risks and negative consequences

- **Cost.**
  - A Transcribe reprocess pays the transcription provider again; the
    dialog shows the duration before confirming.
  - Clean and Summarize cost a few cents of LLM usage.
  - Bulk use is out of scope, so cost stays one episode at a time.
- **The mentions gap.** Quotes and the entity strip on that episode are
  empty for the minutes between extract and resolve. If the linker is
  unavailable on the worker, they stay empty until it is back. That
  happens today on a first pass too, but a reprocess makes it happen to
  a published episode.
- **A failed run shows as FAILED to admins.** That is correct, and the
  content stays readable. Counts that filter on `failed_at_stage IS NULL`
  (arriving items, some dashboards) drop the episode until it is
  retried.
- **The summary can change under a reader.** Summaries already in
  briefings and narrations stay as they were sent. The episode page shows
  the new summary.

## Rollback

- **Backend:** the endpoint and the CLI command are additive.
  - Removing them leaves stages with a `reprocess` branch that nothing
    triggers.
  - Task rows that already carry `reprocess` finish normally.
- **Frontend:** the menu item can be removed on its own.

## Open questions

1. **Mentions gap.** Should extract write new mentions alongside the old
   ones (a generation column) and swap only when resolve finishes? That
   needs a migration and changes to the read paths. Phase 3 if the gap
   turns out to matter.
2. **Whole facts refresh.** Should Refresh speaker names and guests also
   re-run role linking for past episodes of the same podcast? Not in this
   spec. Today role linking reads the facts per episode.
3. **Where bulk lives.** Should bulk reprocess be a filter on the admin
   episodes list, or a CLI command with a cost estimate first?
