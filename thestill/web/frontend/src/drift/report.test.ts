import { describe, expect, it } from 'vitest'
import { browserLabel, MAX_REPORTED_SPANS, toOutcomeBody } from './report'
import type { DriftReport } from './run'

const UA = {
  safari:
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/27.2 Safari/605.1.15',
  iphone:
    'Mozilla/5.0 (iPhone; CPU iPhone OS 27_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/27.1 Mobile/15E148 Safari/604.1',
  chrome:
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36',
  chromeIos:
    'Mozilla/5.0 (iPhone; CPU iPhone OS 27_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/157.0.1 Mobile/15E148 Safari/604.1',
  edge: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36 Edg/157.0.0.0',
  firefox: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:150.0) Gecko/20100101 Firefox/150.0',
}

// The server's pattern for `browser` (DriftOutcomeRequest).
const SERVER_BROWSER = /^[A-Za-z][A-Za-z ]{0,30} \d{1,4}$/

const report: DriftReport = {
  outcome: 'aligned',
  generation: 'abc123',
  spans: [
    { srcStartS: 0, srcEndS: 2623.0004, offsetS: -0.0000102 },
    { srcStartS: 2688.7, srcEndS: 3282, offsetS: 40.1239999 },
  ],
  gaps: [],
  gapsConfirmedS: 60.4,
  unresolvedS: 13.30001,
  listenerAdsS: 105.2,
  coverage: 0.995912,
  anchorsTotal: 3372,
  anchorsHit: 3301,
  anchorsInlier: 3299,
  audioHost: 'stitcher2.acast.com',
  audioBytes: 54_000_000,
  fetchedBytes: 54_720_000,
  fetchMs: 2100,
  fitMs: 35,
  error: null,
}

describe('browserLabel', () => {
  it.each([
    [UA.safari, 'Safari 27'],
    [UA.iphone, 'Safari 27'],
    [UA.chrome, 'Chrome 157'],
    [UA.chromeIos, 'Chrome 157'],
    [UA.edge, 'Edge 157'],
    [UA.firefox, 'Firefox 150'],
  ])('names the family and major version', (userAgent, label) => {
    expect(browserLabel(userAgent)).toBe(label)
    expect(label).toMatch(SERVER_BROWSER)
  })

  it('gives up on an unknown agent', () => {
    expect(browserLabel('curl/8.4.0')).toBeNull()
  })
})

describe('toOutcomeBody', () => {
  it('maps the report to the wire names, rounded', () => {
    const body = toOutcomeBody(report, 'abc123', UA.safari)
    expect(body.spans).toEqual([
      { src_start: 0, src_end: 2623, offset: -0 },
      { src_start: 2688.7, src_end: 3282, offset: 40.124 },
    ])
    expect(body).toMatchObject({
      generation: 'abc123',
      outcome: 'aligned',
      unresolved_s: 13.3,
      coverage: 0.9959,
      anchors_outlier: 2,
      audio_host: 'stitcher2.acast.com',
      browser: 'Safari 27',
    })
  })

  it('never claims the checks that are not built yet', () => {
    const body = toOutcomeBody(report, 'abc123', UA.safari)
    expect(body.verify_length_ok).toBeNull()
    expect(body.verify_ranges_checked).toBeNull()
    expect(body.verify_ranges_matched).toBeNull()
  })

  it('drops a host that is not a bare host name, and caps the spans', () => {
    const many = Array.from({ length: 100 }, (_, i) => ({ srcStartS: i, srcEndS: i + 0.5, offsetS: 1 }))
    const body = toOutcomeBody({ ...report, audioHost: '[::1]:8443', spans: many }, 'abc123', UA.chrome)
    expect(body.audio_host).toBeNull()
    expect(body.spans).toHaveLength(MAX_REPORTED_SPANS)
  })

  it('falls back to the transcript’s generation when the track was never read', () => {
    const body = toOutcomeBody({ ...report, generation: null, outcome: 'error' }, 'from-transcript', UA.firefox)
    expect(body.generation).toBe('from-transcript')
    expect(JSON.stringify(body).length).toBeLessThan(4096)
  })
})
