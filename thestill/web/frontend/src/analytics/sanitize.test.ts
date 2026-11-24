import { describe, expect, it } from 'vitest'
import type { CaptureResult } from 'posthog-js'
import { sanitizeEvent } from './sanitize'

const origin = window.location.origin

function event(name: string, properties: Record<string, unknown>, extra: Partial<CaptureResult> = {}): CaptureResult {
  return { uuid: 'u', event: name, properties, ...extra } as CaptureResult
}

describe('sanitizeEvent (spec #95 §3.6)', () => {
  it('drops events outside the allowlist', () => {
    expect(sanitizeEvent(event('$autocapture', {}))).toBeNull()
    expect(sanitizeEvent(event('$pageleave', {}))).toBeNull()
    expect(sanitizeEvent(event('$opt_in', {}))).toBeNull()
    expect(sanitizeEvent(null)).toBeNull()
  })

  it('passes the reader events with only their allowlisted props', () => {
    const tab = sanitizeEvent(event('reader_tab', { tab: 'transcript', episode: 'secret' }))!
    expect(tab.properties).toEqual({ tab: 'transcript', app: 'thestill' })
    const depth = sanitizeEvent(event('read_depth', { tab: 'summary', percent: 75, title: 'secret' }))!
    expect(depth.properties).toEqual({ tab: 'summary', percent: 75, app: 'thestill' })
  })

  it('reduces every URL to a route pattern or a host', () => {
    const out = sanitizeEvent(
      event('$pageview', {
        path_kind: '/search',
        $current_url: `${origin}/search?q=secret+words#top`,
        $pathname: '/search',
        $referrer: 'https://news.ycombinator.com/item?id=123',
        $initial_referrer: '$direct',
        $referring_domain: 'news.ycombinator.com',
        $host: window.location.host,
        $browser: 'Chrome',
        token: 'phc_x',
        distinct_id: 'u-1',
      }),
    )!
    expect(out.properties.$current_url).toBe(`${origin}/search`)
    expect(out.properties.$pathname).toBe('/search')
    expect(out.properties.$referrer).toBe('news.ycombinator.com')
    expect(out.properties.$initial_referrer).toBe('$direct')
    expect(out.properties.$referring_domain).toBe('news.ycombinator.com')
    expect(out.properties.$browser).toBe('Chrome')
    expect(out.properties.token).toBe('phc_x')
    expect(JSON.stringify(out)).not.toContain('secret')
  })

  it('keeps only allowlisted props of our own and drops page text', () => {
    const out = sanitizeEvent(
      event('share', { kind: 'episode', url: `${origin}/podcasts/x?ref=y`, title: 'Private briefing', $raw_user_agent: 'UA' }),
    )!
    expect(out.properties).toEqual({ kind: 'episode', app: 'thestill' })
  })

  it('drops SDK props that are not allowlisted, including session-entry URLs', () => {
    const out = sanitizeEvent(
      event('$pageview', {
        path_kind: '/search',
        $session_entry_url: `${origin}/search?q=secret`,
        $session_entry_referrer: 'https://news.ycombinator.com/item?id=9',
        $session_entry_pathname: '/briefings/abc',
        $prev_pageview_pathname: '/briefings/abc',
        $session_entry_utm_source: 'newsletter',
        $some_future_sdk_prop: 'x',
        $os: 'Mac OS X',
        $session_id: 's-1',
      }),
    )!
    expect(out.properties).toEqual({ path_kind: '/search', $os: 'Mac OS X', $session_id: 's-1', app: 'thestill' })
  })

  it('lets only is_admin through, and only on the $set event', () => {
    const out = sanitizeEvent(
      event('$set', { $set: { is_admin: true, email: 'a@example.com' } }, { $set: { is_admin: true, name: 'Ada' } }),
    )!
    expect(out.$set).toEqual({ is_admin: true })
    expect(out.properties.$set).toEqual({ is_admin: true })
    expect(JSON.stringify(out)).not.toMatch(/a@example.com|Ada/)
    expect(sanitizeEvent(event('$set', { $set: { is_admin: 'yes' } }))!.$set).toBeUndefined()
  })

  it('never sends other person properties or $identify', () => {
    expect(sanitizeEvent(event('$identify', { $set: { is_admin: true } }))).toBeNull()
    const out = sanitizeEvent(
      event('share', { kind: 'episode', $set: { email: 'a@example.com' } }, {
        $set: { email: 'a@example.com' },
        $set_once: { $initial_current_url: `${origin}/briefings/abc?x=1` },
      }),
    )!
    expect(out.$set).toBeUndefined()
    expect(out.$set_once).toBeUndefined()
    expect(JSON.stringify(out)).not.toContain('a@example.com')
  })

  it('caps long strings', () => {
    const out = sanitizeEvent(event('entity_open', { entity_type: 'x'.repeat(500) }))!
    expect(String(out.properties.entity_type)).toHaveLength(200)
  })

  it('labels every event as thestill, whatever the page tried to set', () => {
    const out = sanitizeEvent(event('entity_open', { entity_type: 'person', app: 'sarunic.com' }))!
    expect(out.properties.app).toBe('thestill')
  })
})
