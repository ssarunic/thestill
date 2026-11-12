// Spec #116 §7.2 — the alignment report as `POST …/drift-outcome` takes it.
// Nothing here identifies the listener: a host name, a browser family and
// numbers.
import type { DriftReport } from './run'
import type { SwState } from './swClient'
import type { Verification } from './verify'

const round = (value: number | null, digits = 3): number | null =>
  value === null ? null : Math.round(value * 10 ** digits) / 10 ** digits

/** "Safari 27", "Chrome 157", "Firefox 150", or null when unrecognised. */
export function browserLabel(userAgent: string): string | null {
  const pick = (pattern: RegExp, family: string) => {
    const match = pattern.exec(userAgent)
    return match ? `${family} ${match[1]}` : null
  }
  // Order matters: Edge and Chrome both say "Safari", Edge also says "Chrome".
  return (
    pick(/\bEdg(?:e|A|iOS)?\/(\d{1,4})/, 'Edge') ??
    pick(/\bFirefox\/(\d{1,4})/, 'Firefox') ??
    pick(/\bFxiOS\/(\d{1,4})/, 'Firefox') ??
    pick(/\b(?:Chrome|CriOS)\/(\d{1,4})/, 'Chrome') ??
    (/\bSafari\//.test(userAgent) ? pick(/\bVersion\/(\d{1,4})/, 'Safari') : null)
  )
}

export interface DriftOutcomeBody {
  generation: string
  outcome: DriftReport['outcome']
  spans: { src_start: number; src_end: number; offset: number }[]
  gaps_confirmed_s: number | null
  unresolved_s: number | null
  listener_ads_s: number | null
  coverage: number | null
  anchors_total: number | null
  anchors_hit: number | null
  anchors_inlier: number | null
  anchors_outlier: number | null
  // The two checks of §4 step 6. Null means "not known", never "passed":
  // the element loaded without crossorigin, or the worker saw nothing.
  verify_length_ok: boolean | null
  verify_ranges_checked: number | null
  verify_ranges_matched: number | null
  sw_state: SwState | null
  // 0 for a play's first report; 1 and up when a later range changed the
  // verdict of the same play, so counts of plays can leave those out.
  revision: number
  // Whether the map was put in force in the player for this play.
  applied: boolean
  audio_host: string | null
  audio_bytes: number | null
  fetched_bytes: number | null
  fetch_ms: number | null
  fit_ms: number | null
  browser: string | null
  error: string | null
}

const HOST = /^[A-Za-z0-9.-]{1,253}$/
const isMapOutcome = (outcome: DriftReport['outcome']) => outcome === 'aligned' || outcome === 'identity'
/** The server takes at most this many spans; a real map has a handful. */
export const MAX_REPORTED_SPANS = 64
/** The server refuses a body over this many bytes (`DRIFT_OUTCOME_MAX_BYTES`). */
export const MAX_REPORT_BYTES = 4096

const byteLength = (body: DriftOutcomeBody): number => new TextEncoder().encode(JSON.stringify(body)).length

/** What the reader learned about the play this report describes. */
export interface PlayCheck {
  swState: SwState
  verification: Verification | null
}

export function toOutcomeBody(
  report: DriftReport,
  generation: string,
  userAgent: string,
  check: PlayCheck | null = null,
  revision = 0,
  applied = false,
): DriftOutcomeBody {
  const outlier =
    report.anchorsHit !== null && report.anchorsInlier !== null ? report.anchorsHit - report.anchorsInlier : null
  const body: DriftOutcomeBody = {
    generation: report.generation ?? generation,
    // A map built from another copy than the one playing is no map.
    outcome: check?.verification?.verdict === 'mismatch' && isMapOutcome(report.outcome) ? 'variant_mismatch' : report.outcome,
    spans: report.spans.slice(0, MAX_REPORTED_SPANS).map((span) => ({
      src_start: round(span.srcStartS) as number,
      src_end: round(span.srcEndS) as number,
      offset: round(span.offsetS) as number,
    })),
    gaps_confirmed_s: round(report.gapsConfirmedS),
    unresolved_s: round(report.unresolvedS),
    listener_ads_s: round(report.listenerAdsS),
    coverage: round(report.coverage, 4),
    anchors_total: report.anchorsTotal,
    anchors_hit: report.anchorsHit,
    anchors_inlier: report.anchorsInlier,
    anchors_outlier: outlier,
    verify_length_ok: check?.verification?.lengthOk ?? null,
    verify_ranges_checked: check?.verification?.rangesChecked ?? null,
    verify_ranges_matched: check?.verification?.rangesMatched ?? null,
    sw_state: check?.swState ?? null,
    revision,
    applied,
    audio_host: report.audioHost && HOST.test(report.audioHost) ? report.audioHost : null,
    audio_bytes: report.audioBytes,
    fetched_bytes: report.fetchedBytes,
    fetch_ms: report.fetchMs,
    fit_ms: report.fitMs,
    browser: browserLabel(userAgent),
    error: report.error,
  }
  // 64 spans of long timestamps can overrun the byte cap on their own; drop
  // spans from the end so a fragmented map still reports its outcome.
  while (body.spans.length > 0 && byteLength(body) > MAX_REPORT_BYTES) body.spans.pop()
  return body
}
