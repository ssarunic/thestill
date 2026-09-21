import { Component, type ErrorInfo, type ReactNode } from 'react'
import { isChunkLoadError, reloadForStaleChunk } from '../utils/chunkReload'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

// Without a boundary a render error unmounts the whole tree and leaves a
// blank screen. This keeps something on screen and offers a way out.
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    if (isChunkLoadError(error) && reloadForStaleChunk()) return
    console.error('Unhandled render error', error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children

    const stale = isChunkLoadError(this.state.error)
    return (
      <div role="alert" className="flex flex-col items-center justify-center min-h-screen gap-4 p-6 text-center">
        <h1 className="text-xl font-semibold text-gray-900">
          {stale ? 'Thestill has been updated' : 'Something went wrong'}
        </h1>
        <p className="text-gray-600 max-w-md">
          {stale
            ? 'This tab is running an older version. Reload to get the latest one.'
            : 'This page failed to display. Reloading usually fixes it.'}
        </p>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="px-4 py-2 rounded-lg bg-primary-600 text-white hover:bg-primary-700"
        >
          Reload
        </button>
      </div>
    )
  }
}
