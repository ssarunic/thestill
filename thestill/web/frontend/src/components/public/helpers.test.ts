import { describe, expect, it } from 'vitest'
import { splitSummary } from './helpers'

const SUMMARY = [
  '## 1. 🎙️ The Gist',
  'Two hosts argue about chips.',
  '## 2. ⏱️ Timeline',
  '- 00:00 Start',
  '## 3. 🧠 Key Takeaways',
  '- Chips are hard.',
  '## 4. 💩 The "BS" Test',
  'Some hype.',
].join('\n')

describe('splitSummary (spec #94)', () => {
  it('leads with the gist and key takeaways and keeps the rest for later', () => {
    const { lead, rest, restTitles } = splitSummary(SUMMARY)
    expect(lead).toBe('## 🎙️ The Gist\nTwo hosts argue about chips.\n## 🧠 Key Takeaways\n- Chips are hard.')
    expect(rest).toContain('## ⏱️ Timeline')
    expect(rest).toContain('## 💩 The "BS" Test')
    expect(restTitles).toEqual(['Timeline', 'The "BS" Test'])
  })

  it('falls back to the first section when the headings are different', () => {
    const { lead, rest } = splitSummary('## 1. Overview\nA.\n## 2. Details\nB.')
    expect(lead).toBe('## Overview\nA.')
    expect(rest).toBe('## Details\nB.')
  })
})
