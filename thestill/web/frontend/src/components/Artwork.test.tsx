import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import Artwork from './Artwork'

describe('Artwork (spec #76 §5.8)', () => {
  it('fixes size and radius per role', () => {
    render(<Artwork role="hero" sources={['https://example.com/a.jpg']} alt="Episode artwork" loading="eager" />)
    const img = screen.getByRole('img', { name: 'Episode artwork' })
    expect(img).toHaveAttribute('src', 'https://example.com/a.jpg')
    expect(img).toHaveAttribute('loading', 'eager')
    expect(img.className).toContain('max-w-[160px]')
    expect(img.className).toContain('rounded-xl')
  })

  it('renders the shared placeholder when there is no source', () => {
    const { container } = render(<Artwork role="inline" sources={[null, undefined]} />)
    const placeholder = container.firstElementChild!
    expect(placeholder).toHaveAttribute('aria-hidden', 'true')
    expect(placeholder.className).toContain('w-7')
    expect(placeholder.querySelector('svg')).not.toBeNull()
  })

  it('names the placeholder when alt text is given', () => {
    render(<Artwork role="card" sources={[]} alt="Show artwork" />)
    expect(screen.getByRole('img', { name: 'Show artwork' })).toBeInTheDocument()
  })
})
