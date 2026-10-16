// Spec #94 — the route tree a signed-out visitor sees in multi-user mode.
// Public pages render inside PublicLayout; every other path asks for
// sign-in and comes back afterwards. No player, inbox or per-user hooks
// are mounted here.
import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import PublicLayout from './components/public/PublicLayout'
import PublicPageViews from './analytics/PublicPageViews'

const Landing = lazy(() => import('./pages/public/Landing'))
const TopChart = lazy(() => import('./pages/public/TopChart'))
const Show = lazy(() => import('./pages/public/Show'))
const Episode = lazy(() => import('./pages/public/Episode'))
const EntityPage = lazy(() => import('./pages/public/EntityPage'))
const Legal = lazy(() => import('./pages/public/Legal'))
const NotFound = lazy(() => import('./pages/public/NotFound'))
const Login = lazy(() => import('./pages/Login'))

function SignInRedirect() {
  const location = useLocation()
  return <Navigate to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`} replace />
}

export default function PublicApp() {
  return (
    <>
    <PublicPageViews />
    <Routes>
      <Route
        path="/login"
        element={
          <Suspense fallback={null}>
            <Login />
          </Suspense>
        }
      />
      <Route element={<PublicLayout />}>
        {[
          ['/', <Landing />],
          ['/top', <TopChart />],
          ['/podcasts/:podcastSlug', <Show />],
          ['/podcasts/:podcastSlug/episodes/:episodeSlug', <Episode />],
          ['/entities/:entityType/:slug', <EntityPage />],
          ['/terms', <Legal doc="terms" />],
          ['/privacy', <Legal doc="privacy" />],
        ].map(([path, element]) => (
          <Route key={path as string} path={path as string} element={<Suspense fallback={null}>{element}</Suspense>} />
        ))}
        {/* Signed-in areas: ask for sign-in, then come back. */}
        {['/inbox', '/briefings/*', '/search', '/settings', '/episodes', '/status', '/queue', '/failed', '/analytics', '/podcasts'].map((path) => (
          <Route key={path} path={path} element={<SignInRedirect />} />
        ))}
        <Route
          path="*"
          element={
            <Suspense fallback={null}>
              <NotFound />
            </Suspense>
          }
        />
      </Route>
    </Routes>
    </>
  )
}
