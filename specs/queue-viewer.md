# Task Queue Viewer

## Overview

Add a Task Queue Viewer page to thestill web that provides visibility into background processing:

- Currently processing task
- Pending tasks in queue
- Retry-scheduled tasks
- Recently completed tasks (for context and future statistics)
- Ability to "bump" pending tasks to the front of the queue

## Features

### Task Display

Each task shows:

- Episode title and podcast name
- Pipeline stage (download, downsample, transcribe, clean, summarize)
- Audio duration (from episode metadata)
- Time in queue (since created_at)
- Processing time (for active tasks)
- Retry count and next retry time (for retry-scheduled tasks)

### Bump Functionality

Users can move any pending task to the front of the queue by clicking a "bump" button. This sets the task's priority higher than all other pending tasks.

### Polling

- 5-second polling when active tasks exist
- 15-second polling when queue is idle

## Implementation

### Backend

#### QueueManager Methods

**File:** `thestill/core/queue_manager.py`

```python
def bump_task(self, task_id: str) -> bool:
    """Move a pending task to the front of the queue."""
    # Set priority to max(current priorities) + 1

def get_active_tasks(self, include_completed: int = 10) -> dict:
    """Get all active and recently completed tasks."""
    # Returns: {pending, processing, retry_scheduled, completed}
```

#### API Endpoints

**File:** `thestill/web/routes/api_commands.py`

```
GET  /api/commands/queue/tasks?completed_limit=10
POST /api/commands/queue/task/{task_id}/bump
```

Response model `QueuedTaskWithContext`:

- task_id, episode_id, stage, status, priority
- episode_title, episode_slug, podcast_title, podcast_slug
- created_at, started_at, completed_at
- time_in_queue_seconds, processing_time_seconds
- duration_seconds, duration_formatted
- retry_count, next_retry_at

### Frontend

#### Types

**File:** `thestill/web/frontend/src/api/types.ts`

```typescript
interface QueuedTaskWithContext {
  task_id: string
  episode_title: string
  podcast_title: string
  stage: PipelineStage
  status: string
  time_in_queue_seconds: number | null
  processing_time_seconds: number | null
  duration_formatted: string | null
  retry_count: number
  next_retry_at: string | null
}

interface QueueTasksResponse {
  worker_running: boolean
  processing_task: QueuedTaskWithContext | null
  pending_tasks: QueuedTaskWithContext[]
  retry_scheduled_tasks: QueuedTaskWithContext[]
  completed_tasks: QueuedTaskWithContext[]
  pending_count: number
  processing_count: number
  retry_scheduled_count: number
  completed_shown: number
}
```

#### API Client

**File:** `thestill/web/frontend/src/api/client.ts`

```typescript
export async function getQueueTasks(completedLimit?: number): Promise<QueueTasksResponse>
export async function bumpQueueTask(taskId: string): Promise<{ status: string }>
```

#### Hooks

**File:** `thestill/web/frontend/src/hooks/useApi.ts`

```typescript
export function useQueueTasks(completedLimit = 10)
export function useBumpQueueTask()
```

#### Page Component

**File:** `thestill/web/frontend/src/pages/QueueViewer.tsx`

Layout:

```
┌─────────────────────────────────────────────────────────────┐
│ Task Queue                              [Worker: Running]   │
│ Monitor background processing tasks                         │
├─────────────────────────────────────────────────────────────┤
│ ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐    │
│ │ Processing│ │  Pending  │ │   Retry   │ │ Completed │    │
│ │     1     │ │     5     │ │     2     │ │    10     │    │
│ └───────────┘ └───────────┘ └───────────┘ └───────────┘    │
├─────────────────────────────────────────────────────────────┤
│ Currently Processing                                        │
│ ┌─────────────────────────────────────────────────────────┐│
│ │ Episode Title                    [transcribe] 1:08:00   ││
│ │ Podcast Name                                            ││
│ │ Processing for 5m 32s                                   ││
│ └─────────────────────────────────────────────────────────┘│
├─────────────────────────────────────────────────────────────┤
│ Pending (5)                                                 │
│ ┌─────────────────────────────────────────────────────────┐│
│ │ Episode Title                    [downsample] 45:30 [↑] ││
│ │ Podcast Name                                            ││
│ │ In queue for 2m 15s                                     ││
│ └─────────────────────────────────────────────────────────┘│
│ ...                                                         │
├─────────────────────────────────────────────────────────────┤
│ Recently Completed (10)                                     │
│ ┌─────────────────────────────────────────────────────────┐│
│ │ Episode Title                    [summarize] 32:00      ││
│ │ Podcast Name                                            ││
│ │ Completed 2m ago                                        ││
│ └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
```

#### Navigation

Add "Task Queue" link to sidebar and mobile navigation drawer at route `/queue`.

## Files to Change

| File | Action |
|------|--------|
| `thestill/core/queue_manager.py` | Add `bump_task()` and `get_active_tasks()` |
| `thestill/web/routes/api_commands.py` | Add endpoints and response models |
| `thestill/web/frontend/src/api/types.ts` | Add TypeScript types |
| `thestill/web/frontend/src/api/client.ts` | Add API functions |
| `thestill/web/frontend/src/hooks/useApi.ts` | Add hooks |
| `thestill/web/frontend/src/pages/QueueViewer.tsx` | Create new page |
| `thestill/web/frontend/src/App.tsx` | Add route |
| `thestill/web/frontend/src/components/Layout.tsx` | Add nav item |
| `thestill/web/frontend/src/components/NavigationDrawer.tsx` | Add mobile nav |

## Verification

1. `curl http://localhost:8000/api/commands/queue/tasks | jq`
2. `curl -X POST http://localhost:8000/api/commands/queue/task/{id}/bump`
3. Navigate to `/queue` in browser
4. Click bump button and verify task moves to top
5. Verify polling behavior (5s active, 15s idle)
