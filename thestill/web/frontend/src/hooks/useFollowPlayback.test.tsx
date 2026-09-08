import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { useFollowPlayback, __resetFollowPlaybackForTests, FOLLOW_PLAYBACK_STORAGE_KEY } from './useFollowPlayback'

function Consumer({ name }: { name: string }) {
  const [follow, setFollow] = useFollowPlayback()
  return (
    <button type="button" onClick={() => setFollow(!follow)} data-testid={name}>
      {follow ? 'on' : 'off'}
    </button>
  )
}

describe('useFollowPlayback (spec #72 §6)', () => {
  beforeEach(() => {
    localStorage.clear()
    __resetFollowPlaybackForTests()
  })

  it('reads the persisted value under the historical key', () => {
    localStorage.setItem(FOLLOW_PLAYBACK_STORAGE_KEY, 'true')
    render(<Consumer name="a" />)
    expect(screen.getByTestId('a')).toHaveTextContent('on')
  })

  it('keeps two mounted consumers in step and persists', () => {
    render(
      <>
        <Consumer name="sheet" />
        <Consumer name="viewer" />
      </>,
    )
    expect(screen.getByTestId('viewer')).toHaveTextContent('off')
    fireEvent.click(screen.getByTestId('sheet'))
    expect(screen.getByTestId('sheet')).toHaveTextContent('on')
    expect(screen.getByTestId('viewer')).toHaveTextContent('on')
    expect(localStorage.getItem(FOLLOW_PLAYBACK_STORAGE_KEY)).toBe('true')
    fireEvent.click(screen.getByTestId('viewer'))
    expect(screen.getByTestId('sheet')).toHaveTextContent('off')
  })
})
