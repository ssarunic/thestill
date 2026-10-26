import { afterEach, describe, expect, it } from 'vitest'
import {
  consumePendingFollow,
  notNowPath,
  parseIntent,
  parseSignInState,
  referrerPath,
  rememberPendingFollow,
  showSlugFromNext,
  signInLink,
  signInPath,
} from './signIn'

describe('sign-in links (spec #102)', () => {
  it('builds /login with a validated next and an intent', () => {
    expect(signInPath('/podcasts/prof-g', 'follow')).toBe('/login?next=%2Fpodcasts%2Fprof-g&intent=follow')
    expect(signInPath('/entities/person/x?about=company:y')).toBe('/login?next=%2Fentities%2Fperson%2Fx%3Fabout%3Dcompany%3Ay')
    expect(signInPath('//evil.example')).toBe('/login')
  })

  it('links every page to the one /login URL, with next and intent in state', () => {
    expect(signInLink('/podcasts/prof-g', 'follow')).toEqual({ to: '/login', state: { next: '/podcasts/prof-g', intent: 'follow' } })
    expect(signInLink('/entities/person/x#about=company:y')).toEqual({
      to: '/login',
      state: { next: '/entities/person/x#about=company:y' },
    })
    expect(signInLink('//evil.example')).toEqual({ to: '/login', state: {} })
  })

  it('reads only a valid next and intent back out of router state', () => {
    expect(parseSignInState({ next: '/podcasts/prof-g', intent: 'follow' })).toEqual({ next: '/podcasts/prof-g', intent: 'follow' })
    expect(parseSignInState({ next: '//evil.example', intent: 'delete-account' })).toEqual({})
    expect(parseSignInState(null)).toEqual({})
    expect(parseSignInState('/podcasts/prof-g')).toEqual({})
  })

  it('falls back to a same-site referrer for a pre-hydration click', () => {
    expect(referrerPath('https://thestill.me/entities/person/x?about=y#t=1', 'https://thestill.me')).toBe(
      '/entities/person/x?about=y#t=1',
    )
    expect(referrerPath('https://www.google.com/', 'https://thestill.me')).toBeNull()
    expect(referrerPath('', 'https://thestill.me')).toBeNull()
    expect(referrerPath('not a url', 'https://thestill.me')).toBeNull()
  })

  it('accepts only known intents', () => {
    expect(parseIntent('follow')).toBe('follow')
    expect(parseIntent('summary')).toBe('summary')
    expect(parseIntent('delete-account')).toBeNull()
    expect(parseIntent(null)).toBeNull()
  })

  it('recognises a show page as next', () => {
    expect(showSlugFromNext('/podcasts/prof-g')).toBe('prof-g')
    expect(showSlugFromNext('/podcasts/prof-g?x=1')).toBe('prof-g')
    expect(showSlugFromNext('/podcasts/prof-g/episodes/a')).toBeNull()
    expect(showSlugFromNext(null)).toBeNull()
  })

  it('sends Not now back to public pages only', () => {
    expect(notNowPath('/podcasts/prof-g/episodes/a?t=12')).toBe('/podcasts/prof-g/episodes/a?t=12')
    expect(notNowPath('/entities/company/nvidia')).toBe('/entities/company/nvidia')
    expect(notNowPath('/inbox')).toBe('/')
    expect(notNowPath(null)).toBe('/')
  })
})

describe('pending follow (spec #102)', () => {
  afterEach(() => window.sessionStorage.clear())

  it('is consumed once, for the same show, while fresh', () => {
    rememberPendingFollow('prof-g', 1_000)
    expect(consumePendingFollow('prof-g', 2_000)).toBe(true)
    expect(consumePendingFollow('prof-g', 2_000)).toBe(false)
  })

  it('is cleared by a different show without following it', () => {
    rememberPendingFollow('prof-g', 1_000)
    expect(consumePendingFollow('all-in', 2_000)).toBe(false)
    expect(consumePendingFollow('prof-g', 2_000)).toBe(false)
  })

  it('expires after 15 minutes', () => {
    rememberPendingFollow('prof-g', 0)
    expect(consumePendingFollow('prof-g', 15 * 60_000 + 1)).toBe(false)
  })
})
