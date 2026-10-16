import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Login from './Login'

const auth = { isAnonymous: true, isAuthenticated: false, isLoading: false, isMultiUser: true, login: vi.fn() }
vi.mock('../contexts/AuthContext', () => ({ useAuth: () => auth }))

function renderAt(url: string) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <Login />
    </MemoryRouter>,
  )
}

describe('Login (spec #102)', () => {
  beforeEach(() => auth.login.mockReset())
  afterEach(() => window.sessionStorage.clear())

  it('offers the providers, the consent line and a way back', () => {
    renderAt('/login?next=%2Fpodcasts%2Fprof-g%2Fepisodes%2Fa')
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Sign in to thestill')
    expect(screen.getByRole('link', { name: 'Terms of Service' })).toHaveAttribute('href', '/terms')
    expect(screen.getByRole('link', { name: 'Privacy Policy' })).toHaveAttribute('href', '/privacy')
    expect(screen.getByRole('link', { name: 'Not now' })).toHaveAttribute('href', '/podcasts/prof-g/episodes/a')
    fireEvent.click(screen.getByRole('button', { name: 'Continue with Google' }))
    expect(auth.login).toHaveBeenCalledWith('/podcasts/prof-g/episodes/a')
    expect(window.sessionStorage.length).toBe(0)
  })

  it('says why, and remembers a Follow for the show it returns to', () => {
    renderAt('/login?next=%2Fpodcasts%2Fprof-g&intent=follow')
    expect(screen.getByText(/we'll follow the show for you/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Continue with Google' }))
    expect(JSON.parse(window.sessionStorage.getItem('thestill.pendingFollow') ?? '{}').slug).toBe('prof-g')
  })

  it('never remembers a Follow without a show to return to', () => {
    renderAt('/login?next=%2Finbox&intent=follow')
    fireEvent.click(screen.getByRole('button', { name: 'Continue with Google' }))
    expect(window.sessionStorage.length).toBe(0)
  })
})
