// Spec #95 §3.4 — the browser side of usage events. posthog-js is imported
// lazily, only by the signed-in app and only when the server handed out a
// key, so public pages, /login and opted-out readers never load it.
//
// No cookie and no storage (``persistence: 'memory'``), no autocapture,
// replay, surveys, flags, heatmaps or extra script loads; events go to the
// same-origin /ingest proxy and pass through sanitizeEvent first.
//
// Stopping is an in-memory flag, never posthog-js opt-out: the SDK keeps
// opt-out consent in localStorage even with memory persistence, which would
// both store something in the browser and silently mute every later session.
import type { CaptureResult, PostHog } from 'posthog-js'
import { sanitizeEvent } from './sanitize'

let posthog: PostHog | null = null
let loading: Promise<PostHog | null> | null = null
let stopped = true // nothing is sent until a reader with a key starts it
const pending: [string, Record<string, unknown>][] = []

function beforeSend(event: CaptureResult | null): CaptureResult | null {
  return stopped ? null : sanitizeEvent(event)
}

/** Global Privacy Control: the SDK is never loaded. */
export function gpcEnabled(): boolean {
  return (navigator as Navigator & { globalPrivacyControl?: boolean }).globalPrivacyControl === true
}

export function startAnalytics(key: string, user: { id: string; is_admin: boolean }): void {
  if (gpcEnabled()) return
  stopped = false
  if (posthog || loading) return
  loading = import('posthog-js')
    .then(({ default: client }) => {
      client.init(key, {
        api_host: '/ingest',
        ui_host: 'https://eu.posthog.com',
        persistence: 'memory',
        // Start as the reader: no anonymous id is minted and nothing has to
        // be merged.
        bootstrap: { distinctID: user.id, isIdentifiedID: true },
        person_profiles: 'identified_only',
        autocapture: false,
        capture_pageview: false,
        capture_pageleave: false,
        capture_performance: false,
        capture_exceptions: false,
        capture_heatmaps: false,
        capture_dead_clicks: false,
        rageclick: false,
        disable_session_recording: true,
        disable_surveys: true,
        disable_web_experiments: true,
        advanced_disable_flags: true,
        disable_external_dependency_loading: true,
        mask_all_text: true,
        mask_all_element_attributes: true,
        before_send: beforeSend,
      })
      posthog = client
      // The one person property: lets PostHog's internal-users filter hide
      // the operator. Sent once per load; nothing else about the person.
      if (!stopped) client.setPersonProperties({ is_admin: user.is_admin })
      for (const [event, props] of pending.splice(0)) client.capture(event, props)
      return client
    })
    .catch(() => {
      // Analytics is best effort: a failed chunk load must not surface.
      loading = null
      pending.length = 0
      return null
    })
}

/** One allowlisted event; queued while the SDK is still loading. */
export function track(event: string, props: Record<string, unknown> = {}): void {
  if (stopped) return
  if (posthog) posthog.capture(event, props)
  else if (loading) pending.push([event, props])
}

/** Opt-out, GPC or no key: stop sending. In memory only, nothing stored. */
export function stopAnalytics(): void {
  stopped = true
  pending.length = 0
}

/** Sign-out: stop, and forget the identity before the next reader. */
export function resetAnalytics(): void {
  stopAnalytics()
  posthog?.reset()
}
