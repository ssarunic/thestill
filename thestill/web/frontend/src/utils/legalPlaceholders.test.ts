import { describe, expect, it } from 'vitest'
import { fillLegalPlaceholders } from './legalPlaceholders'

describe('fillLegalPlaceholders', () => {
  it('fills every occurrence of a known key', () => {
    const text = 'Run by {{operator_name}}. Contact {{ contact_email }} or {{contact_email}}.'
    expect(fillLegalPlaceholders(text, { operator_name: 'Example Ltd', contact_email: 'hi@example.com' })).toBe(
      'Run by Example Ltd. Contact hi@example.com or hi@example.com.',
    )
  })

  it('leaves unknown and non-string keys visible', () => {
    expect(fillLegalPlaceholders('{{missing}} {{draft}}', { draft: true })).toBe('{{missing}} {{draft}}')
  })
})
