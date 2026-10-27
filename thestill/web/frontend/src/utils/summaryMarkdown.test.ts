import { describe, expect, it } from 'vitest'
import { escapeListItemLeadingNumbers } from './summaryMarkdown'

describe('escapeListItemLeadingNumbers', () => {
  it('escapes an ordinal date at the start of a bullet', () => {
    expect(escapeListItemLeadingNumbers('- 17. kolovoza 1990.: Početak [15:00].')).toBe(
      '- 17\\. kolovoza 1990.: Početak [15:00].',
    )
    expect(escapeListItemLeadingNumbers('  * 4) kolovoza')).toBe('  * 4\\) kolovoza')
  })

  it('escapes the same shape inside an ordered list item', () => {
    expect(escapeListItemLeadingNumbers('1. 22. siječnja 1993.')).toBe('1. 22\\. siječnja 1993.')
  })

  it('leaves real lists, plain text and years without a delimiter alone', () => {
    const text = [
      '1. First point',
      '17. kolovoza starts a paragraph',
      '- Ljeto 1989.: Veliki skup',
      '- 1990 was the year',
      '- 3.5 million listeners',
    ].join('\n')
    expect(escapeListItemLeadingNumbers(text)).toBe(text)
  })

  it('does not touch fenced code', () => {
    const text = ['```', '- 17. kolovoza', '```', '- 17. kolovoza'].join('\n')
    expect(escapeListItemLeadingNumbers(text)).toBe(['```', '- 17. kolovoza', '```', '- 17\\. kolovoza'].join('\n'))
  })
})
