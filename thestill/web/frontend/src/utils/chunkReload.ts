// A deploy replaces the hashed page chunks. A tab opened before the deploy
// still holds the old index, so its next lazy import() asks for a file that
// no longer exists and rejects. Reloading picks up the new index; the guard
// stops a reload loop when the chunk is missing for some other reason.

const RELOAD_KEY = 'thestill:chunk-reload-at'
const RELOAD_GUARD_MS = 30_000

const CHUNK_ERROR_PATTERNS = [
  /Failed to fetch dynamically imported module/i,
  /error loading dynamically imported module/i,
  /Importing a module script failed/i,
  /Unable to preload CSS/i,
]

export function isChunkLoadError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error)
  return CHUNK_ERROR_PATTERNS.some((pattern) => pattern.test(message))
}

/** Reloads the page unless it already did so moments ago. Returns whether it reloaded. */
export function reloadForStaleChunk(): boolean {
  try {
    const last = Number(sessionStorage.getItem(RELOAD_KEY) ?? 0)
    if (Date.now() - last < RELOAD_GUARD_MS) return false
    sessionStorage.setItem(RELOAD_KEY, String(Date.now()))
  } catch {
    // No sessionStorage means no loop guard, so leave the fallback UI to it.
    return false
  }
  window.location.reload()
  return true
}

export function installChunkReloadHandler(): void {
  window.addEventListener('vite:preloadError', (event) => {
    if (reloadForStaleChunk()) event.preventDefault()
  })
}
