# Task Queue Monitor Page

## Overview

Replace the "Failed Tasks" page with a comprehensive "Task Queue" monitor that shows all task states: pending, processing, retry scheduled, failed (transient), and dead (DLQ/fatal).

## Problem

Currently:

- `/api/commands/dlq` only returns `status='dead'` tasks
- Tasks marked `status='failed'` (e.g., server restart interruptions) are invisible
- The "Transient Errors" counter on FailedTasks page always shows 0 for non-DLQ failures
- Users have no visibility into pending, processing, or retry-scheduled tasks

## Solution

Create a unified Task Queue Monitor page with:

1. Stats overview showing counts for each task state
2. Tabbed interface to filter by state
3. Task cards with actions (retry, cancel, skip)
4. Real-time updates via polling

---

## Implementation Plan

### 1. Backend: New Query Methods in QueueManager

**File:** `thestill/core/queue_manager.py`

Add methods to query tasks by status:

```python
def get_tasks_by_status(
    self,
    status: Union[TaskStatus, List[TaskStatus]],
    limit: int = 100,
    offset: int = 0
) -> List[Task]:
    """Get tasks filtered by status(es) with pagination."""

def get_active_tasks(self, limit: int = 100) -> List[Task]:
    """Get pending + processing + retry_scheduled tasks."""

def get_failed_tasks(self, limit: int = 100) -> List[Task]:
    """Get failed (transient) tasks - exhausted retries but not fatal."""
```

### 2. Backend: New API Endpoint

**File:** `thestill/web/routes/api_commands.py`

Add unified tasks endpoint:

```python
@router.get("/tasks")
async def list_tasks(
    status: Optional[str] = None,  # pending,processing,retry_scheduled,failed,dead or "active" or "failed_all"
    limit: int = 100,
    offset: int = 0,
    state: AppState = Depends(get_app_state),
) -> TaskListResponse:
    """List tasks with optional status filter."""
```

Response model:

```python
class TaskResponse(BaseModel):
    task_id: str
    episode_id: str
    episode_title: str
    episode_slug: str
    podcast_title: str
    podcast_slug: str
    stage: str
    status: str  # pending, processing, retry_scheduled, failed, dead
    error_message: Optional[str]
    error_type: Optional[str]  # transient, fatal
    retry_count: int
    max_retries: int
    next_retry_at: Optional[str]
    created_at: Optional[str]
    started_at: Optional[str]
    completed_at: Optional[str]

class TaskListResponse(BaseModel):
    status: str
    tasks: List[TaskResponse]
    count: int
    stats: Dict[str, int]  # {pending: 5, processing: 1, ...}
```

### 3. Frontend: New API Client Functions

**File:** `thestill/web/frontend/src/api/client.ts`

```typescript
export async function getTasks(
  status?: string,
  limit?: number,
  offset?: number
): Promise<TaskListResponse>

export async function retryTask(taskId: string): Promise<TaskActionResponse>
export async function cancelTask(taskId: string): Promise<TaskActionResponse>
```

### 4. Frontend: New Types

**File:** `thestill/web/frontend/src/api/types.ts`

```typescript
export type TaskStatus = 'pending' | 'processing' | 'retry_scheduled' | 'failed' | 'dead'

export interface QueueTask {
  task_id: string
  episode_id: string
  episode_title: string
  episode_slug: string
  podcast_title: string
  podcast_slug: string
  stage: PipelineStage
  status: TaskStatus
  error_message: string | null
  error_type: 'transient' | 'fatal' | null
  retry_count: number
  max_retries: number
  next_retry_at: string | null
  created_at: string | null
  started_at: string | null
  completed_at: string | null
}

export interface TaskListResponse {
  status: string
  tasks: QueueTask[]
  count: number
  stats: Record<TaskStatus, number>
}
```

### 5. Frontend: New Hooks

**File:** `thestill/web/frontend/src/hooks/useApi.ts`

```typescript
export function useTasks(status?: string, limit = 100) {
  return useQuery({
    queryKey: ['tasks', status, limit],
    queryFn: () => getTasks(status, limit),
    refetchInterval: 5000,  // 5s for active monitoring
  })
}

export function useRetryTask() { ... }
export function useCancelTask() { ... }
```

### 6. Frontend: TaskQueue Page

**File:** `thestill/web/frontend/src/pages/TaskQueue.tsx` (new file)

Layout:

```
┌─────────────────────────────────────────────────────────────┐
│ Task Queue                                                  │
│ Monitor background processing tasks                         │
├─────────────────────────────────────────────────────────────┤
│ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐│
│ │ Pending │ │Processing│ │ Retry   │ │ Failed  │ │  Dead   ││
│ │    5    │ │    1    │ │    2    │ │    3    │ │    1    ││
│ └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘│
├─────────────────────────────────────────────────────────────┤
│ [All] [Active] [Pending] [Processing] [Retry] [Failed] [DLQ]│
├─────────────────────────────────────────────────────────────┤
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ Episode Title                              [transcribe] │ │
│ │ Podcast Name                                            │ │
│ │ Status: processing | Started: 2 min ago                 │ │
│ │ Progress: 45% - Transcribing audio...                   │ │
│ └─────────────────────────────────────────────────────────┘ │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ Episode Title                              [transcribe] │ │
│ │ Podcast Name                     [transient] [Retry 2/3]│ │
│ │ Status: retry_scheduled | Next retry: 30s               │ │
│ │ Error: Network timeout                     [Cancel]     │ │
│ └─────────────────────────────────────────────────────────┘ │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ Episode Title                                [download] │ │
│ │ Podcast Name                        [fatal] [Retry 3/3] │ │
│ │ Status: dead | Failed: 5 min ago                        │ │
│ │ Error: 404 Not Found              [Retry] [Skip]        │ │
│ └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

Features:

- Stats cards for each status (clickable to filter)
- Tab bar for quick filtering
- Task cards showing:
  - Episode/podcast info with link
  - Stage badge (download, transcribe, etc.)
  - Status badge with appropriate color
  - Error type badge (transient/fatal) when applicable
  - Retry count
  - Timestamps (created, started, next retry)
  - Progress for processing tasks (via SSE)
  - Actions: Retry (for failed/dead), Cancel (for retry_scheduled), Skip (for dead)
- Empty state when no tasks match filter
- 5-second polling for real-time updates

### 7. Frontend: Update Navigation

**File:** `thestill/web/frontend/src/App.tsx`

- Change route from `/failed` to `/queue`
- Update nav label from "Failed Tasks" to "Task Queue"

**File:** `thestill/web/frontend/src/components/Layout.tsx`

- Update sidebar link

### 8. Cleanup: Remove Old Page

**File to delete:** `thestill/web/frontend/src/pages/FailedTasks.tsx`

Move any reusable components (like FailureDetailsModal usage) to the new page.

---

## Status Color Scheme

| Status | Background | Text | Badge |
|--------|-----------|------|-------|
| pending | gray-100 | gray-700 | Waiting |
| processing | blue-100 | blue-700 | Processing |
| retry_scheduled | yellow-100 | yellow-700 | Retry in Xs |
| failed | orange-100 | orange-700 | Failed |
| dead | red-100 | red-700 | Dead (DLQ) |

---

## Files to Modify

1. `thestill/core/queue_manager.py` - Add query methods
2. `thestill/web/routes/api_commands.py` - Add /tasks endpoint
3. `thestill/web/frontend/src/api/client.ts` - Add API functions
4. `thestill/web/frontend/src/api/types.ts` - Add types
5. `thestill/web/frontend/src/hooks/useApi.ts` - Add hooks
6. `thestill/web/frontend/src/pages/TaskQueue.tsx` - New page
7. `thestill/web/frontend/src/App.tsx` - Update route
8. `thestill/web/frontend/src/components/Layout.tsx` - Update nav

## Files to Delete

1. `thestill/web/frontend/src/pages/FailedTasks.tsx`

## Endpoints to Remove

The existing DLQ-specific endpoints will be replaced by the unified `/api/commands/tasks` endpoint:

- `GET /api/commands/dlq` → replaced by `GET /api/commands/tasks?status=dead`
- `POST /api/commands/dlq/{task_id}/retry` → replaced by `POST /api/commands/tasks/{task_id}/retry`
- `POST /api/commands/dlq/{task_id}/skip` → replaced by `POST /api/commands/tasks/{task_id}/skip`
- `POST /api/commands/dlq/retry-all` → replaced by `POST /api/commands/tasks/retry-all`

This simplifies the API surface and avoids duplicate functionality.

---

## Verification

1. **Backend tests:**
   - Run `pytest tests/` to ensure no regressions
   - Test new `/api/commands/tasks` endpoint with curl

2. **Frontend build:**
   - Run `cd thestill/web/frontend && npm run build`
   - Verify no TypeScript errors

3. **Manual testing:**
   - Start server: `thestill server --reload`
   - Navigate to `/queue`
   - Verify stats show correct counts
   - Filter by each status tab
   - Trigger a task and watch it appear in "Processing"
   - Force a failure and verify it appears in "Failed" or "Dead"
   - Test Retry, Cancel, Skip actions

4. **Edge cases:**
   - Empty queue (all zeros)
   - Large queue (pagination if implemented)
   - Server restart during processing (verify task shows in Failed)
