import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const fake = vi.hoisted(() => ({
  init: vi.fn(),
  identify: vi.fn(),
  capture: vi.fn(),
  opt_in_capturing: vi.fn(),
  opt_out_capturing: vi.fn(),
  setPersonProperties: vi.fn(),
  reset: vi.fn(),
}))
vi.mock('posthog-js', () => ({ default: fake }))

async function freshClient() {
  vi.resetModules()
  return import('./client')
}

function setGpc(value: boolean | undefined) {
  Object.defineProperty(navigator, 'globalPrivacyControl', { value, configurable: true })
}

describe('analytics client (spec #95 §3.4)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setGpc(undefined)
  })
  afterEach(() => setGpc(undefined))

  it('initialises memory-only, same-origin, with nothing captured automatically', async () => {
    const client = await freshClient()
    client.startAnalytics('phc_test', { id: 'u-1', is_admin: false })
    await vi.waitFor(() => expect(fake.init).toHaveBeenCalled())
    const [key, config] = fake.init.mock.calls[0]
    expect(key).toBe('phc_test')
    expect(config).toMatchObject({
      api_host: '/ingest',
      persistence: 'memory',
      autocapture: false,
      capture_pageview: false,
      disable_session_recording: true,
      disable_surveys: true,
      advanced_disable_flags: true,
      disable_external_dependency_loading: true,
      person_profiles: 'identified_only',
      bootstrap: { distinctID: 'u-1', isIdentifiedID: true },
    })
    expect(typeof config.before_send).toBe('function')
    expect(fake.identify).not.toHaveBeenCalled()
    expect(fake.setPersonProperties).toHaveBeenCalledWith({ is_admin: false })
  })

  it('queues events until the SDK has loaded, then sends them in order', async () => {
    const client = await freshClient()
    client.startAnalytics('phc_test', { id: 'u-1', is_admin: false })
    client.track('$pageview', { path_kind: '/inbox' })
    await vi.waitFor(() => expect(fake.capture).toHaveBeenCalledWith('$pageview', { path_kind: '/inbox' }))
    client.track('share', { kind: 'episode' })
    expect(fake.capture).toHaveBeenLastCalledWith('share', { kind: 'episode' })
  })

  it('never loads under Global Privacy Control, and drops events', async () => {
    setGpc(true)
    const client = await freshClient()
    client.startAnalytics('phc_test', { id: 'u-1', is_admin: false })
    client.track('$pageview', { path_kind: '/inbox' })
    await new Promise((r) => setTimeout(r, 0))
    expect(fake.init).not.toHaveBeenCalled()
    expect(fake.capture).not.toHaveBeenCalled()
  })

  it('stops in memory only: no SDK opt-out (it would write localStorage), no second init', async () => {
    const client = await freshClient()
    client.startAnalytics('phc_test', { id: 'u-1', is_admin: false })
    await vi.waitFor(() => expect(fake.init).toHaveBeenCalledTimes(1))
    const beforeSend = fake.init.mock.calls[0][1].before_send

    client.stopAnalytics()
    client.track('share', { kind: 'episode' })
    expect(fake.capture).not.toHaveBeenCalled()
    expect(beforeSend({ uuid: 'x', event: 'share', properties: { kind: 'episode' } })).toBeNull()
    expect(fake.opt_out_capturing).not.toHaveBeenCalled()

    client.startAnalytics('phc_test', { id: 'u-1', is_admin: false })
    client.track('share', { kind: 'episode' })
    expect(fake.capture).toHaveBeenCalledWith('share', { kind: 'episode' })
    expect(beforeSend({ uuid: 'x', event: 'share', properties: { kind: 'episode' } })).not.toBeNull()
    expect(fake.init).toHaveBeenCalledTimes(1)

    client.resetAnalytics()
    expect(fake.reset).toHaveBeenCalled()
    client.track('share', { kind: 'episode' })
    expect(fake.capture).toHaveBeenCalledTimes(1)
  })

  it('opting out while the SDK loads sends nothing queued', async () => {
    const client = await freshClient()
    client.startAnalytics('phc_test', { id: 'u-1', is_admin: false })
    client.track('$pageview', { path_kind: '/inbox' })
    client.stopAnalytics()
    await vi.waitFor(() => expect(fake.init).toHaveBeenCalled())
    expect(fake.capture).not.toHaveBeenCalled()
  })

  it('does nothing before start', async () => {
    const client = await freshClient()
    client.track('share', { kind: 'episode' })
    client.stopAnalytics()
    expect(fake.capture).not.toHaveBeenCalled()
  })
})
