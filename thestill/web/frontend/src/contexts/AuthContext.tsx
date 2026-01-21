import { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react'

// User type matching backend model
export interface User {
  id: string
  email: string
  name: string | null
  picture: string | null
  created_at: string
  last_login_at: string | null
}

// Auth status response from /auth/status
interface AuthStatus {
  multi_user: boolean
  authenticated: boolean
  user: User | null
}

interface AuthContextType {
  user: User | null
  isLoading: boolean
  isMultiUser: boolean
  isAuthenticated: boolean
  login: () => void
  logout: () => Promise<void>
  refreshAuth: () => Promise<void>
}

const AuthContext = createContext<AuthContextType | null>(null)

const AUTH_BASE = '/api/auth'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isMultiUser, setIsMultiUser] = useState(false)

  const refreshAuth = useCallback(async () => {
    try {
      const response = await fetch(`${AUTH_BASE}/status`)
      if (!response.ok) {
        throw new Error('Failed to fetch auth status')
      }
      const authStatus = await response.json() as AuthStatus

      setIsMultiUser(authStatus.multi_user)
      setUser(authStatus.user)
    } catch (error) {
      console.error('Auth status check failed:', error)
      setUser(null)
    } finally {
      setIsLoading(false)
    }
  }, [])

  // Check auth status on mount
  useEffect(() => {
    refreshAuth()
  }, [refreshAuth])

  const login = useCallback(() => {
    // Redirect to Google OAuth login
    window.location.href = `${AUTH_BASE}/google/login`
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

  return (
    <AuthContext.Provider
      value={{
        user,
        isLoading,
        isMultiUser,
        isAuthenticated,
        login,
        logout,
        refreshAuth,
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
