import { describe, expect, it } from 'vitest'
import { buildSpeakerColorMap, resolveSpeakerColor, NEUTRAL_SPEAKER_COLOR } from './speakerColors'

// --- contrast helpers (independent reimplementation, WCAG 2.x) ---

function srgbToLinear(c: number): number {
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
}

function luminance(r: number, g: number, b: number): number {
  return 0.2126 * srgbToLinear(r) + 0.7152 * srgbToLinear(g) + 0.0722 * srgbToLinear(b)
}

function parseColor(color: string): [number, number, number] {
  const hex = color.match(/^#([0-9a-f]{6})$/i)
  if (hex) {
    const n = parseInt(hex[1], 16)
    return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255]
  }
  const hsl = color.match(/^hsl\(([\d.]+)\s+([\d.]+)%\s+([\d.]+)%\)$/)
  if (!hsl) throw new Error(`unparseable color: ${color}`)
  const h = parseFloat(hsl[1])
  const s = parseFloat(hsl[2]) / 100
  const l = parseFloat(hsl[3]) / 100
  const c = (1 - Math.abs(2 * l - 1)) * s
  const hp = (h % 360) / 60
  const x = c * (1 - Math.abs((hp % 2) - 1))
  let rgb: [number, number, number]
  if (hp < 1) rgb = [c, x, 0]
  else if (hp < 2) rgb = [x, c, 0]
  else if (hp < 3) rgb = [0, c, x]
  else if (hp < 4) rgb = [0, x, c]
  else if (hp < 5) rgb = [x, 0, c]
  else rgb = [c, 0, x]
  const m = l - c / 2
  return [rgb[0] + m, rgb[1] + m, rgb[2] + m]
}

function contrastOnWhite(color: string): number {
  const [r, g, b] = parseColor(color)
  return (1 + 0.05) / (luminance(r, g, b) + 0.05)
}

describe('buildSpeakerColorMap', () => {
  it('assigns colours by order of first appearance', () => {
    const map = buildSpeakerColorMap(['Alice', 'Bob', 'Alice', 'Carol'])
    expect([...map.keys()]).toEqual(['Alice', 'Bob', 'Carol'])
    // distinct colours for distinct speakers
    expect(new Set(map.values()).size).toBe(3)
  })

  it('skips null / empty / Unknown so they do not consume a palette slot', () => {
    const map = buildSpeakerColorMap([null, 'Unknown', '', 'Alice', 'unknown', 'Bob'])
    expect([...map.keys()]).toEqual(['Alice', 'Bob'])
  })

  it('resolves unknown speakers to the neutral colour', () => {
    const map = buildSpeakerColorMap(['Alice'])
    expect(resolveSpeakerColor('Unknown', map)).toBe(NEUTRAL_SPEAKER_COLOR)
    expect(resolveSpeakerColor(null, map)).toBe(NEUTRAL_SPEAKER_COLOR)
    expect(resolveSpeakerColor('Alice', map)).toBe(map.get('Alice'))
  })
})

describe('speaker colour contrast (WCAG AA on white)', () => {
  // 40 distinct speakers exercises the 8 curated palette colours plus the
  // golden-angle overflow generator (the part previously below AA).
  const speakers = Array.from({ length: 40 }, (_, i) => `speaker-${i}`)
  const colors = [...buildSpeakerColorMap(speakers).values()]

  it('every assigned colour clears 4.5:1 contrast on white', () => {
    for (let i = 0; i < colors.length; i++) {
      expect(contrastOnWhite(colors[i]), `index ${i} (${colors[i]})`).toBeGreaterThanOrEqual(4.5)
    }
  })

  it('the previously-failing overflow indices now pass (regression: #46-adjacent review)', () => {
    // index 11 ≈ hsl(72.6 …) and index 24 were ~4.05:1 / ~3.82:1 at the old
    // fixed L=33%. They must now meet AA.
    expect(contrastOnWhite(colors[11])).toBeGreaterThanOrEqual(4.5)
    expect(contrastOnWhite(colors[24])).toBeGreaterThanOrEqual(4.5)
  })
})
