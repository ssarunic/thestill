import { describe, expect, it } from 'vitest'
import { returnPath } from './helpers'

describe('returnPath (sign-in return)', () => {
  it('brings an episode opened from a quote back to the transcript at that line', () => {
    expect(returnPath('/podcasts/show/episodes/ep', '', '#t=460')).toBe(
      '/podcasts/show/episodes/ep?view=transcript&t=460',
    )
  })

  it('keeps the query of the episode it rewrites', () => {
    expect(returnPath('/podcasts/show/episodes/ep', '?lang=de', '#t=5')).toBe(
      '/podcasts/show/episodes/ep?lang=de&view=transcript&t=5',
    )
  })

  it('leaves every other page and fragment as it was', () => {
    expect(returnPath('/entities/person/ada', '', '#about=topic:ai')).toBe('/entities/person/ada#about=topic:ai')
    expect(returnPath('/podcasts/show', '', '#t=5')).toBe('/podcasts/show#t=5')
    expect(returnPath('/podcasts/show/episodes/ep', '?view=transcript', '')).toBe(
      '/podcasts/show/episodes/ep?view=transcript',
    )
  })
})
