import { useState, useEffect, useRef } from 'react'
import { useAddPodcast, useAddPodcastStatus } from '../hooks/useApi'

interface AddPodcastModalProps {
  isOpen: boolean
  onClose: () => void
}

export default function AddPodcastModal({ isOpen, onClose }: AddPodcastModalProps) {
  const [url, setUrl] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const { mutate: startAdd, isPending } = useAddPodcast()
  const { data: status } = useAddPodcastStatus(isOpen)

  const isRunning = status?.status === 'running'
  const isCompleted = status?.status === 'completed'
  const isFailed = status?.status === 'failed'
  const isDisabled = isPending || isRunning

  // Focus input when modal opens
  useEffect(() => {
    if (isOpen && inputRef.current) {
      inputRef.current.focus()
    }
  }, [isOpen])

  // Reset form when modal closes
  useEffect(() => {
    if (!isOpen) {
      setUrl('')
    }
  }, [isOpen])

  // Close modal on successful completion
  useEffect(() => {
    if (isCompleted && status?.result) {
      // Auto-close after showing success message
      const timer = setTimeout(() => {
        onClose()
      }, 2000)
      return () => clearTimeout(timer)
    }
  }, [isCompleted, status?.result, onClose])

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (url.trim() && !isDisabled) {
      startAdd({ url: url.trim() })
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      onClose()
    }
  }

  if (!isOpen) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50"
      onClick={onClose}
      onKeyDown={handleKeyDown}
    >
      <div
        className="bg-white rounded-xl shadow-xl max-w-lg w-full p-6"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-xl font-semibold text-gray-900">Add Podcast</h2>
          <button
            onClick={onClose}
            className="p-1 text-gray-400 hover:text-gray-600 transition-colors"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit}>
          <div className="mb-4">
            <label htmlFor="podcast-url" className="block text-sm font-medium text-gray-700 mb-2">
              Podcast URL
            </label>
            <input
              ref={inputRef}
              id="podcast-url"
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://example.com/feed.xml"
              disabled={isDisabled}
              className={`
                w-full px-4 py-3 border rounded-lg text-gray-900 placeholder-gray-400
                focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500
                transition-colors
                ${isDisabled ? 'bg-gray-100 cursor-not-allowed' : 'bg-white'}
              `}
            />
            <p className="mt-2 text-sm text-gray-500">
              Supports RSS feeds, Apple Podcasts, and YouTube channels/playlists
            </p>
          </div>

          {/* Status indicators */}
          {isRunning && status && (
            <div className="mb-4 p-3 bg-indigo-50 rounded-lg">
              <div className="flex items-center gap-3">
                <svg className="w-5 h-5 text-indigo-600 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle
                    className="opacity-25"
                    cx="12"
                    cy="12"
                    r="10"
                    stroke="currentColor"
                    strokeWidth="4"
                  />
                  <path
                    className="opacity-75"
                    fill="currentColor"
                    d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                  />
                </svg>
                <div className="flex-1">
                  <p className="text-sm font-medium text-indigo-700">{status.message}</p>
                  <div className="mt-2 w-full h-2 bg-indigo-200 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-indigo-600 transition-all duration-300"
                      style={{ width: `${status.progress}%` }}
                    />
                  </div>
                </div>
              </div>
            </div>
          )}

          {isCompleted && status?.result && (
            <div className="mb-4 p-3 bg-green-50 rounded-lg">
              <div className="flex items-center gap-3">
                <svg className="w-5 h-5 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
                <div>
                  <p className="text-sm font-medium text-green-700">
                    Added: {status.result.podcast_title}
                  </p>
                  <p className="text-xs text-green-600">
                    {status.result.episodes_count} episode{status.result.episodes_count !== 1 ? 's' : ''} discovered
                  </p>
                </div>
              </div>
            </div>
          )}

          {isFailed && status?.error && (
            <div className="mb-4 p-3 bg-red-50 rounded-lg">
              <div className="flex items-center gap-3">
                <svg className="w-5 h-5 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
                <p className="text-sm text-red-700">{status.error}</p>
              </div>
            </div>
          )}

          {/* Actions */}
          <div className="flex items-center justify-end gap-3 mt-6">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-sm font-medium text-gray-700 hover:text-gray-900 transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isDisabled || !url.trim()}
              className={`
                inline-flex items-center gap-2 px-4 py-2 rounded-lg font-medium text-sm
                transition-all duration-200
                ${isDisabled || !url.trim()
                  ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
                  : 'bg-indigo-600 text-white hover:bg-indigo-700 active:bg-indigo-800 shadow-sm hover:shadow'
                }
              `}
            >
              {isRunning ? (
                <>
                  <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                    <circle
                      className="opacity-25"
                      cx="12"
                      cy="12"
                      r="10"
                      stroke="currentColor"
                      strokeWidth="4"
                    />
                    <path
                      className="opacity-75"
                      fill="currentColor"
                      d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                    />
                  </svg>
                  <span>Adding...</span>
                </>
              ) : (
                <>
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                  </svg>
                  <span>Add Podcast</span>
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
