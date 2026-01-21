import { useState, useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useAddPodcast, useAddPodcastStatus } from '../hooks/useApi'
import { useToast } from './Toast'
import Button, { PlusIcon, CloseIcon } from './Button'

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
  // Track the timestamp when we started our task (to ignore stale results)
  const taskStartTimeRef = useRef<number | null>(null)
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

    // Ignore stale results from before we started our task
    if (status.started_at && taskStartTimeRef.current) {
      const statusStartTime = new Date(status.started_at).getTime()
      if (statusStartTime < taskStartTimeRef.current) {
        // This is a result from a previous task, ignore it
        return
      }
    }

    // Skip if we've already seen this status (include podcast title for uniqueness)
    const resultTitle = status.result?.podcast_title || ''
    const statusKey = `${status.status}-${status.started_at}-${resultTitle}`
    if (statusKey === lastSeenStatusRef.current) return

    if (status.status === 'completed' && status.result) {
      lastSeenStatusRef.current = statusKey
      const resultData = status.result

      // Refetch podcasts list first, then show toast after list is updated
      Promise.all([
        queryClient.refetchQueries({ queryKey: ['podcasts'] }),
        queryClient.refetchQueries({ queryKey: ['dashboard'] }),
      ]).then(() => {
        // Dismiss the "Following..." toast before showing success
        if (pendingToastIdRef.current) {
          dismissToast(pendingToastIdRef.current)
          pendingToastIdRef.current = null
        }
        showToast(
          `Following: ${resultData.podcast_title} (${resultData.episodes_count} episodes)`,
          'success'
        )
      })
      setWaitingForResult(false)
    } else if (status.status === 'failed' && status.error) {
      lastSeenStatusRef.current = statusKey
      // Dismiss the "Following..." toast before showing error
      if (pendingToastIdRef.current) {
        dismissToast(pendingToastIdRef.current)
        pendingToastIdRef.current = null
      }
      showToast(`Failed to follow podcast: ${status.error}`, 'error')
      setWaitingForResult(false)
    }
  }, [status, waitingForResult, showToast, dismissToast, queryClient])

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (url.trim() && !isPending) {
      // Clear cached status so we don't show old results
      queryClient.removeQueries({ queryKey: ['commands', 'add', 'status'] })
      lastSeenStatusRef.current = null
      // Record when we started this task (to ignore stale results)
      taskStartTimeRef.current = Date.now()
      // Show "Following..." toast and save ID so we can dismiss it later
      pendingToastIdRef.current = showToast('Following podcast...', 'info')
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
          <h2 className="text-xl font-semibold text-gray-900">Follow Podcast</h2>
          <Button
            variant="ghost"
            size="sm"
            icon={<CloseIcon />}
            onClick={onClose}
            aria-label="Close"
          />
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
            <Button
              type="button"
              variant="ghost"
              onClick={onClose}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={isPending || !url.trim()}
              icon={<PlusIcon />}
            >
              Follow
            </Button>
          </div>
        </form>
      </div>
    </div>
  )
}
