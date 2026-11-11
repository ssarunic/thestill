import '@testing-library/jest-dom'
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'
import { listeningCache } from '../contexts/listening/listeningCache'

// Spec #116 — drift alignment runs only in browsers it has been proven in
// (src/drift/support.ts). jsdom's own user agent is none of them, so the
// suite presents itself as desktop Chrome; tests of other browsers pass a
// user agent explicitly.
export const CHROME_UA =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36'
Object.defineProperty(window.navigator, 'userAgent', { configurable: true, get: () => CHROME_UA })

// Cleanup after each test
afterEach(() => {
  cleanup()
  // Spec #90 — the player mirrors its session into sessionStorage and a
  // module-level position cache; neither may leak into the next test (a
  // leaked snapshot restores a track on the next PlayerProvider mount).
  sessionStorage.clear()
  listeningCache.clear()
})
