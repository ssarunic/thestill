import { beforeEach, describe, expect, it } from 'vitest'
import {
  beginAudioLoad,
  chainKey,
  currentAudioLoad,
  getCorsVerdict,
  rejectCrossOrigin,
  setCorsVerdict,
  setCrossOriginLoadingEnabled,
} from './corsVerdict'

const MEGAPHONE = 'https://www.podtrac.com/pts/redirect.mp3/pdst.fm/e/traffic.megaphone.fm/VMP123.mp3?x=1'
const SAME_CHAIN = 'https://www.podtrac.com/pts/redirect.mp3/pdst.fm/e/traffic.megaphone.fm/VMP999.mp3'
const SIMPLECAST = 'https://dts.podtrac.com/redirect.mp3/stitcher.simplecastaudio.com/abc/episodes/def/audio/128/default.mp3'
const DAY = 24 * 60 * 60 * 1000

beforeEach(() => {
  localStorage.clear()
  setCrossOriginLoadingEnabled(true)
})

describe('chainKey', () => {
  it('names the hosts a prefixed enclosure URL shows, not the episode', () => {
    expect(chainKey(MEGAPHONE)).toBe('https://www.podtrac.com|pdst.fm|traffic.megaphone.fm')
    expect(chainKey(SAME_CHAIN)).toBe(chainKey(MEGAPHONE))
    expect(chainKey(SIMPLECAST)).toBe('https://dts.podtrac.com|stitcher.simplecastaudio.com')
    expect(chainKey('https://traffic.megaphone.fm/STM1.mp3')).toBe('https://traffic.megaphone.fm')
  })

  it('rejects what is not a web URL', () => {
    expect(chainKey('blob:https://thestill.me/1')).toBeNull()
    expect(chainKey('not a url')).toBeNull()
  })
})

describe('verdict store', () => {
  it('is shared by episodes behind the same chain and kept for a week', () => {
    const now = 1_800_000_000_000
    setCorsVerdict(MEGAPHONE, 'cors_ok', now)
    expect(getCorsVerdict(SAME_CHAIN, now + 6 * DAY)).toBe('cors_ok')
    expect(getCorsVerdict(SAME_CHAIN, now + 8 * DAY)).toBeNull()
    expect(getCorsVerdict(SIMPLECAST, now)).toBeNull()
  })

  it('ignores a corrupt or future-dated entry', () => {
    localStorage.setItem('thestill:drift:cors:https://traffic.megaphone.fm', '{"v":"yes"}')
    expect(getCorsVerdict('https://traffic.megaphone.fm/a.mp3')).toBeNull()
    setCorsVerdict(MEGAPHONE, 'cors_ok', Date.now() + 30 * DAY)
    expect(getCorsVerdict(MEGAPHONE)).toBeNull()
  })
})

describe('beginAudioLoad', () => {
  it('asks for crossorigin only for a chain known to allow it', () => {
    expect(beginAudioLoad(MEGAPHONE, true)).toBe(false) // never seen
    setCorsVerdict(MEGAPHONE, 'cors_ok')
    expect(beginAudioLoad(SAME_CHAIN, true)).toBe(true)
    expect(currentAudioLoad()).toMatchObject({ url: SAME_CHAIN, crossOrigin: true })
    setCorsVerdict(SIMPLECAST, 'no_cors')
    expect(beginAudioLoad(SIMPLECAST, true)).toBe(false)
  })

  it('never does for a video or HLS source, or without the permission', () => {
    setCorsVerdict(MEGAPHONE, 'cors_ok')
    expect(beginAudioLoad(MEGAPHONE, false)).toBe(false)
    setCrossOriginLoadingEnabled(false)
    expect(beginAudioLoad(MEGAPHONE, true)).toBe(false)
  })

  it('a rejected load flips the verdict and the record of the load', () => {
    setCorsVerdict(MEGAPHONE, 'cors_ok')
    beginAudioLoad(MEGAPHONE, true)
    rejectCrossOrigin(MEGAPHONE)
    expect(getCorsVerdict(SAME_CHAIN)).toBe('no_cors')
    expect(currentAudioLoad()?.crossOrigin).toBe(false)
    expect(beginAudioLoad(SAME_CHAIN, true)).toBe(false)
  })
})
