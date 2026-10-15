import { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react'
import { safeNextPath } from '../utils/safeNext'

// User type matching backend model
export interface User {
  id: string
  email: string
  name: string | null
  picture: string | null
  created_at: string
  last_login_at: string | null
  region: string | null
  region_locked: boolean
  is_admin: boolean
}

// Auth status response from /auth/status
interface AuthStatus {
  multi_user: boolean
  authenticated: boolean
  user: User | null
  // Spec #51 capability flag: false when EMAIL_PROVIDER=none, hiding the
  // briefing email-delivery checkbox in settings.
  email_delivery_available?: boolean
  // Spec #94: signed-out visitors get the public site (PUBLIC_PAGES_ENABLED).
  public_pages?: boolean
}

interface AuthContextType {
  user: User | null
  isLoading: boolean
  isMultiUser: boolean
  isAuthenticated: boolean
  // Spec #94 — a signed-out visitor in multi-user mode (false while the
  // auth status is still loading, and always false in single-user mode).
  isAnonymous: boolean
  isAdmin: boolean
  emailDeliveryAvailable: boolean
  // ``next``: same-origin path to return to after sign-in (spec #94).
  login: (next?: string) => void
  logout: () => Promise<void>
  refreshAuth: () => Promise<void>
  updateRegion: (region: string | null) => Promise<void>
}

const AuthContext = createContext<AuthContextType | null>(null)

const AUTH_BASE = '/api/auth'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isMultiUser, setIsMultiUser] = useState(false)
  const [emailDeliveryAvailable, setEmailDeliveryAvailable] = useState(false)
  const [publicPages, setPublicPages] = useState(false)

  const refreshAuth = useCallback(async () => {
    try {
      const response = await fetch(`${AUTH_BASE}/status`, { credentials: 'include' })
      if (!response.ok) {
        throw new Error('Failed to fetch auth status')
      }
      const authStatus = await response.json() as AuthStatus

      setIsMultiUser(authStatus.multi_user)
      setUser(authStatus.user)
      setEmailDeliveryAvailable(authStatus.email_delivery_available === true)
      setPublicPages(authStatus.public_pages === true)
    } catch (error) {
      console.error('Auth status check failed:', error)
      setUser(null)
    } finally {
      setIsLoading(false)
    }
  }, [])

  const updateRegion = useCallback(async (region: string | null) => {
    const response = await fetch(`${AUTH_BASE}/me`, {
      method: 'PATCH',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ region }),
    })
    if (!response.ok) {
      const body = await response.json().catch(() => ({}))
      throw new Error(body.detail || `Failed to update region (${response.status})`)
    }
    const body = await response.json()
    if (body?.user) {
      setUser(body.user as User)
    }
  }, [])

  // Check auth status on mount
  useEffect(() => {
    // Fetch-on-mount: every setState in refreshAuth runs after an await, so
    // nothing updates synchronously inside this effect.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refreshAuth()
  }, [refreshAuth])

  const login = useCallback((next?: string) => {
    // Redirect to Google OAuth login; the server validates ``next`` again
    // and returns here after the callback.
    const safe = safeNextPath(next)
    window.location.href = `${AUTH_BASE}/google/login${safe ? `?next=${encodeURIComponent(safe)}` : ''}`
  }, [])

  const logout = useCallback(async () => {
    try {
      await fetch(`${AUTH_BASE}/logout`, { method: 'POST' })
      setUser(null)
      // In multi-user mode, redirect to login page after logout
      if (isMultiUser) {
        window.location.href = '/login'
      }
    } catch (error) {
      console.error('Logout failed:', error)
    }
  }, [isMultiUser])

  const isAuthenticated = user !== null
  // A signed-out visitor who gets the public site; with public pages off
  // they stay on the sign-in wall like before spec #94.
  const isAnonymous = !isLoading && isMultiUser && !isAuthenticated && publicPages
  // Single-user mode: the local user is always the operator (admin), even if
  // the stored flag is unset on a pre-existing database. In multi-user mode,
  // admin is gated on the server-provided is_admin flag.
  const isAdmin = isMultiUser ? user?.is_admin === true : true

  return (
    <AuthContext.Provider
      value={{
        user,
        isLoading,
        isMultiUser,
        isAuthenticated,
        isAnonymous,
        isAdmin,
        emailDeliveryAvailable,
        login,
        logout,
        refreshAuth,
        updateRegion,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return context
}
