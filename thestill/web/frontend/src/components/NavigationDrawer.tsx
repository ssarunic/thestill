import { useEffect } from 'react'
import { NavLink } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'
import { MAIN_NAV_ITEMS, ADMIN_NAV_ITEMS, SETTINGS_NAV_ITEM } from '../constants/navigation'

interface NavItemProps {
  to: string
  icon: React.ReactNode
  label: string
  onClick: () => void
}

function NavItem({ to, icon, label, onClick }: NavItemProps) {
  return (
    <NavLink
      to={to}
      onClick={onClick}
      className={({ isActive }) =>
        `flex items-center gap-3 px-4 py-3 rounded-lg transition-colors ${
          isActive
            ? 'bg-primary-900 text-white'
            : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
        }`
      }
    >
      {icon}
      <span className="text-base">{label}</span>
    </NavLink>
  )
}

interface NavigationDrawerProps {
  isOpen: boolean
  onClose: () => void
}

export default function NavigationDrawer({ isOpen, onClose }: NavigationDrawerProps) {
  const { isAdmin } = useAuth()

  // Close on escape key
  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) {
        onClose()
      }
    }
    document.addEventListener('keydown', handleEscape)
    return () => document.removeEventListener('keydown', handleEscape)
  }, [isOpen, onClose])

  // Prevent body scroll when drawer is open
  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = 'hidden'
    } else {
      document.body.style.overflow = ''
    }
    return () => {
      document.body.style.overflow = ''
    }
  }, [isOpen])

  return (
    <>
      {/* Overlay */}
      <div
        className={`fixed inset-0 bg-black/50 z-40 transition-opacity duration-300 ${
          isOpen ? 'opacity-100' : 'opacity-0 pointer-events-none'
        }`}
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Drawer */}
      <div
        className={`fixed top-0 left-0 h-full w-72 max-w-[85vw] bg-white z-50 transform transition-transform duration-300 ease-out ${
          isOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
        role="dialog"
        aria-modal="true"
        aria-label="Navigation menu"
      >
        {/* Header with close button */}
        <div className="flex items-center justify-between p-4 border-b border-gray-200">
          <div>
            <h1 className="text-xl font-bold text-primary-900">Thestill</h1>
            <p className="text-sm text-gray-500">Podcast Intelligence</p>
          </div>
          <button
            onClick={onClose}
            className="p-2 rounded-lg hover:bg-gray-100 transition-colors"
            aria-label="Close menu"
          >
            <svg className="w-6 h-6 text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Navigation — items come from constants/navigation.tsx, shared with
            the desktop sidebar (Layout) so the two surfaces cannot drift. */}
        <nav className="p-4 space-y-1">
          {MAIN_NAV_ITEMS.map((item) => (
            <NavItem key={item.to} to={item.to} icon={item.icon} label={item.label} onClick={onClose} />
          ))}
          {/* Admin section — operator-only, hidden for non-admins (multi-user). */}
          {isAdmin && (
            <>
              <div className="pt-4 pb-1 px-4">
                <span className="text-[11px] font-semibold uppercase tracking-wider text-gray-400">
                  Admin
                </span>
              </div>
              {ADMIN_NAV_ITEMS.map((item) => (
                <NavItem key={item.to} to={item.to} icon={item.icon} label={item.label} onClick={onClose} />
              ))}
            </>
          )}
          <NavItem
            to={SETTINGS_NAV_ITEM.to}
            icon={SETTINGS_NAV_ITEM.icon}
            label={SETTINGS_NAV_ITEM.label}
            onClick={onClose}
          />
        </nav>

        {/* Footer */}
        <div className="absolute bottom-0 left-0 right-0 p-4 border-t border-gray-200">
          <p className="text-xs text-gray-400 text-center">Read-only mode</p>
        </div>
      </div>
    </>
  )
}
