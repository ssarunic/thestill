import { useState, useEffect } from 'react'
import { Outlet, NavLink } from 'react-router-dom'

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

function useIsLargeScreen() {
  const [isLarge, setIsLarge] = useState(
    typeof window !== 'undefined' ? window.innerWidth >= 1024 : false
  )

  useEffect(() => {
    const handleResize = () => {
      setIsLarge(window.innerWidth >= 1024)
    }

    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [])

  return isLarge
}

export default function Layout() {
  const [isExpanded, setIsExpanded] = useState(false)
  const isLargeScreen = useIsLargeScreen()

  // Close sidebar when resizing to large screen
  useEffect(() => {
    if (isLargeScreen) {
      setIsExpanded(false)
    }
  }, [isLargeScreen])

  const showLabels = isExpanded || isLargeScreen

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

  const failedIcon = (
    <svg className="w-5 h-5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
    </svg>
  )

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Overlay for mobile when sidebar is expanded */}
      {isExpanded && (
        <div
          className="fixed inset-0 bg-black/50 z-30 lg:hidden"
          onClick={() => setIsExpanded(false)}
        />
      )}

      {/* Sidebar */}
      <aside
        className={`fixed left-0 top-0 h-full bg-white border-r border-gray-200 flex flex-col z-40 transition-all duration-300 ${
          isExpanded ? 'w-64' : 'w-16 lg:w-64'
        }`}
      >
        {/* Logo */}
        <div className={`border-b border-gray-200 ${showLabels ? 'p-6' : 'p-3'}`}>
          <div className={`flex items-center ${showLabels ? '' : 'justify-center'}`}>
            {/* Logo icon - visible on collapsed mobile/tablet */}
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

        {/* Hamburger button - visible on collapsed mobile/tablet */}
        {!isLargeScreen && !isExpanded && (
          <button
            onClick={() => setIsExpanded(true)}
            className="p-3 mx-2 mt-2 rounded-lg hover:bg-gray-100 transition-colors"
            aria-label="Open menu"
          >
            <svg className="w-5 h-5 text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>
        )}

        {/* Close button - visible on expanded mobile/tablet */}
        {!isLargeScreen && isExpanded && (
          <button
            onClick={() => setIsExpanded(false)}
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
            to="/failed"
            icon={failedIcon}
            label="Failed Tasks"
            showLabel={showLabels}
          />
        </nav>

        {/* Footer */}
        <div className={`border-t border-gray-200 ${showLabels ? 'p-4' : 'p-2'}`}>
          {showLabels && (
            <p className="text-xs text-gray-400 text-center">
              Read-only mode
            </p>
          )}
        </div>
      </aside>

      {/* Main content */}
      <main className="ml-16 lg:ml-64 min-h-screen transition-all duration-300">
        <div className="p-4 md:p-6 lg:p-8">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
