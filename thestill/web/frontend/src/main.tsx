import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
// Self-hosted so the font loads under our `style-src 'self'` CSP and
// without a third-party request; one variable file covers every weight.
import '@fontsource-variable/inter/wght.css'
import './index.css'
// Spec #105 — in the entry stylesheet (not the public chunk's), so a page
// the server wrote is styled before any JavaScript runs.
import './styles/public.css'
import App from './App'
import { ToastProvider } from './components/Toast'
import { AuthProvider } from './contexts/AuthContext'
import { createQueryClient } from './queryClient'
import ErrorBoundary from './components/ErrorBoundary'
import { installChunkReloadHandler } from './utils/chunkReload'
import { ListeningQuerySync } from './hooks/useListening'
import { readBootstrap, seedBootstrap } from './publicBootstrap'
import { preloadPublicSite } from './publicEntry'

const queryClient = createQueryClient()

installChunkReloadHandler()

// Spec #105 — a signed-out public page arrives with its content written by
// the server. Seed the cache from its data block and load the page's chunks
// before the first render: createRoot then swaps the server HTML for the
// same page in one commit, with no blank frame and no repeat API calls.
const bootstrap = readBootstrap()

async function start() {
  if (bootstrap) {
    seedBootstrap(queryClient, bootstrap)
    try {
      await preloadPublicSite(bootstrap.page)
    } catch {
      // A chunk failed to load: render anyway; React.lazy retries it.
    }
  }
  render()
}

function render() {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <ErrorBoundary>
        <QueryClientProvider client={queryClient}>
          {/* Spec #90 — listening writes refresh the queries they invalidate. */}
          <ListeningQuerySync />
          <BrowserRouter>
            <AuthProvider initialViewer={bootstrap?.viewer}>
              <ToastProvider>
                <App />
              </ToastProvider>
            </AuthProvider>
          </BrowserRouter>
        </QueryClientProvider>
      </ErrorBoundary>
    </StrictMode>,
  )
}

void start()
