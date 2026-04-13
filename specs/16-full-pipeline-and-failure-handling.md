# Full Pipeline Execution & Failure Handling

**Status**: ✅ Complete
**Created**: 2026-01-07
**Last Updated**: 2026-01-07

> **Implementation Summary**: All backend infrastructure (task queue, error classification, retry logic, DLQ) and frontend UI components (full pipeline button, failure banners, DLQ dashboard) have been implemented. Unit and integration tests are pending.

## Overview

This spec covers two related features:

1. **Full Pipeline Execution**: Allow users to run the entire pipeline (download → downsample → transcribe → clean → summarize) with a single action, rather than step-by-step
2. **Robust Failure Handling**: Distinguish between transient (retryable) and fatal errors, implement exponential backoff, and add Dead Letter Queue (DLQ) for manual intervention

---

## Part 1: Full Pipeline Execution

### Current Behavior

- Episode details page has a `PipelineActionButton` that shows the next step
- Each click runs one stage (e.g., "Download" → "Downsample" → "Transcribe")
- User must manually trigger each step

### Proposed Behavior

- Add "Run Full Pipeline" option alongside single-step execution
- When selected, completing one stage automatically enqueues the next
- Pipeline continues until summarization or failure

### Backend Architecture: Chain Enqueueing

**Chosen approach**: When a task completes, check metadata flag and enqueue next stage.

#### Database Changes

```sql
-- Add metadata column to tasks table
ALTER TABLE tasks ADD COLUMN metadata TEXT NULL;  -- JSON blob
```

#### Task Metadata Schema

```json
{
  "run_full_pipeline": true,
  "target_state": "summarized",
  "initiated_at": "2026-01-07T10:30:00Z",
  "initiated_by": "user"
}
```

#### Implementation Steps

- [x] **1.1** Add `metadata` column to tasks table (migration) ✅
- [x] **1.2** Update `QueueManager.add_task()` to accept optional `metadata: dict` ✅
- [x] **1.3** Update `Task` dataclass to include `metadata: Optional[dict]` ✅
- [x] **1.4** Create helper function `get_next_stage(current: TaskStage) -> Optional[TaskStage]` ✅
- [x] **1.5** Modify `TaskWorker._process_task()` to call chain-enqueue logic after successful completion ✅
- [x] **1.6** Add `POST /api/commands/run-pipeline` endpoint ✅
- [x] **1.7** Update `PipelineActionButton` to split button with dropdown ✅
- [x] **1.8** Add pipeline progress visualization during full pipeline run ✅
- [x] **1.9** Implement "Cancel Pipeline" functionality ✅

#### Stage Progression Map

```
DOWNLOAD    → DOWNSAMPLE
DOWNSAMPLE  → TRANSCRIBE
TRANSCRIBE  → CLEAN
CLEAN       → SUMMARIZE
SUMMARIZE   → (done)
```

#### API Endpoint

```
POST /api/commands/run-pipeline
Body: {
  "podcast_slug": "my-podcast",
  "episode_slug": "episode-42",
  "target_state": "summarized"  // optional, defaults to summarized
}
Response: {
  "task_id": "uuid",
  "starting_stage": "download",
  "target_state": "summarized",
  "episode_id": "uuid"
}
```

### UI/UX Design

#### Option: Split Button with Dropdown (Recommended)

```
┌─────────────────────────────────┐
│  [▶ Downsample        ▾]        │
│      ├─ Downsample (next step)  │
│      └─ Run Full Pipeline →     │
└─────────────────────────────────┘
```

- Click button directly → runs next step only (existing behavior)
- Click dropdown arrow → shows options including "Run Full Pipeline"

#### Progress Display During Full Pipeline

```
┌─────────────────────────────────┐
│  Running Pipeline...            │
│                                 │
│  ✓ Downloaded                   │
│  ✓ Downsampled                  │
│  ● Transcribing... (45%)        │
│  ○ Clean                        │
│  ○ Summarize                    │
│                                 │
│  [Cancel Pipeline]              │
└─────────────────────────────────┘
```

---

## Part 2: Robust Failure Handling

### Current Behavior

- Tasks have 4 statuses: `pending`, `processing`, `completed`, `failed`
- No retry logic at task level (only within individual handlers via tenacity)
- Failed tasks stay failed with no recovery path
- No distinction between "API was down" vs "file is corrupt"
- Episode state doesn't reflect failures (just stays at previous state)

### Proposed Behavior

- Classify errors as **transient** (retryable) or **fatal** (needs intervention)
- Transient errors: automatic retry with exponential backoff
- Fatal errors: move to Dead Letter Queue (DLQ)
- Episode tracks failure state for UI display
- DLQ dashboard for manual review and retry

### Error Classification

#### Transient Errors (Auto-Retry)

| Error | Rationale |
|-------|-----------|
| HTTP 502, 503, 504 | Server temporarily unavailable |
| HTTP 429 | Rate limited, wait and retry |
| Network timeout | Temporary network issue |
| Connection reset | Network glitch |
| LLM rate limit | Wait and retry |
| LLM API 500 error | Provider issue, may recover |
| "Invalid JSON response" | LLM glitch, retry may work |
| Database locked | Concurrent access, retry |

#### Fatal Errors (DLQ)

| Error | Rationale |
|-------|-----------|
| HTTP 404 | Resource doesn't exist |
| HTTP 403, 401 | No permission |
| "Corrupt audio file" | File is broken |
| "Unsupported format" | Can't process this type |
| "Episode not found" | Data integrity issue |
| Disk full | Needs manual intervention |
| Invalid configuration | Won't fix itself |

### Database Changes

#### Tasks Table

```sql
-- Add retry tracking columns
ALTER TABLE tasks ADD COLUMN retry_count INTEGER DEFAULT 0;
ALTER TABLE tasks ADD COLUMN max_retries INTEGER DEFAULT 3;
ALTER TABLE tasks ADD COLUMN next_retry_at TIMESTAMP NULL;
ALTER TABLE tasks ADD COLUMN error_type TEXT NULL;  -- 'transient' or 'fatal'
ALTER TABLE tasks ADD COLUMN last_error TEXT NULL;  -- Most recent error message

-- Update status CHECK constraint
-- Add: 'retry_scheduled', 'dead'
```

#### Episodes Table

```sql
-- Add failure tracking columns
ALTER TABLE episodes ADD COLUMN failed_at_stage TEXT NULL;  -- 'download', 'transcribe', etc.
ALTER TABLE episodes ADD COLUMN failure_reason TEXT NULL;   -- Human-readable error
ALTER TABLE episodes ADD COLUMN failure_type TEXT NULL;     -- 'transient' or 'fatal'
ALTER TABLE episodes ADD COLUMN failed_at TIMESTAMP NULL;   -- When failure occurred
```

### New Enums and Exceptions

#### TaskStatus (Extended)

```python
class TaskStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    RETRY_SCHEDULED = "retry_scheduled"  # Waiting for backoff timer
    FAILED = "failed"                     # Exhausted retries (transient)
    DEAD = "dead"                         # Fatal error, in DLQ
```

#### ErrorType

```python
class ErrorType(str, Enum):
    TRANSIENT = "transient"
    FATAL = "fatal"
```

#### Custom Exceptions

```python
class TransientError(Exception):
    """Error that may resolve on retry (network issues, rate limits)."""
    pass

class FatalError(Exception):
    """Error that will never resolve (404, corrupt file, invalid format)."""
    pass
```

### Exponential Backoff Strategy

```python
def calculate_backoff(retry_count: int) -> timedelta:
    """
    Exponential backoff with jitter.

    Attempt 0 (first retry): ~5 seconds
    Attempt 1: ~30 seconds
    Attempt 2: ~3 minutes
    Attempt 3: Give up → FAILED or DEAD
    """
    base_seconds = 5
    multiplier = 6
    max_seconds = 600  # 10 minute cap

    delay = min(base_seconds * (multiplier ** retry_count), max_seconds)
    jitter = random.uniform(0.8, 1.2)  # ±20% jitter to prevent thundering herd

    return timedelta(seconds=delay * jitter)
```

| Retry | Delay |
|-------|-------|
| 0 | ~5 seconds |
| 1 | ~30 seconds |
| 2 | ~3 minutes |
| 3 | Give up |

### Task Execution Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                        TASK EXECUTION FLOW                          │
└─────────────────────────────────────────────────────────────────────┘

1. Task picked up (PENDING/RETRY_SCHEDULED → PROCESSING)
              │
              ▼
        ┌──────────┐
        │ Execute  │
        │ Handler  │
        └────┬─────┘
             │
    ┌────────┴────────┐
    │                 │
SUCCESS           EXCEPTION
    │                 │
    ▼                 ▼
COMPLETED     ┌─────────────┐
    │         │ Classify    │
    │         │ Error Type  │
    │         └──────┬──────┘
    │                │
    │      ┌─────────┴─────────┐
    │      │                   │
    │  TRANSIENT             FATAL
    │      │                   │
    │      ▼                   ▼
    │  retry_count < max?   Mark DEAD
    │      │                Update episode:
    │  ┌───┴───┐            - failed_at_stage
    │  │       │            - failure_reason
    │ YES      NO           - failure_type
    │  │       │                │
    │  ▼       ▼                ▼
    │ Calculate  Mark FAILED  (DLQ - needs
    │ backoff    Update episode manual review)
    │  │
    │  ▼
    │ RETRY_SCHEDULED
    │ next_retry_at = now + backoff
    │
    ▼
Chain enqueue next stage (if run_full_pipeline)
```

### Implementation Steps

#### Phase 1: Task Retry Infrastructure ✅ COMPLETE

- [x] **2.1** Create migration for tasks table (retry columns) ✅
- [x] **2.2** Add `ErrorType` enum to `queue_manager.py` ✅
- [x] **2.3** Create `TransientError` and `FatalError` exception classes ✅
- [x] **2.4** Update `TaskStatus` enum with new states ✅
- [x] **2.5** Update `Task` dataclass with new fields ✅
- [x] **2.6** Implement `calculate_backoff()` function ✅
- [x] **2.7** Update `QueueManager.get_next_task()` to check `next_retry_at` ✅
- [x] **2.8** Add `QueueManager.schedule_retry()` method ✅
- [x] **2.9** Add `QueueManager.mark_dead()` method ✅
- [x] **2.10** Update `TaskWorker._process_task()` with error classification logic ✅

#### Phase 2: Error Classification in Handlers ✅ COMPLETE

- [x] **2.11** Create error classifier utility function ✅ (`thestill/core/error_classifier.py`)
- [x] **2.12** Update `handle_download()` to raise `TransientError`/`FatalError` ✅
- [x] **2.13** Update `handle_downsample()` to raise `TransientError`/`FatalError` ✅
- [x] **2.14** Update `handle_transcribe()` to raise `TransientError`/`FatalError` ✅
- [x] **2.15** Update `handle_clean()` to raise `TransientError`/`FatalError` ✅
- [x] **2.16** Update `handle_summarize()` to raise `TransientError`/`FatalError` ✅

#### Phase 3: Episode Failure Tracking ✅ COMPLETE

- [x] **2.17** Create migration for episodes table (failure columns) ✅
- [x] **2.18** Update `Episode` model with failure fields ✅
  - Added: `failed_at_stage`, `failure_reason`, `failure_type`, `failed_at`
  - Added: `FailureType` enum (transient/fatal)
  - Added: `is_failed`, `can_retry`, `last_successful_state` properties
- [x] **2.19** Update computed `state` property to consider failures ✅
  - Episode returns `EpisodeState.FAILED` when `failed_at_stage` is set
- [x] **2.20** Add `Repository.mark_episode_failed()` method ✅ (in SqlitePodcastRepository)
- [x] **2.21** Add `Repository.clear_episode_failure()` method ✅ (in SqlitePodcastRepository)
- [x] **2.22** Update `TaskWorker` to call `mark_episode_failed()` on final failure ✅
  - Called for fatal errors (immediate)
  - Called for transient errors when retries exhausted

#### Phase 4: API Endpoints ✅ COMPLETE

- [x] **2.23** Add `GET /api/commands/dlq` - List dead tasks ✅
- [x] **2.24** Add `POST /api/commands/dlq/{task_id}/retry` - Retry dead task ✅
- [x] **2.25** Add `POST /api/commands/dlq/{task_id}/skip` - Mark as skipped/resolved ✅
- [x] **2.26** Add `GET /api/episodes/{id}/failure` - Get failure details ✅
- [x] **2.27** Add `POST /api/episodes/{id}/retry` - Clear failure and retry ✅

#### Phase 5: UI Updates

- [x] **2.28** Update `EpisodeCard` to show failure state ✅
- [x] **2.29** Add retry countdown display for `RETRY_SCHEDULED` tasks ✅
- [x] **2.30** Create DLQ dashboard page ✅ (`FailedTasks.tsx`)
- [x] **2.31** Add "Retry" and "Skip" actions to DLQ items ✅
- [x] **2.32** Add failure details modal/panel ✅ (`FailureDetailsModal.tsx`)
- [x] **2.33** Add "Retry All Transient" bulk action ✅

### UI Mockups

#### Episode Card - Failure State

```
┌──────────────────────────────────────┐
│ Episode: "Interview with..."         │
│                                      │
│ State: ⚠️ Download Failed            │
│ Error: HTTP 404 - File not found     │
│ Type: Fatal (will not retry)         │
│                                      │
│ [🔄 Retry Download] [📋 View Details]│
└──────────────────────────────────────┘
```

#### Episode Card - Retry Scheduled

```
┌──────────────────────────────────────┐
│ Episode: "Tech Talk #42"             │
│                                      │
│ State: ⏳ Transcription Retry        │
│ Attempt 2/3 in 4m 30s                │
│ Last error: API rate limit (429)     │
│                                      │
│ [❌ Cancel Retry] [📋 View Errors]   │
└──────────────────────────────────────┘
```

#### DLQ Dashboard

```
┌─────────────────────────────────────────────────────────────────────┐
│ 🏥 Dead Letter Queue (3 items needing attention)                    │
├─────────────────────────────────────────────────────────────────────┤
│ Episode              │ Stage      │ Error               │ Actions  │
├──────────────────────┼────────────┼─────────────────────┼──────────┤
│ Podcast #123         │ download   │ 404 Not Found       │ [Retry]  │
│                      │            │                     │ [Skip]   │
├──────────────────────┼────────────┼─────────────────────┼──────────┤
│ Tech Talk #42        │ transcribe │ Corrupt audio file  │ [Retry]  │
│                      │            │                     │ [Skip]   │
├──────────────────────┼────────────┼─────────────────────┼──────────┤
│ Interview...         │ clean      │ LLM quota exceeded  │ [Retry]  │
│                      │            │                     │ [Skip]   │
└─────────────────────────────────────────────────────────────────────┘

[🔄 Retry All] [🗑️ Clear Resolved]
```

---

## Integration: Full Pipeline + Failure Handling

### Behavior When Combined

1. User clicks "Run Full Pipeline"
2. Download task created with `metadata.run_full_pipeline = true`
3. Download succeeds → auto-enqueue Downsample (with same metadata)
4. Downsample fails with transient error → schedule retry
5. Retry succeeds → auto-enqueue Transcribe
6. Transcribe fails with fatal error → mark DEAD, update episode
7. **Pipeline stops** - no further stages enqueued
8. User sees episode in failed state, can retry from DLQ

### Chain Enqueueing Rules

```python
def should_chain_enqueue(task: Task, success: bool) -> bool:
    """Determine if next stage should be enqueued."""
    if not success:
        return False

    if not task.metadata:
        return False

    if not task.metadata.get("run_full_pipeline"):
        return False

    # Check if we've reached target state
    target = task.metadata.get("target_state", "summarized")
    next_stage = get_next_stage(task.stage)

    if next_stage is None:
        return False  # Already at final stage

    return True
```

---

## Migration Strategy

### Order of Implementation

1. **Phase 1**: Task retry infrastructure (2.1-2.10)
2. **Phase 2**: Error classification in handlers (2.11-2.16)
3. **Phase 3**: Episode failure tracking (2.17-2.22)
4. **Phase 1 (Pipeline)**: Chain enqueueing backend (1.1-1.6)
5. **Phase 4**: API endpoints (2.23-2.27)
6. **Phase 5 + Pipeline UI**: All UI updates (1.7-1.9, 2.28-2.33)

### Database Migrations

```
migrations/
├── 001_add_task_retry_columns.sql
├── 002_add_task_metadata_column.sql
├── 003_add_episode_failure_columns.sql
└── 004_update_task_status_constraint.sql
```

### Backward Compatibility

- Existing tasks with `status = 'failed'` remain valid
- New columns have defaults, no data migration needed
- Episode failure fields are nullable, existing episodes unaffected

---

## Testing Strategy

### Unit Tests

- [ ] `test_calculate_backoff()` - Verify exponential delays
- [ ] `test_error_classification()` - Verify correct error types
- [ ] `test_retry_scheduling()` - Verify next_retry_at calculation
- [ ] `test_chain_enqueueing()` - Verify metadata propagation
- [ ] `test_get_next_stage()` - Verify stage progression

### Integration Tests

- [ ] Test full pipeline from discovered to summarized
- [ ] Test transient error → retry → success flow
- [ ] Test fatal error → DLQ flow
- [ ] Test pipeline stops on fatal error
- [ ] Test cancel pipeline mid-execution

### Manual Testing Scenarios

1. Run full pipeline on healthy episode - verify all stages complete
2. Simulate network timeout during download - verify retry
3. Use 404 URL - verify moves to DLQ
4. Cancel pipeline mid-transcription - verify stops cleanly
5. Retry from DLQ - verify episode recovers

---

## Open Questions

1. **Max retries per stage**: Should all stages have same max (3)? Or should expensive stages like transcription have fewer?

2. **Retry across server restarts**: Should `RETRY_SCHEDULED` tasks be recovered on restart, or reset to `PENDING`?

3. **Notification**: Should we notify user when task moves to DLQ? (Email, webhook, in-app?)

4. **Auto-cleanup**: Should completed/failed tasks be auto-deleted after N days? (Currently 7 days)

5. **Parallel pipelines**: If user has 10 episodes, should "Run All Pipelines" process them sequentially or in parallel?

---

## Success Metrics

- **Reduced manual intervention**: Fewer tasks stuck in `failed` that could have auto-recovered
- **Faster recovery**: Transient failures resolve without user action
- **Clear visibility**: Users understand why something failed and what to do
- **Pipeline completion rate**: % of "Run Full Pipeline" requests that complete successfully
