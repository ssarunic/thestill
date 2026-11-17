import { useEffect } from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import { ToastProvider, useToast } from './Toast'

const captured: { show?: ReturnType<typeof useToast>['showToast'] } = {}
const show: ReturnType<typeof useToast>['showToast'] = (...args) => captured.show!(...args)

function Capture() {
  const { showToast } = useToast()
  useEffect(() => {
    captured.show = showToast
  }, [showToast])
  return null
}

afterEach(() => {
  vi.useRealTimers()
})

describe('Toast', () => {
  it('runs an action once and closes the toast', () => {
    const onClick = vi.fn()
    render(
      <ToastProvider>
        <Capture />
      </ToastProvider>,
    )
    act(() => {
      show('Following Sample Pod.', 'success', { action: { label: 'Undo', onClick } })
    })

    act(() => screen.getByRole('button', { name: 'Undo' }).click())

    expect(onClick).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('Following Sample Pod.')).toBeNull()
  })

  it('stays up for the requested duration instead of the default 5 s', () => {
    vi.useFakeTimers()
    render(
      <ToastProvider>
        <Capture />
      </ToastProvider>,
    )
    act(() => {
      show('Short', 'info')
      show('Long', 'success', { durationMs: 8000 })
    })

    act(() => vi.advanceTimersByTime(5000))
    expect(screen.queryByText('Short')).toBeNull()
    expect(screen.getByText('Long')).toBeInTheDocument()

    act(() => vi.advanceTimersByTime(3000))
    expect(screen.queryByText('Long')).toBeNull()
  })
})
