import { Navigate } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'

interface AdminRouteProps {
  children: React.ReactNode
}

/**
 * Wrapper component that protects operator-only routes (task queue, DLQ).
 *
 * In single-user mode (MULTI_USER=false): the local user is the operator, so
 * access is always allowed.
 * In multi-user mode (MULTI_USER=true): access requires an admin user;
 * non-admins are redirected to the dashboard.
 *
 * This mirrors the server-side `require_admin` guard — the frontend hides the
 * nav and blocks the route, but the API enforces access independently.
 */
export default function AdminRoute({ children }: AdminRouteProps) {
  const { isAdmin, isLoading } = useAuth()

  // Show loading state while checking auth
  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[50vh]">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
      </div>
    )
  }

  if (!isAdmin) {
    return <Navigate to="/" replace />
  }

  return <>{children}</>
}
