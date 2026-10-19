import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiErrorCode, apiErrorMessage, redirectIfAccountDisabled } from './errors'

describe('apiErrorMessage', () => {
  it('reads a string detail', () => {
    expect(apiErrorMessage({ detail: 'Podcast not found' }, 404)).toBe('Podcast not found')
  })

  it('prefers the readable message of a spec #104 refusal over its code', () => {
    const body = { detail: { error: 'feature_paused', feature: 'new_feeds', message: 'Adding new podcasts is paused right now.' } }
    expect(apiErrorMessage(body, 503)).toBe('Adding new podcasts is paused right now.')
  })

  it('keeps an older route whose error is the readable text', () => {
    const body = { detail: { error: 'Refresh already in progress', message: 'Fetching podcast feed...' } }
    expect(apiErrorMessage(body, 409)).toBe('Refresh already in progress')
  })

  it('joins validation messages and falls back to the status', () => {
    expect(apiErrorMessage({ detail: [{ msg: 'field required' }, { msg: 'too long' }] }, 422)).toBe(
      'field required; too long',
    )
    expect(apiErrorMessage({}, 500)).toBe('API error: 500')
    expect(apiErrorMessage(null, 502)).toBe('API error: 502')
  })
})

describe('redirectIfAccountDisabled', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('sends a disabled session to /login once', () => {
    const assign = vi.fn()
    vi.stubGlobal('location', { pathname: '/inbox', assign })
    redirectIfAccountDisabled({ detail: { error: 'account_disabled' } })
    expect(assign).toHaveBeenCalledWith('/login?error=account_disabled')

    assign.mockReset()
    vi.stubGlobal('location', { pathname: '/login', assign })
    redirectIfAccountDisabled({ detail: { error: 'account_disabled' } })
    redirectIfAccountDisabled({ detail: { error: 'read_only' } })
    expect(assign).not.toHaveBeenCalled()
    expect(apiErrorCode({ detail: 'text' })).toBeNull()
  })
})
