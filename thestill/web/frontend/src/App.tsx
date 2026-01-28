import { lazy, Suspense } from 'react'
import { Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import ProtectedRoute from './components/ProtectedRoute'

// Lazy load pages for code splitting
const Dashboard = lazy(() => import('./pages/Dashboard'))
const Podcasts = lazy(() => import('./pages/Podcasts'))
const PodcastDetail = lazy(() => import('./pages/PodcastDetail'))
const EpisodeDetail = lazy(() => import('./pages/EpisodeDetail'))
const Episodes = lazy(() => import('./pages/Episodes'))
const Digests = lazy(() => import('./pages/Digests'))
const DigestDetail = lazy(() => import('./pages/DigestDetail'))
const FailedTasks = lazy(() => import('./pages/FailedTasks'))
const QueueViewer = lazy(() => import('./pages/QueueViewer'))
const Login = lazy(() => import('./pages/Login'))

// Loading fallback for page transitions
function PageLoader() {
  return (
    <div className="flex items-center justify-center min-h-[50vh]">
      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
    </div>
  )
}

function App() {
  return (
    <Routes>
      {/* Login page - outside protected routes */}
      <Route path="/login" element={
        <Suspense fallback={<PageLoader />}>
          <Login />
        </Suspense>
      } />

      {/* Protected routes with layout */}
      <Route path="/" element={
        <ProtectedRoute>
          <Layout />
        </ProtectedRoute>
      }>
        <Route index element={
          <Suspense fallback={<PageLoader />}>
            <Dashboard />
          </Suspense>
        } />
        <Route path="podcasts" element={
          <Suspense fallback={<PageLoader />}>
            <Podcasts />
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
          <Suspense fallback={<PageLoader />}>
            <Episodes />
          </Suspense>
        } />
        <Route path="digests" element={
          <Suspense fallback={<PageLoader />}>
            <Digests />
          </Suspense>
        } />
        <Route path="digests/:digestId" element={
          <Suspense fallback={<PageLoader />}>
            <DigestDetail />
          </Suspense>
        } />
        <Route path="failed" element={
          <Suspense fallback={<PageLoader />}>
            <FailedTasks />
          </Suspense>
        } />
        <Route path="queue" element={
          <Suspense fallback={<PageLoader />}>
            <QueueViewer />
          </Suspense>
        } />
      </Route>
    </Routes>
  )
}

export default App
