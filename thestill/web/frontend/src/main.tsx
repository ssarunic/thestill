import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
// Self-hosted so the font loads under our `style-src 'self'` CSP and
// without a third-party request; one variable file covers every weight.
import '@fontsource-variable/inter/wght.css'
import './index.css'
import App from './App'
import { ToastProvider } from './components/Toast'
import { AuthProvider } from './contexts/AuthContext'
import { createQueryClient } from './queryClient'
import ErrorBoundary from './components/ErrorBoundary'
import { installChunkReloadHandler } from './utils/chunkReload'

const queryClient = createQueryClient()

installChunkReloadHandler()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <ToastProvider>
            <App />
          </ToastProvider>
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>,
)
