import { useState, useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useAddPodcast, useAddPodcastStatus } from '../hooks/useApi'
import { useToast } from './Toast'

interface AddPodcastModalProps {
  isOpen: boolean
  onClose: () => void
}

export default function AddPodcastModal({ isOpen, onClose }: AddPodcastModalProps) {
  const [url, setUrl] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const queryClient = useQueryClient()
  const { mutate: startAdd, isPending } = useAddPodcast()
  const { showToast, dismissToast } = useToast()

  // Track if we're waiting for a result from a task we started
  const [waitingForResult, setWaitingForResult] = useState(false)
  // Track the last status we've seen to avoid duplicate toasts
  const lastSeenStatusRef = useRef<string | null>(null)
  // Track the "Adding..." toast ID so we can dismiss it
  const pendingToastIdRef = useRef<number | null>(null)
  const { data: status } = useAddPodcastStatus(waitingForResult)

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

  // Show toast notifications only for tasks we started (when waitingForResult is true)
  // Only show if status changed from what we last saw (avoid duplicate toasts)
  useEffect(() => {
    if (!waitingForResult || !status) return

    // Skip if we've already seen this status
    const statusKey = `${status.status}-${status.started_at}`
    if (statusKey === lastSeenStatusRef.current) return

    if (status.status === 'completed' && status.result) {
      lastSeenStatusRef.current = statusKey
      const resultData = status.result

      // Refetch podcasts list first, then show toast after list is updated
      Promise.all([
        queryClient.refetchQueries({ queryKey: ['podcasts'] }),
        queryClient.refetchQueries({ queryKey: ['dashboard'] }),
      ]).then(() => {
        // Dismiss the "Adding..." toast before showing success
        if (pendingToastIdRef.current) {
          dismissToast(pendingToastIdRef.current)
          pendingToastIdRef.current = null
        }
        showToast(
          `Added: ${resultData.podcast_title} (${resultData.episodes_count} episodes)`,
          'success'
        )
      })
      setWaitingForResult(false)
    } else if (status.status === 'failed' && status.error) {
      lastSeenStatusRef.current = statusKey
      // Dismiss the "Adding..." toast before showing error
      if (pendingToastIdRef.current) {
        dismissToast(pendingToastIdRef.current)
        pendingToastIdRef.current = null
      }
      showToast(`Failed to add podcast: ${status.error}`, 'error')
      setWaitingForResult(false)
    }
  }, [status, waitingForResult, showToast, dismissToast, queryClient])

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (url.trim() && !isPending) {
      // Clear cached status so we don't show old results
      queryClient.removeQueries({ queryKey: ['commands', 'add', 'status'] })
      lastSeenStatusRef.current = null
      // Show "Adding..." toast and save ID so we can dismiss it later
      pendingToastIdRef.current = showToast('Adding podcast...', 'info')
      setWaitingForResult(true)
      startAdd({ url: url.trim() })
      onClose()
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
              disabled={isPending}
              className={`
                w-full px-4 py-3 border rounded-lg text-gray-900 placeholder-gray-400
                focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500
                transition-colors
                ${isPending ? 'bg-gray-100 cursor-not-allowed' : 'bg-white'}
              `}
            />
            <p className="mt-2 text-sm text-gray-500">
              Supports RSS feeds, Apple Podcasts, and YouTube channels/playlists
            </p>
          </div>

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
              disabled={isPending || !url.trim()}
              className={`
                inline-flex items-center gap-2 px-4 py-2 rounded-lg font-medium text-sm
                transition-all duration-200
                ${isPending || !url.trim()
                  ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
                  : 'bg-indigo-600 text-white hover:bg-indigo-700 active:bg-indigo-800 shadow-sm hover:shadow'
                }
              `}
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
              </svg>
              <span>Add Podcast</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
