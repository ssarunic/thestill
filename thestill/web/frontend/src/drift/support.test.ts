import { describe, expect, it } from 'vitest'
import { driftAlignmentSupported } from './support'

const UA = {
  chrome:
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36',
  edge: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36 Edg/157.0.0.0',
  safari:
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/27.2 Safari/605.1.15',
  iphone:
    'Mozilla/5.0 (iPhone; CPU iPhone OS 27_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/27.1 Mobile/15E148 Safari/604.1',
  chromeIos:
    'Mozilla/5.0 (iPhone; CPU iPhone OS 27_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/157.0.1 Mobile/15E148 Safari/604.1',
  chromeAndroid:
    'Mozilla/5.0 (Linux; Android 16; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36',
  ipad: 'Mozilla/5.0 (iPad; CPU OS 27_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/27.1 Mobile/15E148 Safari/604.1',
  firefox: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:150.0) Gecko/20100101 Firefox/150.0',
}

describe('driftAlignmentSupported', () => {
  it('allows the browsers the feature has been proven in: desktop Chrome, Edge and Safari', () => {
    expect(driftAlignmentSupported(UA.chrome)).toBe(true)
    expect(driftAlignmentSupported(UA.edge)).toBe(true)
    expect(driftAlignmentSupported(UA.safari)).toBe(true)
  })

  it('does not take an iPad asking for the desktop site for a Mac', () => {
    expect(driftAlignmentSupported(UA.safari, 5)).toBe(false)
    expect(driftAlignmentSupported(UA.safari, 0)).toBe(true)
  })

  it.each(Object.entries(UA).filter(([name]) => !['chrome', 'edge', 'safari'].includes(name)))(
    'gives %s none of it',
    (_name, userAgent) => {
      expect(driftAlignmentSupported(userAgent)).toBe(false)
    },
  )

  it('is off for an unknown or empty user agent', () => {
    expect(driftAlignmentSupported('')).toBe(false)
    expect(driftAlignmentSupported('curl/8.4.0')).toBe(false)
  })
})
