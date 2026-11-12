import { describe, expect, it } from 'vitest'
import { elementAudioUrl, isSafariFamily } from './elementAudioUrl'

const owner = { podcastSlug: 'the-martin-lewis-podcast', episodeSlug: 'best-of-question-time', audioUrl: 'http://open.example.org/a.mp3' }

const SAFARI = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/27.2 Safari/605.1.15'
const IPHONE_SAFARI = 'Mozilla/5.0 (iPhone; CPU iPhone OS 19_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/19.0 Mobile/15E148 Safari/604.1'
const IPHONE_CHROME = 'Mozilla/5.0 (iPhone; CPU iPhone OS 19_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/130.0.0.0 Mobile/15E148 Safari/604.1'
const CHROME = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'
const EDGE = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0'
const ANDROID_CHROME = 'Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36'
const FIREFOX = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:132.0) Gecko/20100101 Firefox/132.0'

const VIA = '/api/podcasts/the-martin-lewis-podcast/episodes/best-of-question-time/audio'

describe('isSafariFamily', () => {
  it('is Safari and every browser on an iPhone, and nothing else', () => {
    expect(isSafariFamily(SAFARI)).toBe(true)
    expect(isSafariFamily(IPHONE_SAFARI)).toBe(true)
    expect(isSafariFamily(IPHONE_CHROME)).toBe(true) // WebKit underneath
    expect(isSafariFamily(CHROME)).toBe(false)
    expect(isSafariFamily(EDGE)).toBe(false)
    expect(isSafariFamily(ANDROID_CHROME)).toBe(false)
    expect(isSafariFamily(FIREFOX)).toBe(false)
    expect(isSafariFamily('')).toBe(false)
  })
})

describe('elementAudioUrl', () => {
  it('plays an http enclosure through our https redirect in Safari on an https page', () => {
    expect(elementAudioUrl(owner.audioUrl, owner, 'https:', SAFARI)).toBe(VIA)
    expect(elementAudioUrl(owner.audioUrl, owner, 'https:', IPHONE_CHROME)).toBe(VIA)
  })

  it('leaves Chrome and Firefox to fetch it from the publisher as before', () => {
    expect(elementAudioUrl(owner.audioUrl, owner, 'https:', CHROME)).toBe(owner.audioUrl)
    expect(elementAudioUrl(owner.audioUrl, owner, 'https:', FIREFOX)).toBe(owner.audioUrl)
  })

  it('leaves an https enclosure alone', () => {
    const https = { ...owner, audioUrl: 'https://cdn.example.net/a.mp3' }
    expect(elementAudioUrl(https.audioUrl, https, 'https:', SAFARI)).toBe('https://cdn.example.net/a.mp3')
  })

  it('leaves everything alone on an http page (local development)', () => {
    expect(elementAudioUrl(owner.audioUrl, owner, 'http:', SAFARI)).toBe(owner.audioUrl)
  })

  it('only stands in for the episode’s own enclosure, and needs both slugs', () => {
    // Another http asset of the episode: the endpoint knows only the enclosure.
    expect(elementAudioUrl('http://cdn.example.net/other.mp3', owner, 'https:', SAFARI)).toBe('http://cdn.example.net/other.mp3')
    expect(elementAudioUrl(owner.audioUrl, null, 'https:', SAFARI)).toBe(owner.audioUrl)
    expect(elementAudioUrl(owner.audioUrl, { ...owner, episodeSlug: '' }, 'https:', SAFARI)).toBe(owner.audioUrl)
  })

  it('escapes the slugs', () => {
    const odd = { ...owner, podcastSlug: 'a/b', episodeSlug: 'c d' }
    expect(elementAudioUrl(odd.audioUrl, odd, 'https:', SAFARI)).toBe('/api/podcasts/a%2Fb/episodes/c%20d/audio')
  })
})
