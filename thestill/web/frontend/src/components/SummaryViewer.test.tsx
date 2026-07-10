import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { SummaryCitation } from '../api/types'
import SummaryViewer from './SummaryViewer'

function citation(overrides: Partial<SummaryCitation> = {}): SummaryCitation {
  return {
    id: 'c3',
    raw_label: '49:30',
    cited_playback_s: 2970,
    target_playback_s: 2970,
    segment_id_hint: 42,
    source_segment_ids: [1001],
    resolved: true,
    ...overrides,
  }
}

describe('SummaryViewer citation links', () => {
  it('renders resolved citation links as clickable timestamp chips', () => {
    const onCite = vi.fn()
    const item = citation()

    render(
      <SummaryViewer
        content="Source: [49:30](?t=2970&cite=c3)"
        available
        citations={[item]}
        onCite={onCite}
      />,
    )

    const chip = screen.getByRole('button', {
      name: 'Play summary citation at 49:30',
    })
    expect(chip).toHaveTextContent('49:30')

    fireEvent.click(chip)

    expect(onCite).toHaveBeenCalledTimes(1)
    expect(onCite).toHaveBeenCalledWith(item)
  })

  it('renders unknown citation ids as plain text', () => {
    const { container } = render(
      <SummaryViewer
        content="Source: [10:00](?t=600&cite=missing)"
        available
        citations={[]}
        onCite={vi.fn()}
      />,
    )

    expect(screen.queryByRole('button', { name: /10:00/ })).toBeNull()
    expect(screen.queryByRole('link', { name: '10:00' })).toBeNull()
    expect(container.querySelector('p')?.textContent).toBe('Source: 10:00')
  })

  it('keeps ordinary external markdown links as anchors', () => {
    render(
      <SummaryViewer
        content="[Project site](https://example.com)"
        available
      />,
    )

    const link = screen.getByRole('link', { name: 'Project site' })
    expect(link).toHaveAttribute('href', 'https://example.com')
    expect(link).toHaveAttribute('target', '_blank')
  })
})
