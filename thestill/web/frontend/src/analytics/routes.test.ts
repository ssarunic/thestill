import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { APP_ROUTE_PATTERNS, routePattern } from './routes'

describe('route patterns (spec #95)', () => {
  it('maps real paths to their pattern and nothing else', () => {
    expect(routePattern('/podcasts/hard-fork/episodes/ep-1')).toBe('/podcasts/:podcastSlug/episodes/:episodeSlug')
    expect(routePattern('/briefings/7f3e')).toBe('/briefings/:briefingId')
    expect(routePattern('/entities/person/scott-galloway')).toBe('/entities/:entityType/:idSlug')
    expect(routePattern('/search')).toBe('/search')
    expect(routePattern('/somewhere/else')).toBe('/other')
  })

  it('covers every route App.tsx declares', () => {
    const app = readFileSync(resolve(__dirname, '../App.tsx'), 'utf8')
    const declared = [...app.matchAll(/path="([^"*]+)"/g)].map(([, p]) => (p.startsWith('/') ? p : `/${p}`))
    expect(declared.length).toBeGreaterThan(10)
    for (const path of declared) {
      expect(APP_ROUTE_PATTERNS as readonly string[]).toContain(path)
    }
  })
})
