// Spec #25 item 3.2 — regression tests for sanitizeUntrustedHtml.
// Each payload below is a real-world XSS vector that has bypassed
// permissive sanitisers in the past. The assertion shape is
// deliberately tight: not just "the dangerous string disappeared", but
// also "the safe parts of the input survived" so we'd notice if a
// future change accidentally over-sanitises.

import { describe, it, expect } from 'vitest'
import { sanitizeUntrustedHtml } from './sanitize'

describe('sanitizeUntrustedHtml — XSS vectors', () => {
  it('strips <script> tags entirely', () => {
    const out = sanitizeUntrustedHtml('hello<script>alert(1)</script>world')
    expect(out).not.toContain('<script>')
    expect(out).not.toContain('alert(1)')
    expect(out).toContain('hello')
    expect(out).toContain('world')
  })

  it('strips on* event handler attributes', () => {
    // The spec calls out this exact payload.
    const out = sanitizeUntrustedHtml('<img src=x onerror=alert(1)>')
    expect(out).not.toContain('onerror')
    expect(out).not.toContain('alert')
  })

  it('strips iframe', () => {
    const out = sanitizeUntrustedHtml('<iframe src="https://evil.com"></iframe>')
    expect(out).not.toContain('<iframe')
    expect(out).not.toContain('evil.com')
  })

  it('strips javascript: hrefs from anchor tags', () => {
    const out = sanitizeUntrustedHtml('<a href="javascript:alert(1)">click</a>')
    expect(out).not.toContain('javascript:')
    expect(out).not.toContain('alert')
  })

  it('strips data: URIs from anchor tags', () => {
    const out = sanitizeUntrustedHtml(
      '<a href="data:text/html,<script>alert(1)</script>">click</a>',
    )
    expect(out).not.toContain('data:text/html')
    expect(out).not.toContain('<script>')
  })

  it('strips style attribute', () => {
    const out = sanitizeUntrustedHtml(
      '<p style="background:url(javascript:alert(1))">x</p>',
    )
    expect(out).not.toContain('style=')
    expect(out).not.toContain('javascript:')
  })

  it('strips svg/foreignObject vector', () => {
    const out = sanitizeUntrustedHtml(
      '<svg><foreignObject><script>alert(1)</script></foreignObject></svg>',
    )
    expect(out).not.toContain('<svg')
    expect(out).not.toContain('<script>')
  })
})

describe('sanitizeUntrustedHtml — allowlist preserves safe content', () => {
  it('keeps paragraphs and emphasis', () => {
    const out = sanitizeUntrustedHtml(
      '<p>This is <strong>important</strong> and <em>emphasised</em>.</p>',
    )
    expect(out).toContain('<p>')
    expect(out).toContain('<strong>')
    expect(out).toContain('<em>')
    expect(out).toContain('important')
  })

  it('keeps line breaks', () => {
    const out = sanitizeUntrustedHtml('line1<br>line2<br/>line3')
    expect(out.match(/<br\s*\/?>/g)?.length).toBe(2)
  })

  it('keeps lists', () => {
    const out = sanitizeUntrustedHtml('<ul><li>one</li><li>two</li></ul>')
    expect(out).toContain('<ul>')
    expect(out).toContain('<li>one</li>')
  })

  it('keeps anchor tags with safe http href', () => {
    const out = sanitizeUntrustedHtml('<a href="https://example.com">link</a>')
    expect(out).toContain('href="https://example.com"')
    expect(out).toContain('link')
  })
})

describe('sanitizeUntrustedHtml — anchor hardening', () => {
  it('forces target=_blank on every anchor', () => {
    const out = sanitizeUntrustedHtml('<a href="https://example.com">x</a>')
    expect(out).toContain('target="_blank"')
  })

  it('forces rel="noopener noreferrer" on every anchor', () => {
    const out = sanitizeUntrustedHtml('<a href="https://example.com">x</a>')
    expect(out).toContain('rel="noopener noreferrer"')
  })

  it('overrides attacker-supplied target / rel', () => {
    // An attacker setting ``target="_top"`` to nudge the parent should
    // be overridden by the sanitiser.
    const out = sanitizeUntrustedHtml(
      '<a href="https://example.com" target="_top" rel="opener">x</a>',
    )
    expect(out).toContain('target="_blank"')
    expect(out).toContain('rel="noopener noreferrer"')
    expect(out).not.toContain('target="_top"')
    expect(out).not.toContain('rel="opener"')
  })
})
