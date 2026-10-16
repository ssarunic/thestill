import { afterEach, describe, expect, it } from 'vitest'
import { consumePendingFollow, notNowPath, parseIntent, rememberPendingFollow, showSlugFromNext, signInPath } from './signIn'

describe('sign-in links (spec #102)', () => {
  it('builds /login with a validated next and an intent', () => {
    expect(signInPath('/podcasts/prof-g', 'follow')).toBe('/login?next=%2Fpodcasts%2Fprof-g&intent=follow')
    expect(signInPath('/entities/person/x?about=company:y')).toBe('/login?next=%2Fentities%2Fperson%2Fx%3Fabout%3Dcompany%3Ay')
    expect(signInPath('//evil.example')).toBe('/login')
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
