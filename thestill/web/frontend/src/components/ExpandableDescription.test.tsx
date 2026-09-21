import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import ExpandableDescription from './ExpandableDescription'

const LONG = 'A long description. <a href="https://example.com/notes">Show notes</a>'

describe('ExpandableDescription', () => {
  let scrollHeight = 500

  beforeEach(() => {
    scrollHeight = 500
    // jsdom lays nothing out; the component measures scrollHeight to decide on "More".
    vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockImplementation(() => scrollHeight)
  })

  afterEach(() => {
    vi.restoreAllMocks()
    window.getSelection()?.removeAllRanges()
  })

  it('expands and collapses on a click anywhere in the text', () => {
    render(<ExpandableDescription html={LONG} />)
    const button = screen.getByRole('button', { name: 'More' })
    const text = screen.getByText(/A long description/)

    fireEvent.click(text)
    expect(button).toHaveAttribute('aria-expanded', 'true')
    expect(text.className).not.toContain('line-clamp-3')

    fireEvent.click(text)
    expect(button).toHaveAttribute('aria-expanded', 'false')
    expect(text.className).toContain('line-clamp-3')
  })

  it('leaves a click on a link to the link', () => {
    render(<ExpandableDescription html={LONG} />)
    fireEvent.click(screen.getByRole('link', { name: 'Show notes' }))
    expect(screen.getByRole('button', { name: 'More' })).toHaveAttribute('aria-expanded', 'false')
  })

  it('does not toggle when the click ends a text selection', () => {
    render(<ExpandableDescription html={LONG} />)
    const text = screen.getByText(/A long description/)
    const range = document.createRange()
    range.selectNodeContents(text)
    window.getSelection()?.addRange(range)

    fireEvent.click(text)
    expect(screen.getByRole('button', { name: 'More' })).toHaveAttribute('aria-expanded', 'false')
  })

  it('is not clickable when the text already fits', () => {
    scrollHeight = 10
    render(<ExpandableDescription html="Short." />)
    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.getByText('Short.').className).not.toContain('cursor-pointer')
  })
})
