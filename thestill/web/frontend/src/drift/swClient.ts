// Spec #116 §4 step 6 — the page's side of public/sw.js: register it, keep
// what it reports about the audio element's requests, unregister it when
// the session may no longer align.
import { crossOriginLoadingEnabled } from './corsVerdict'
import type { MediaReport } from './verify'

export const SW_URL = '/sw.js'

/** How the worker stood when a play was verified. */
export type SwState =
  | 'unsupported' // no service workers here (or a private window)
  | 'not_controlling' // registered, but this page loaded before it took control
  | 'element_not_cors' // the element loaded without crossorigin; nothing is readable
  | 'no_requests' // controlling, yet it saw no request from the element
  | 'opaque' // it saw requests but could not read them
  | 'ok' // it read at least one response

const MAX_REPORTS = 400
const reports: MediaReport[] = []
let listening = false
let registration: Promise<ServiceWorkerRegistration | null> | null = null

function supported(): boolean {
  return typeof navigator !== 'undefined' && 'serviceWorker' in navigator
}

/**
 * Start keeping what an installed worker reports. Called as soon as a
 * session may align, not when the reader opens: playback can start
 * elsewhere (Continue listening, a restored session), and the worker's
 * report of the element's first response is sent once.
 */
export function listenForMediaReports(): void {
  if (listening || !supported()) return
  listening = true
  navigator.serviceWorker.addEventListener('message', (event: MessageEvent) => {
    const data = event.data as { kind?: string; report?: MediaReport } | null
    if (data?.kind !== 'drift-media' || !data.report) return
    reports.push(data.report)
    if (reports.length > MAX_REPORTS) reports.splice(0, reports.length - MAX_REPORTS)
  })
}

/** Register the worker (idempotent). Never throws. */
export function ensureDriftServiceWorker(): Promise<ServiceWorkerRegistration | null> {
  if (!supported()) return Promise.resolve(null)
  listenForMediaReports()
  registration ??= navigator.serviceWorker.register(SW_URL, { scope: '/' }).catch(() => null)
  return registration
}

/** Remove the worker, if this origin has it. Never throws. */
export async function removeDriftServiceWorker(): Promise<void> {
  registration = null
  if (!supported()) return
  try {
    const all = await navigator.serviceWorker.getRegistrations()
    await Promise.all(
      all
        .filter((r) => [r.active, r.waiting, r.installing].some((w) => w?.scriptURL.endsWith(SW_URL)))
        .map((r) => r.unregister()),
    )
  } catch {
    // Nothing to remove, or the browser refused: either way nothing runs.
  }
}

export function serviceWorkerSupported(): boolean {
  return supported()
}

export function serviceWorkerControlling(): boolean {
  return supported() && navigator.serviceWorker.controller !== null
}

/**
 * The element's responses since `sinceMs` for one audio load. After a
 * redirect the browser may ask the final URL directly (Safari does), so a
 * report counts when it names the enclosure URL or the host that served
 * the fetched copy.
 */
export function mediaReportsFor(audioUrl: string, finalHost: string | null, sinceMs: number): MediaReport[] {
  return reports.filter((report) => {
    if (report.t < sinceMs) return false
    if (report.url === audioUrl) return true
    try {
      return finalHost !== null && new URL(report.url).hostname === finalHost
    } catch {
      return false
    }
  })
}

/** Test seam. */
export function resetDriftServiceWorkerClient(): void {
  reports.length = 0
  registration = null
  listening = false
}

// A page load by a session that could align last time listens from the
// start, before the session has even loaded (see corsVerdict.ts).
if (crossOriginLoadingEnabled()) listenForMediaReports()

/** Test seam: what a `drift-media` message does. */
export function recordMediaReport(report: MediaReport): void {
  reports.push(report)
}
