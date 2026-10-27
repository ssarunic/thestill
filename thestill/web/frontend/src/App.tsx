import { lazy, Suspense } from 'react'
import { Routes, Route, Navigate, useLocation } from 'react-router-dom'
import Layout from './components/Layout'
import ProtectedRoute from './components/ProtectedRoute'
import AdminRoute from './components/AdminRoute'
import { PlayerProvider } from './contexts/PlayerContext'
import FloatingVideoTile from './components/FloatingVideoTile'
import { useBackgroundLocation } from './hooks/useBackgroundLocation'
import { useAuth } from './contexts/AuthContext'
import AnalyticsBridge from './analytics/AnalyticsBridge'
import { PublicApp } from './publicEntry'
import { useScrollRestoration } from './hooks/useScrollRestoration'

// Lazy load pages for code splitting
const Status = lazy(() => import('./pages/Dashboard'))
const Podcasts = lazy(() => import('./pages/Podcasts'))
const PodcastDetail = lazy(() => import('./pages/PodcastDetail'))
const EpisodeDetail = lazy(() => import('./pages/EpisodeDetail'))
const Episodes = lazy(() => import('./pages/Episodes'))
const Inbox = lazy(() => import('./pages/Inbox'))
const Briefings = lazy(() => import('./pages/Briefings'))
const BriefingDetail = lazy(() => import('./pages/BriefingDetail'))
const FailedTasks = lazy(() => import('./pages/FailedTasks'))
const QueueViewer = lazy(() => import('./pages/QueueViewer'))
// Spec #95 — product analytics (admin).
const Analytics = lazy(() => import('./pages/Analytics'))
// Spec #104 — the admin console.
const AdminUsers = lazy(() => import('./pages/admin/AdminUsers'))
const AdminUserDetail = lazy(() => import('./pages/admin/AdminUserDetail'))
const AdminSettings = lazy(() => import('./pages/admin/AdminSettings'))
const AdminAudit = lazy(() => import('./pages/admin/AdminAudit'))
const Settings = lazy(() => import('./pages/Settings'))
const TopPodcasts = lazy(() => import('./pages/TopPodcasts'))
const SearchResults = lazy(() => import('./pages/SearchResults'))
const Entities = lazy(() => import('./pages/Entities'))
const Login = lazy(() => import('./pages/Login'))
const LegalPage = lazy(() => import('./pages/public/LegalPage'))
const OAuthConsent = lazy(() => import('./pages/OAuthConsent'))
const EpisodeReaderOverlay = lazy(() => import('./components/EpisodeReaderOverlay'))

// Loading fallback for page transitions
function PageLoader() {
  return (
    <div className="flex items-center justify-center min-h-[50vh]">
      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
    </div>
  )
}

// App-wide window scroll (top on a new page, restore on Back). Outside the
// page ``<Routes>`` like AnalyticsBridge: beneath a ``<Routes location>``
// every navigation reads as POP, so a fresh page kept the old offset.
function ScrollRestoration() {
  useScrollRestoration()
  return null
}

function App() {
  const location = useLocation()

  // Spec #52 — background-location pattern. An inbox row click pushes the
  // canonical episode URL with the inbox location stashed in navigation
  // state; while that state is present the page routes keep rendering the
  // inbox (still mounted, scroll/poll/cache intact) and the episode renders
  // in a reader overlay above it. Refresh/direct links have no state and
  // fall through to the standalone EpisodeDetail route as before.
  const backgroundLocation = useBackgroundLocation()
  const { isAnonymous } = useAuth()

  // Spec #94 — a signed-out visitor in multi-user mode gets the public site:
  // editorial pages for shows, episodes, people and entities, and sign-in
  // for everything else. ``isAnonymous`` stays false while the auth status
  // loads, so a signed-in deep link never flashes the public layout.
  if (isAnonymous) {
    return (
      <Suspense fallback={<PageLoader />}>
        <PublicApp />
      </Suspense>
    )
  }

  return (
    <PlayerProvider>
    {/* Spec #95 — outside the page Routes, so it sees the real URL. */}
    <AnalyticsBridge />
    <ScrollRestoration />
    <Routes location={backgroundLocation ?? location}>
      {/* Login page - outside protected routes */}
      <Route path="/login" element={
        <Suspense fallback={<PageLoader />}>
          <Login />
        </Suspense>
      } />

      {/* Spec #109 — the MCP OAuth consent screen: signed in, but outside the
          app layout (no sidebar or player between the reader and the decision). */}
      <Route path="/oauth/consent" element={
        <ProtectedRoute>
          <Suspense fallback={<PageLoader />}>
            <OAuthConsent />
          </Suspense>
        </ProtectedRoute>
      } />

      {/* Spec #94 — Terms and Privacy are readable signed in or not. */}
      {(['terms', 'privacy'] as const).map((doc) => (
        <Route key={doc} path={`/${doc}`} element={
          <Suspense fallback={<PageLoader />}>
            <LegalPage doc={doc} />
          </Suspense>
        } />
      ))}

      {/* Protected routes with layout */}
      <Route path="/" element={
        <ProtectedRoute>
          <Layout />
        </ProtectedRoute>
      }>
        {/* Inbox is the daily entry point — the root redirects there. The
            pipeline overview lives at /status (admin section). */}
        <Route index element={<Navigate to="/inbox" replace />} />
        <Route path="status" element={
          <AdminRoute>
            <Suspense fallback={<PageLoader />}>
              <Status />
            </Suspense>
          </AdminRoute>
        } />
        <Route path="podcasts" element={
          <Suspense fallback={<PageLoader />}>
            <Podcasts />
          </Suspense>
        } />
        <Route path="top" element={
          <Suspense fallback={<PageLoader />}>
            <TopPodcasts />
          </Suspense>
        } />
        <Route path="podcasts/:podcastSlug" element={
          <Suspense fallback={<PageLoader />}>
            <PodcastDetail />
          </Suspense>
        } />
        <Route path="podcasts/:podcastSlug/episodes/:episodeSlug" element={
          <Suspense fallback={<PageLoader />}>
            <EpisodeDetail />
          </Suspense>
        } />
        <Route path="episodes" element={
          <AdminRoute>
            <Suspense fallback={<PageLoader />}>
              <Episodes />
            </Suspense>
          </AdminRoute>
        } />
        <Route path="inbox" element={
          <Suspense fallback={<PageLoader />}>
            <Inbox />
          </Suspense>
        } />
        <Route path="briefings" element={
          <Suspense fallback={<PageLoader />}>
            <Briefings />
          </Suspense>
        } />
        <Route path="briefings/:briefingId" element={
          <Suspense fallback={<PageLoader />}>
            <BriefingDetail />
          </Suspense>
        } />
        {/* Admin-only: operator pipeline views (gated by require_admin server-side) */}
        <Route path="failed" element={
          <AdminRoute>
            <Suspense fallback={<PageLoader />}>
              <FailedTasks />
            </Suspense>
          </AdminRoute>
        } />
        <Route path="queue" element={
          <AdminRoute>
            <Suspense fallback={<PageLoader />}>
              <QueueViewer />
            </Suspense>
          </AdminRoute>
        } />
        <Route path="analytics" element={
          <AdminRoute>
            <Suspense fallback={<PageLoader />}>
              <Analytics />
            </Suspense>
          </AdminRoute>
        } />
        <Route path="admin/users" element={
          <AdminRoute>
            <Suspense fallback={<PageLoader />}>
              <AdminUsers />
            </Suspense>
          </AdminRoute>
        } />
        <Route path="admin/users/:userId" element={
          <AdminRoute>
            <Suspense fallback={<PageLoader />}>
              <AdminUserDetail />
            </Suspense>
          </AdminRoute>
        } />
        <Route path="admin/settings" element={
          <AdminRoute>
            <Suspense fallback={<PageLoader />}>
              <AdminSettings />
            </Suspense>
          </AdminRoute>
        } />
        <Route path="admin/audit" element={
          <AdminRoute>
            <Suspense fallback={<PageLoader />}>
              <AdminAudit />
            </Suspense>
          </AdminRoute>
        } />
        <Route path="settings" element={
          <Suspense fallback={<PageLoader />}>
            <Settings />
          </Suspense>
        } />
        <Route path="search" element={
          <Suspense fallback={<PageLoader />}>
            <SearchResults />
          </Suspense>
        } />
        {/* Spec #28 §5.1 — entity page (person/company/product/topic). */}
        <Route path="entities/:entityType/:idSlug" element={
          <Suspense fallback={<PageLoader />}>
            <Entities />
          </Suspense>
        } />
      </Route>
    </Routes>

    {/* Spec #61 §2 — floating video tile for off-reader video playback.
        Deliberately NOT mounted while the reader overlay is open: a global
        tile above the z-[45] overlay would fight its focus trap, one below
        would vanish behind the scrim (§3). Unmounting unregisters the
        floating slot, so playback simply continues audio-first. */}
    {!backgroundLocation && <FloatingVideoTile />}

    {/* Overlay pass — only mounted while navigation state carries a
        background location. PlayerProvider sits above both passes so
        playback continues across overlay open/close (spec #22 / #52). */}
    {backgroundLocation && (
      <Routes>
        <Route path="podcasts/:podcastSlug/episodes/:episodeSlug" element={
          <ProtectedRoute>
            <Suspense fallback={null}>
              <EpisodeReaderOverlay />
            </Suspense>
          </ProtectedRoute>
        } />
        {/* Any other URL carrying background state: no overlay to draw. */}
        <Route path="*" element={null} />
      </Routes>
    )}
    </PlayerProvider>
  )
}

export default App
