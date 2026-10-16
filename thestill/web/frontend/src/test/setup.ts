import '@testing-library/jest-dom'
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'
import { listeningCache } from '../contexts/listening/listeningCache'

// Cleanup after each test
afterEach(() => {
  cleanup()
  // Spec #90 — the player mirrors its session into sessionStorage and a
  // module-level position cache; neither may leak into the next test (a
  // leaked snapshot restores a track on the next PlayerProvider mount).
  sessionStorage.clear()
  listeningCache.clear()
})
