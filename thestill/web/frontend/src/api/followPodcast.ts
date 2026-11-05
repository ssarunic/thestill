// Follow a podcast by feed URL and resolve only once it is really followed.
//
// ``POST /api/commands/add`` runs one add at a time for the whole server: a
// second POST while one is running gets a 409, and a 200 only means the
// background add *started*. So a bare ``addPodcast`` call fails fast clicks
// on several Follow buttons and reports success before the feed has loaded.
// This helper queues this tab's follows, waits out a 409 from someone else's
// add, then polls *this* add's status (by ``task_id``) until it finishes.
import { addPodcast, getAddPodcastStatus } from './client'
import { ApiStatusError } from './errors'
import type { FollowSource } from './types'

const POLL_INTERVAL_MS = 500
// How long to wait for the server's add slot, and then, separately, for the
// add itself (a new feed's first refresh can be slow).
const SLOT_TIMEOUT_MS = 60_000
const ADD_TIMEOUT_MS = 90_000
// One request; a stalled socket must not wedge the queue for the whole tab.
const REQUEST_TIMEOUT_MS = 20_000

// The tail of this tab's follow queue. Each follow starts after the previous
// one settles, success or failure.
let queueTail: Promise<unknown> = Promise.resolve()

export function followPodcast(url: string, source: FollowSource): Promise<void> {
  const run = queueTail.then(() => addAndWait(url, source))
  queueTail = run.catch(() => undefined)
  return run
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

function withTimeout<T>(promise: Promise<T>): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(
      () => reject(new Error('The server is not responding. Try again in a moment.')),
      REQUEST_TIMEOUT_MS,
    )
  })
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer))
}

async function addAndWait(url: string, source: FollowSource): Promise<void> {
  const taskId = await startAdd(url, source)
  await waitForAddTask(taskId)
}

/** POST the add, waiting out a 409 while another add holds the server's slot. */
async function startAdd(url: string, source: FollowSource): Promise<string | null> {
  const deadline = Date.now() + SLOT_TIMEOUT_MS
  for (;;) {
    try {
      const started = await withTimeout(addPodcast({ url, source }))
      return started.task_id ?? null
    } catch (err) {
      if (!(err instanceof ApiStatusError && err.status === 409)) throw err
      if (Date.now() + POLL_INTERVAL_MS >= deadline) {
        throw new Error('Another podcast is still being added. Try again in a moment.')
      }
      await sleep(POLL_INTERVAL_MS)
    }
  }
}

async function waitForAddTask(taskId: string | null): Promise<void> {
  const deadline = Date.now() + ADD_TIMEOUT_MS
  for (;;) {
    const task = await withTimeout(getAddPodcastStatus(taskId))
    if (task.status === 'failed') throw new Error(task.error || 'Failed to add podcast')
    if (task.status === 'completed') {
      if (task.result?.is_following === false) {
        throw new Error('The podcast was added, but following it failed. Try again.')
      }
      return
    }
    // Only an untargeted poll (a server without task ids) can see 'none'.
    if (task.status === 'none' && !taskId) return
    if (Date.now() + POLL_INTERVAL_MS >= deadline) {
      throw new Error('Adding the podcast is taking longer than expected. Check the Podcasts page in a moment.')
    }
    await sleep(POLL_INTERVAL_MS)
  }
}

/** Test hook: forget queued follows so one test can't block the next. */
export function resetFollowQueueForTests(): void {
  queueTail = Promise.resolve()
}
