import { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react'
import { safeNextPath } from '../utils/safeNext'
import { resetAnalytics } from '../analytics/client'
import { apiErrorMessage } from '../api/errors'

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
  // Spec #95 — the Settings switch for usage events.
  usage_analytics_enabled?: boolean
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
  // Spec #95: usage events exist (shows the Settings switch), and the key
  // this reader's app initialises with — null when off, opted out or GPC.
  usage_analytics_available?: boolean
  analytics?: { key: string } | null
  // Spec #104: the session belongs to a suspended or banned account — it
  // reads as signed out, and /login says why (with the operator contact).
  account_disabled?: boolean
  admin_contact_email?: string | null
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
  // Spec #95 — see AuthStatus.
  usageAnalyticsAvailable: boolean
  analyticsKey: string | null
  // Spec #104 — see AuthStatus.
  accountDisabled: boolean
  adminContactEmail: string | null
  // ``next``: same-origin path to return to after sign-in (spec #94).
  login: (next?: string) => void
  logout: () => Promise<void>
  refreshAuth: () => Promise<void>
  updateRegion: (region: string | null) => Promise<void>
  updateUsageAnalytics: (enabled: boolean) => Promise<void>
}

const AuthContext = createContext<AuthContextType | null>(null)

const AUTH_BASE = '/api/auth'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isMultiUser, setIsMultiUser] = useState(false)
  const [emailDeliveryAvailable, setEmailDeliveryAvailable] = useState(false)
  const [publicPages, setPublicPages] = useState(false)
  const [usageAnalyticsAvailable, setUsageAnalyticsAvailable] = useState(false)
  const [analyticsKey, setAnalyticsKey] = useState<string | null>(null)
  const [accountDisabled, setAccountDisabled] = useState(false)
  const [adminContactEmail, setAdminContactEmail] = useState<string | null>(null)

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
      setUsageAnalyticsAvailable(authStatus.usage_analytics_available === true)
      setAnalyticsKey(authStatus.analytics?.key ?? null)
      setAccountDisabled(authStatus.account_disabled === true)
      setAdminContactEmail(authStatus.admin_contact_email ?? null)
    } catch (error) {
      console.error('Auth status check failed:', error)
      setUser(null)
    } finally {
      setIsLoading(false)
    }
  }, [])

  // PATCH /me changes only the fields sent.
  const patchMe = useCallback(async (fields: Record<string, unknown>, what: string) => {
    const response = await fetch(`${AUTH_BASE}/me`, {
      method: 'PATCH',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(fields),
    })
    if (!response.ok) {
      const body = await response.json().catch(() => null)
      throw new Error(body ? apiErrorMessage(body, response.status) : `Failed to update ${what} (${response.status})`)
    }
    const body = await response.json()
    if (body?.user) {
      setUser(body.user as User)
    }
  }, [])

  const updateRegion = useCallback(
    (region: string | null) => patchMe({ region }, 'region'),
    [patchMe],
  )

  // Spec #95 — the status decides whether this reader gets a key, so re-read
  // it after the switch flips; AnalyticsBridge then starts or stops.
  const updateUsageAnalytics = useCallback(
    async (enabled: boolean) => {
      await patchMe({ usage_analytics_enabled: enabled }, 'usage events')
      await refreshAuth()
    },
    [patchMe, refreshAuth],
  )

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
      resetAnalytics()
      setUser(null)
      setAnalyticsKey(null)
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
        usageAnalyticsAvailable,
        analyticsKey,
        accountDisabled,
        adminContactEmail,
        login,
        logout,
        refreshAuth,
        updateRegion,
        updateUsageAnalytics,
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

// For providers that also render outside an AuthProvider (isolated tests):
// null instead of a throw.
export function useOptionalAuth(): AuthContextType | null {
  return useContext(AuthContext)
}
