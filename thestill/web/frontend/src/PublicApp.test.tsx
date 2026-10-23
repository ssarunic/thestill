import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import PublicApp from './PublicApp'

vi.mock('./components/public/PublicLayout', async () => {
  const { Outlet } = await import('react-router-dom')
  return { default: () => <Outlet /> }
})

vi.mock('./pages/Login', () => ({
  default: function LoginProbe() {
    const location = useLocation()
    return <div>LOGIN {location.search}</div>
  },
}))

vi.mock('./pages/public/Landing', () => ({ default: () => <div>LANDING</div> }))
vi.mock('./pages/public/NotFound', () => ({ default: () => <div>NOT_FOUND</div> }))

describe('PublicApp (spec #94)', () => {
  it('serves the landing page at /', async () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <PublicApp />
      </MemoryRouter>,
    )
    expect(await screen.findByText('LANDING')).toBeInTheDocument()
  })

  it('sends signed-in areas to sign-in and remembers where the visitor was', async () => {
    render(
      <MemoryRouter initialEntries={['/inbox?view=unread']}>
        <PublicApp />
      </MemoryRouter>,
    )
    expect(await screen.findByText(`LOGIN ?next=${encodeURIComponent('/inbox?view=unread')}`)).toBeInTheDocument()
  })

  it('keeps the fragment of the page it sends to sign-in', async () => {
    render(
      <MemoryRouter initialEntries={['/briefings/b1#item-3']}>
        <PublicApp />
      </MemoryRouter>,
    )
    expect(await screen.findByText(`LOGIN ?next=${encodeURIComponent('/briefings/b1#item-3')}`)).toBeInTheDocument()
  })

  it('answers unknown paths with the not-found page', async () => {
    render(
      <MemoryRouter initialEntries={['/nowhere']}>
        <PublicApp />
      </MemoryRouter>,
    )
    expect(await screen.findByText('NOT_FOUND')).toBeInTheDocument()
  })
})
