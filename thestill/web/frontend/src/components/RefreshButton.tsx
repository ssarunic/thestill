import { useEffect, useRef } from 'react'
import { useRefreshStatus, useStartRefresh } from '../hooks/useApi'
import { useToast } from './Toast'
import Button, { RefreshIcon } from './Button'

export default function RefreshButton() {
  const { data: status } = useRefreshStatus()
  const { mutate: startRefresh, isPending } = useStartRefresh()
  const { showToast } = useToast()
  const prevStatusRef = useRef<string | undefined>(undefined)

  const isRunning = status?.status === 'running'
  const isDisabled = isPending || isRunning

  // Show toast when status changes to completed or failed
  useEffect(() => {
    const prevStatus = prevStatusRef.current
    const currentStatus = status?.status

    // Only show toast on status transition (not on initial load)
    if (prevStatus && prevStatus !== currentStatus) {
      if (currentStatus === 'completed' && status?.result) {
        const count = status.result.total_episodes
        showToast(
          `Found ${count} new episode${count !== 1 ? 's' : ''}`,
          'success'
        )
      } else if (currentStatus === 'failed' && status?.error) {
        showToast(`Refresh failed: ${status.error}`, 'error')
      }
    }

    prevStatusRef.current = currentStatus
  }, [status, showToast])

  const handleClick = () => {
    if (!isDisabled) {
      startRefresh({})
    }
  }

  return (
    <Button
      onClick={handleClick}
      disabled={isDisabled}
      isLoading={isRunning}
      icon={<RefreshIcon />}
      iconOnlyMobile
      aria-label={isRunning ? 'Refreshing feeds' : 'Refresh feeds'}
    >
      {isRunning ? 'Refreshing...' : 'Refresh Feeds'}
    </Button>
  )
}
