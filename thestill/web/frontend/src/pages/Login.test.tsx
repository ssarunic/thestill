import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Login from './Login'

const auth = {
  accountDisabled: false,
  adminContactEmail: null as string | null,
  isAnonymous: true,
  isAuthenticated: false,
  isLoading: false,
  isMultiUser: true,
  login: vi.fn(),
}
vi.mock('../contexts/AuthContext', () => ({ useAuth: () => auth }))

function renderAt(url: string, state?: unknown) {
  return render(
    <MemoryRouter initialEntries={[state === undefined ? url : { pathname: url, state }]}>
      <Login />
    </MemoryRouter>,
  )
}

describe('Login (spec #102)', () => {
  beforeEach(() => {
    auth.login.mockReset()
    auth.accountDisabled = false
    auth.adminContactEmail = null
  })
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

  it('takes next and intent from router state, the way public pages link here', () => {
    renderAt('/login', { next: '/podcasts/prof-g', intent: 'follow' })
    expect(screen.getByText(/we'll follow the show for you/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Not now' })).toHaveAttribute('href', '/podcasts/prof-g')
    fireEvent.click(screen.getByRole('button', { name: 'Continue with Google' }))
    expect(auth.login).toHaveBeenCalledWith('/podcasts/prof-g')
    expect(JSON.parse(window.sessionStorage.getItem('thestill.pendingFollow') ?? '{}').slug).toBe('prof-g')
  })

  it('falls back to the same-site referrer when a plain /login link was clicked before hydration', () => {
    const referrer = vi.spyOn(document, 'referrer', 'get').mockReturnValue(`${window.location.origin}/entities/person/x`)
    try {
      renderAt('/login')
      expect(screen.getByRole('link', { name: 'Not now' })).toHaveAttribute('href', '/entities/person/x')
      fireEvent.click(screen.getByRole('button', { name: 'Continue with Google' }))
      expect(auth.login).toHaveBeenCalledWith('/entities/person/x')
    } finally {
      referrer.mockRestore()
    }
  })

  it('ignores a referrer from another site', () => {
    const referrer = vi.spyOn(document, 'referrer', 'get').mockReturnValue('https://www.google.com/')
    try {
      renderAt('/login')
      expect(screen.getByRole('link', { name: 'Not now' })).toHaveAttribute('href', '/')
      fireEvent.click(screen.getByRole('button', { name: 'Continue with Google' }))
      expect(auth.login).toHaveBeenCalledWith(undefined)
    } finally {
      referrer.mockRestore()
    }
  })

  it('never remembers a Follow without a show to return to', () => {
    renderAt('/login?next=%2Finbox&intent=follow')
    fireEvent.click(screen.getByRole('button', { name: 'Continue with Google' }))
    expect(window.sessionStorage.length).toBe(0)
  })

  it('explains a disabled account, with the operator contact (spec #104)', () => {
    auth.adminContactEmail = 'ops@thestill.me'
    renderAt('/login?error=account_disabled')
    expect(screen.getByRole('alert')).toHaveTextContent('This account has been disabled. Contact ops@thestill.me.')
    expect(screen.getByRole('link', { name: 'ops@thestill.me' })).toHaveAttribute('href', 'mailto:ops@thestill.me')
  })

  it('explains a disabled session even without the query parameter', () => {
    auth.accountDisabled = true
    renderAt('/login')
    expect(screen.getByRole('alert')).toHaveTextContent('This account has been disabled.')
  })

  it('says signups are paused, and ignores unknown error codes', () => {
    const { unmount } = renderAt('/login?error=signups_closed')
    expect(screen.getByRole('alert')).toHaveTextContent('Signups are paused right now.')
    unmount()
    renderAt('/login?error=%3Cscript%3E')
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('invites another try after a cancelled or unfinished sign-in, without the operator contact', () => {
    auth.adminContactEmail = 'ops@thestill.me'
    const { unmount } = renderAt('/login?error=cancelled&next=%2Fpodcasts%2Fx%2Fepisodes%2Fy')
    expect(screen.getByRole('alert')).toHaveTextContent('Sign-in was cancelled. You can try again below.')
    expect(screen.queryByRole('link', { name: 'ops@thestill.me' })).toBeNull()

    // The retry still returns to the page sign-in started from.
    fireEvent.click(screen.getByRole('button', { name: 'Continue with Google' }))
    expect(auth.login).toHaveBeenCalledWith('/podcasts/x/episodes/y')
    unmount()

    renderAt('/login?error=failed')
    expect(screen.getByRole('alert')).toHaveTextContent("Sign-in didn't finish. Please try again.")
  })

  it('ignores error codes that only look like notices', () => {
    renderAt('/login?error=toString')
    expect(screen.queryByRole('alert')).toBeNull()
  })
})
