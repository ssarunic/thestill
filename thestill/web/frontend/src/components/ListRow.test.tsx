import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import ListRow, { ListRowArtwork } from './ListRow'
import ListGroup from './ListGroup'

function renderRows(ui: React.ReactNode) {
  return render(<MemoryRouter>{ui}</MemoryRouter>)
}

describe('ListRow', () => {
  it('renders the title as a real link stretched over the row, with a trailing action above it', async () => {
    const user = userEvent.setup()
    const onFollow = vi.fn()
    renderRows(
      <ListGroup as="ol">
        <ListRow
          rank={3}
          leading={<ListRowArtwork src={null} />}
          title="The News Agents"
          subtitle="Global · Daily News"
          to="/podcasts/the-news-agents"
          ariaLabel="Open The News Agents"
          trailing={
            <button type="button" onClick={onFollow}>
              Follow
            </button>
          }
        />
      </ListGroup>,
    )

    const link = screen.getByRole('link', { name: 'Open The News Agents' })
    expect(link).toHaveAttribute('href', '/podcasts/the-news-agents')
    // The overlay classes are what make the whole row the hit area.
    expect(link.className).toContain('after:absolute')
    expect(link.className).toContain('after:inset-0')
    // No nested interactive content: the button is a sibling of the link.
    expect(link.querySelector('button')).toBeNull()
    expect(screen.getByText('3')).toBeInTheDocument()
    expect(screen.getByText('Global · Daily News')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Follow' }))
    expect(onFollow).toHaveBeenCalledTimes(1)
  })

  it('renders a button when the row has an action but no link target', async () => {
    const user = userEvent.setup()
    const onClick = vi.fn()
    renderRows(
      <ListGroup>
        <ListRow title="Not imported yet" onClick={onClick} busy />
      </ListGroup>,
    )
    await user.click(screen.getByRole('button', { name: 'Not imported yet' }))
    expect(onClick).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('listitem')).toHaveAttribute('aria-busy', 'true')
  })

  it('lets the caller own the title weight (Inbox read/unread)', () => {
    renderRows(
      <ListGroup>
        <ListRow title="Unread one" to="/a" titleClassName="font-semibold text-gray-900" />
        <ListRow title="Read one" to="/b" titleClassName="font-normal text-gray-600" />
      </ListGroup>,
    )
    expect(screen.getByText('Unread one')).toHaveClass('font-semibold')
    expect(screen.getByText('Unread one')).not.toHaveClass('font-medium')
    expect(screen.getByText('Read one')).toHaveClass('font-normal')
  })

  it('ListGroup runs full-bleed on phones and boxed from sm', () => {
    renderRows(
      <ListGroup>
        <ListRow title="x" />
      </ListGroup>,
    )
    const list = screen.getByRole('list')
    expect(list.className).toContain('-mx-4')
    expect(list.className).toContain('sm:mx-0')
    expect(list.className).toContain('sm:rounded-lg')
    expect(list.className).toContain('divide-y')
  })
})
