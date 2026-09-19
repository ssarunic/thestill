import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import EntityBacklogNotice from './EntityBacklogNotice'

describe('EntityBacklogNotice', () => {
  it('renders nothing when there is no backlog, or the server predates the field', () => {
    const { container, rerender } = render(<EntityBacklogNotice entityExtraction={undefined} />)
    expect(container).toBeEmptyDOMElement()
    rerender(<EntityBacklogNotice entityExtraction={{ available: false, skipped_unavailable: 0 }} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('says the backlog is growing when extraction is not installed', () => {
    render(<EntityBacklogNotice entityExtraction={{ available: false, skipped_unavailable: 1480 }} />)
    expect(screen.getByRole('status')).toHaveTextContent('1,480 episodes have no entity data')
    expect(screen.getByRole('status')).toHaveTextContent('not installed on this server')
  })

  it('asks for a backfill when extraction is available again', () => {
    render(<EntityBacklogNotice entityExtraction={{ available: true, skipped_unavailable: 1 }} />)
    expect(screen.getByRole('status')).toHaveTextContent('1 episode has no entity data')
    expect(screen.getByRole('status')).toHaveTextContent('Run a backfill')
  })
})
