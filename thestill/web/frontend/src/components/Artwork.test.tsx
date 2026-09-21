import { describe, it, expect } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import Artwork from './Artwork'

function loadAs(img: HTMLElement, naturalWidth: number, naturalHeight: number) {
  Object.defineProperty(img, 'naturalWidth', { value: naturalWidth, configurable: true })
  Object.defineProperty(img, 'naturalHeight', { value: naturalHeight, configurable: true })
  fireEvent.load(img)
}

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

  it('widens the hero to 16:9 for a landscape image instead of cropping its sides', () => {
    render(<Artwork role="hero" sources={['https://i.ytimg.com/vi/x/maxresdefault.jpg']} alt="Episode artwork" />)
    const img = screen.getByRole('img', { name: 'Episode artwork' })
    expect(img.className).toContain('aspect-square')

    loadAs(img, 1280, 720)
    expect(img.className).toContain('aspect-video')
    expect(img.className).not.toContain('aspect-square')
    expect(img.className).not.toContain('max-w-[160px]')
  })

  it('keeps the hero square for square artwork', () => {
    render(<Artwork role="hero" sources={['https://example.com/a.jpg']} alt="Episode artwork" />)
    const img = screen.getByRole('img', { name: 'Episode artwork' })
    loadAs(img, 3000, 3000)
    expect(img.className).toContain('aspect-square')
  })

  it('keeps small roles square even for a landscape image', () => {
    render(<Artwork role="row" sources={['https://i.ytimg.com/vi/x/maxresdefault.jpg']} alt="Row artwork" />)
    const img = screen.getByRole('img', { name: 'Row artwork' })
    loadAs(img, 1280, 720)
    expect(img.className).toContain('aspect-square')
    expect(img.className).toContain('w-12')
  })
})
