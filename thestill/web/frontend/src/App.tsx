import { Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Podcasts from './pages/Podcasts'
import PodcastDetail from './pages/PodcastDetail'
import EpisodeDetail from './pages/EpisodeDetail'

function App() {
  return (
    <Routes>
      <Route path="/" element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="podcasts" element={<Podcasts />} />
        <Route path="podcasts/:podcastSlug" element={<PodcastDetail />} />
        <Route path="podcasts/:podcastSlug/episodes/:episodeSlug" element={<EpisodeDetail />} />
      </Route>
    </Routes>
  )
}

export default App
