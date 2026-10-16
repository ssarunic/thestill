// Spec #95 §3.6 — the last check before anything leaves the browser. Every
// event passes through here (posthog-js ``before_send``): unknown events are
// dropped, our own props are allowlisted per event, the SDK's own props are
// allowlisted too (anything new in an SDK upgrade is dropped, not sent), and
// every URL is reduced to a route pattern or a bare host.
import type { CaptureResult } from 'posthog-js'
import { routePattern } from './routes'

// Event → the props our code may attach.
export const EVENT_PROPS: Record<string, readonly string[]> = {
  $pageview: ['path_kind'],
  share: ['kind'],
  entity_open: ['entity_type'],
}

// SDK props kept as they are: device and browser class, ids, timing.
const SDK_PROPS = new Set([
  'token',
  'distinct_id',
  '$lib',
  '$lib_version',
  '$insert_id',
  '$time',
  '$session_id',
  '$window_id',
  '$device_id',
  '$pageview_id',
  '$is_identified',
  '$process_person_profile',
  '$os',
  '$os_version',
  '$browser',
  '$browser_version',
  '$device_type',
  '$device',
  '$screen_height',
  '$screen_width',
  '$viewport_height',
  '$viewport_width',
  '$timezone',
  '$timezone_offset',
  '$browser_language',
  '$browser_language_prefix',
  '$host',
])
// SDK props that carry a URL: rewritten, never passed through.
const URL_PROPS: Record<string, 'pattern' | 'url' | 'host'> = {
  $current_url: 'url',
  $pathname: 'pattern',
  $referrer: 'host',
  $referring_domain: 'host',
  $initial_current_url: 'url',
  $initial_pathname: 'pattern',
  $initial_referrer: 'host',
  $initial_referring_domain: 'host',
}
const MAX_STRING = 200
// Labels every event for a PostHog project shared with another site; set
// here, never taken from the page.
const APP = 'thestill'

function hostOf(value: unknown): string {
  if (typeof value !== 'string' || !value || value === '$direct') return '$direct'
  try {
    return new URL(value.includes('://') ? value : `https://${value}`).host || '$direct'
  } catch {
    return '$direct'
  }
}

function patternOf(value: unknown): string {
  if (typeof value !== 'string') return '/other'
  try {
    return routePattern(new URL(value, window.location.origin).pathname)
  } catch {
    return '/other'
  }
}

function cleanProps(props: Record<string, unknown> | undefined, allowed: readonly string[]) {
  const out: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(props ?? {})) {
    const urlKind = URL_PROPS[key]
    if (urlKind === 'url') out[key] = `${window.location.origin}${patternOf(value)}`
    else if (urlKind === 'pattern') out[key] = patternOf(value)
    else if (urlKind === 'host') out[key] = hostOf(value)
    else if (SDK_PROPS.has(key) || allowed.includes(key)) {
      out[key] = typeof value === 'string' && value.length > MAX_STRING ? value.slice(0, MAX_STRING) : value
    }
  }
  return out
}

/** ``before_send``: the event with only what spec #95 allows, or null. */
export function sanitizeEvent(event: CaptureResult | null): CaptureResult | null {
  if (!event) return null
  const allowed = EVENT_PROPS[event.event]
  if (!allowed) return null
  // Rebuilt field by field: person properties ($set, $set_once) never
  // leave the browser; the server sets ``is_admin`` with its own events.
  return {
    uuid: event.uuid,
    event: event.event,
    properties: { ...cleanProps(event.properties, allowed), app: APP },
    ...(event.timestamp ? { timestamp: event.timestamp } : {}),
  }
}
