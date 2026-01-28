import { useState, useEffect } from 'react'
import { Outlet, NavLink } from 'react-router-dom'
import MobileHeader from './MobileHeader'
import NavigationDrawer from './NavigationDrawer'
import UserMenu from './UserMenu'

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

export default function Layout() {
  const [isDrawerOpen, setIsDrawerOpen] = useState(false)
  const [isSidebarExpanded, setIsSidebarExpanded] = useState(false)
  const screenSize = useScreenSize()

  // Close sidebar/drawer when screen size changes
  useEffect(() => {
    setIsDrawerOpen(false)
    setIsSidebarExpanded(false)
  }, [screenSize])

  const showLabels = isSidebarExpanded || screenSize === 'desktop'

  const dashboardIcon = (
    <svg className="w-5 h-5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
    </svg>
  )

  const podcastsIcon = (
    <svg className="w-5 h-5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
    </svg>
  )

  const episodesIcon = (
    <svg className="w-5 h-5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
    </svg>
  )

  const digestsIcon = (
    <svg className="w-5 h-5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
    </svg>
  )

  const failedIcon = (
    <svg className="w-5 h-5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
    </svg>
  )

  const queueIcon = (
    <svg className="w-5 h-5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 10h16M4 14h16M4 18h16" />
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
                    <h1 className="text-xl font-bold text-primary-900">thestill.me</h1>
                    <p className="text-sm text-gray-500 mt-1">Podcast Transcription</p>
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
              <NavItem
                to="/"
                icon={dashboardIcon}
                label="Dashboard"
                showLabel={showLabels}
              />
              <NavItem
                to="/podcasts"
                icon={podcastsIcon}
                label="Podcasts"
                showLabel={showLabels}
              />
              <NavItem
                to="/episodes"
                icon={episodesIcon}
                label="Episodes"
                showLabel={showLabels}
              />
              <NavItem
                to="/digests"
                icon={digestsIcon}
                label="Digests"
                showLabel={showLabels}
              />
              <NavItem
                to="/failed"
                icon={failedIcon}
                label="Failed Tasks"
                showLabel={showLabels}
              />
              <NavItem
                to="/queue"
                icon={queueIcon}
                label="Task Queue"
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
      <main className="min-h-screen transition-all duration-300 ml-0 pt-14 sm:pt-0 sm:ml-16 lg:ml-64">
        <div className="p-4 md:p-6 lg:p-8">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
