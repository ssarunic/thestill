import { Link } from 'react-router-dom'

interface MobileHeaderProps {
  onMenuClick: () => void
}

export default function MobileHeader({ onMenuClick }: MobileHeaderProps) {
  return (
    <header className="fixed top-0 left-0 right-0 h-14 bg-white border-b border-gray-200 flex items-center px-4 z-40">
      {/* Hamburger button */}
      <button
        onClick={onMenuClick}
        className="p-2 -ml-2 rounded-lg hover:bg-gray-100 transition-colors"
        aria-label="Open menu"
      >
        <svg className="w-6 h-6 text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
        </svg>
      </button>

      {/* Centered logo */}
      <div className="flex-1 flex justify-center">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 bg-primary-900 rounded-lg flex items-center justify-center flex-shrink-0">
            <span className="text-white font-bold text-sm">ts</span>
          </div>
          <span className="font-semibold text-primary-900">Thestill</span>
        </div>
      </div>

      {/* Search — balances the hamburger button and links to the full page */}
      <Link
        to="/search"
        className="p-2 -mr-2 rounded-lg hover:bg-gray-100 transition-colors"
        aria-label="Search"
      >
        <svg className="w-6 h-6 text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-4.35-4.35M11 19a8 8 0 100-16 8 8 0 000 16z" />
        </svg>
      </Link>
    </header>
  )
}
