import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import MetaEyebrow from './MetaEyebrow'

describe('MetaEyebrow (spec #76 §5.3)', () => {
  it('joins items with a visual dot and a spoken comma', () => {
    const { container } = render(<MetaEyebrow items={['Sun 6 Sep', 'S3 E12', 'Explicit']} />)
    const p = container.querySelector('p')!
    expect(screen.getByText('Sun 6 Sep')).toBeInTheDocument()
    expect(screen.getByText('S3 E12')).toBeInTheDocument()
    expect(screen.getByText('Explicit')).toBeInTheDocument()
    const dots = container.querySelectorAll('[aria-hidden="true"]')
    expect(dots).toHaveLength(2)
    expect(dots[0]).toHaveTextContent('·')
    expect(container.querySelectorAll('.sr-only')).toHaveLength(2)
    expect(p.className).toContain('text-eyebrow')
  })

  it('drops empty items and renders nothing when all are empty', () => {
    render(<MetaEyebrow items={[null, undefined, false, '', 'Bonus']} />)
    expect(screen.getByText('Bonus').closest('p')!.querySelectorAll('span')).toHaveLength(1)
    const { container } = render(<MetaEyebrow items={[null, false]} />)
    expect(container).toBeEmptyDOMElement()
  })
})
