// Follow a podcast by feed URL and resolve only once it is really followed.
//
// ``POST /api/commands/add`` runs one add at a time for the whole server: a
// second POST while one is running gets a 409, and a 200 only means the
// background add *started*. So a bare ``addPodcast`` call fails fast clicks
// on several Follow buttons and reports success before the feed has loaded.
// This helper queues this tab's follows one after another, waits out a 409
// from someone else's add, and then waits for the add itself to finish.
import { addPodcast, getAddPodcastStatus } from './client'
import { ApiStatusError } from './errors'
import type { FollowSource } from './types'

const POLL_INTERVAL_MS = 500
const TIMEOUT_MS = 60_000

// The tail of this tab's follow queue. Each follow starts after the previous
// one settles, success or failure.
let queueTail: Promise<unknown> = Promise.resolve()

export function followPodcast(url: string, source: FollowSource): Promise<void> {
  const run = queueTail.then(() => addAndWait(url, source))
  queueTail = run.catch(() => undefined)
  return run
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

async function addAndWait(url: string, source: FollowSource): Promise<void> {
  const deadline = Date.now() + TIMEOUT_MS
  for (;;) {
    try {
      await addPodcast({ url, source })
      break
    } catch (err) {
      // Another add (another tab, or another user) is running; wait our turn.
      if (!(err instanceof ApiStatusError && err.status === 409)) throw err
      if (Date.now() >= deadline) {
        throw new Error('Another podcast is still being added. Try again in a moment.')
      }
      await sleep(POLL_INTERVAL_MS)
    }
  }
  await waitForAddTask(deadline)
}

async function waitForAddTask(deadline: number): Promise<void> {
  while (Date.now() < deadline) {
    const task = await getAddPodcastStatus()
    if (task.status === 'failed') throw new Error(task.error || 'Failed to add podcast')
    if (task.status === 'completed' || task.status === 'none') return
    await sleep(POLL_INTERVAL_MS)
  }
  throw new Error('Adding the podcast is taking longer than expected. Check the Podcasts page in a moment.')
}

/** Test hook: forget queued follows so one test can't block the next. */
export function resetFollowQueueForTests(): void {
  queueTail = Promise.resolve()
}
