import { useState, useEffect } from 'react'
import { Outlet, NavLink } from 'react-router-dom'
import MobileHeader from './MobileHeader'
import MiniPlayer from './MiniPlayer'
import NavigationDrawer from './NavigationDrawer'
import UserMenu from './UserMenu'
import CommandBar from './CommandBar'
import { PlayerProvider, usePlayer } from '../contexts/PlayerContext'
import { useAuth } from '../contexts/AuthContext'
import { MAIN_NAV_ITEMS, ADMIN_NAV_ITEMS, SETTINGS_NAV_ITEM } from '../constants/navigation'

interface NavItemProps {
  to: string
  icon: React.ReactNode
  label: string
  showLabel: boolean
}

function NavItem({ to, icon, label, showLabel }: NavItemProps) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        `flex items-center gap-3 px-3 py-3 rounded-lg transition-colors ${
          showLabel ? 'justify-start' : 'justify-center'
        } ${
          isActive
            ? 'bg-primary-900 text-white'
            : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
        }`
      }
      title={!showLabel ? label : undefined}
    >
      {icon}
      {showLabel && <span>{label}</span>}
    </NavLink>
  )
}

type ScreenSize = 'mobile' | 'tablet' | 'desktop'

function useScreenSize(): ScreenSize {
  const [screenSize, setScreenSize] = useState<ScreenSize>(() => {
    if (typeof window === 'undefined') return 'desktop'
    if (window.innerWidth < 640) return 'mobile'
    if (window.innerWidth < 1024) return 'tablet'
    return 'desktop'
  })

  useEffect(() => {
    const handleResize = () => {
      if (window.innerWidth < 640) setScreenSize('mobile')
      else if (window.innerWidth < 1024) setScreenSize('tablet')
      else setScreenSize('desktop')
    }

    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [])

  return screenSize
}

function LayoutContent() {
  const [isDrawerOpen, setIsDrawerOpen] = useState(false)
  const [isSidebarExpanded, setIsSidebarExpanded] = useState(false)
  const [isCommandBarOpen, setIsCommandBarOpen] = useState(false)
  const screenSize = useScreenSize()
  const { track } = usePlayer()
  const { isAdmin } = useAuth()

  // Close sidebar/drawer when screen size changes
  useEffect(() => {
    setIsDrawerOpen(false)
    setIsSidebarExpanded(false)
  }, [screenSize])

  // Spec #28 §4.1 — global ⌘K / Ctrl+K toggle for the command bar.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setIsCommandBarOpen((open) => !open)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  const showLabels = isSidebarExpanded || screenSize === 'desktop'

  const searchIcon = (
    <svg className="w-5 h-5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-4.35-4.35M11 19a8 8 0 100-16 8 8 0 000 16z" />
    </svg>
  )

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Mobile: Fixed header + navigation drawer (hidden on sm: and up via CSS) */}
      <div className="sm:hidden">
        <MobileHeader onMenuClick={() => setIsDrawerOpen(true)} />
        <NavigationDrawer
          isOpen={isDrawerOpen}
          onClose={() => setIsDrawerOpen(false)}
        />
      </div>

      {/* Tablet/Desktop: Sidebar (hidden on mobile via CSS) */}
      <div className="hidden sm:block">
        {/* Overlay for tablet when sidebar is expanded */}
        {isSidebarExpanded && screenSize === 'tablet' && (
          <div
            className="fixed inset-0 bg-black/50 z-30"
            onClick={() => setIsSidebarExpanded(false)}
          />
        )}

        <aside
          className={`fixed left-0 top-0 h-full bg-white border-r border-gray-200 flex flex-col z-40 transition-all duration-300 ${
            isSidebarExpanded ? 'w-64' : 'w-16 lg:w-64'
          }`}
        >
            {/* Logo */}
            <div className={`border-b border-gray-200 ${showLabels ? 'p-6' : 'p-3'}`}>
              <div className={`flex items-center ${showLabels ? '' : 'justify-center'}`}>
                {/* Logo icon - visible on collapsed tablet */}
                {!showLabels && (
                  <div className="w-8 h-8 bg-primary-900 rounded-lg flex items-center justify-center flex-shrink-0">
                    <span className="text-white font-bold text-sm">ts</span>
                  </div>
                )}
                {/* Full logo - visible on expanded or desktop */}
                {showLabels && (
                  <div>
                    <h1 className="text-xl font-bold text-primary-900">Thestill</h1>
                    <p className="text-sm text-gray-500 mt-1">Podcast Intelligence</p>
                  </div>
                )}
              </div>
            </div>

            {/* Hamburger button - visible on collapsed tablet */}
            {screenSize === 'tablet' && !isSidebarExpanded && (
              <button
                onClick={() => setIsSidebarExpanded(true)}
                className="p-3 mx-2 mt-2 rounded-lg hover:bg-gray-100 transition-colors"
                aria-label="Open menu"
              >
                <svg className="w-5 h-5 text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
                </svg>
              </button>
            )}

            {/* Close button - visible on expanded tablet */}
            {screenSize === 'tablet' && isSidebarExpanded && (
              <button
                onClick={() => setIsSidebarExpanded(false)}
                className="p-3 mx-2 mt-2 rounded-lg hover:bg-gray-100 transition-colors self-end"
                aria-label="Close menu"
              >
                <svg className="w-5 h-5 text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            )}

            {/* Navigation */}
            <nav className={`flex-1 space-y-1 ${showLabels ? 'p-4' : 'p-2'}`}>
              {/* Search links to the full results page; ⌘K still opens the
                  quick-jump command bar (spec #28 §4.1). */}
              <NavLink
                to="/search"
                title={!showLabels ? 'Search' : undefined}
                className={({ isActive }) =>
                  `flex w-full items-center gap-3 rounded-lg px-3 py-3 transition-colors ${
                    showLabels ? 'justify-start' : 'justify-center'
                  } ${
                    isActive
                      ? 'bg-primary-900 text-white'
                      : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
                  }`
                }
                data-testid="search-nav"
              >
                {searchIcon}
                {showLabels && (
                  <>
                    <span className="flex-1 text-left">Search</span>
                    <kbd className="hidden lg:inline-flex items-center rounded border border-gray-200 bg-gray-50 px-1.5 py-0.5 text-[10px] text-gray-500">
                      ⌘K
                    </kbd>
                  </>
                )}
              </NavLink>
              {MAIN_NAV_ITEMS.map((item) => (
                <NavItem
                  key={item.to}
                  to={item.to}
                  icon={item.icon}
                  label={item.label}
                  showLabel={showLabels}
                />
              ))}
              {/* Admin section — operator-only pipeline controls. Hidden for
                  non-admins (multi-user mode); always shown for the local user
                  in single-user mode. */}
              {isAdmin && (
                <>
                  <div className="pt-4 pb-1" aria-hidden={!showLabels}>
                    {showLabels ? (
                      <span className="px-3 text-[11px] font-semibold uppercase tracking-wider text-gray-400">
                        Admin
                      </span>
                    ) : (
                      <div className="mx-2 border-t border-gray-200" />
                    )}
                  </div>
                  {ADMIN_NAV_ITEMS.map((item) => (
                    <NavItem
                      key={item.to}
                      to={item.to}
                      icon={item.icon}
                      label={item.label}
                      showLabel={showLabels}
                    />
                  ))}
                </>
              )}
              <NavItem
                to={SETTINGS_NAV_ITEM.to}
                icon={SETTINGS_NAV_ITEM.icon}
                label={SETTINGS_NAV_ITEM.label}
                showLabel={showLabels}
              />
            </nav>

            {/* Footer with user menu */}
            <div className={`border-t border-gray-200 ${showLabels ? 'p-4' : 'p-2'}`}>
              {showLabels ? (
                <UserMenu />
              ) : (
                <div className="flex justify-center">
                  <UserMenu />
                </div>
              )}
            </div>
          </aside>
        </div>


      {/* Main content */}
      {/*
        Use CSS classes for responsive margins/padding to ensure correct styles on first render.
        Mobile (<640px): no margin, pt-14 for fixed header
        Tablet (640-1024px): ml-16 for collapsed sidebar
        Desktop (≥1024px): ml-64 for full sidebar
      */}
      <main
        className={`min-h-screen transition-all duration-300 ml-0 pt-14 sm:pt-0 sm:ml-16 lg:ml-64 ${
          track ? 'pb-24' : ''
        }`}
      >
        <div className="p-4 md:p-6 lg:p-8">
          <Outlet />
        </div>
      </main>

      <MiniPlayer />

      <CommandBar isOpen={isCommandBarOpen} onClose={() => setIsCommandBarOpen(false)} />
    </div>
  )
}

export default function Layout() {
  return (
    <PlayerProvider>
      <LayoutContent />
    </PlayerProvider>
  )
}
