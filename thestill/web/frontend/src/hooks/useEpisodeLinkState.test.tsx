// Spec #52 overlay contract for player-surface links (spec #71 bar, #72 sheet).
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, type Location } from 'react-router-dom'
import { useEpisodeLinkState } from './useEpisodeLinkState'

const episodePath = '/podcasts/pod/episodes/ep-1'

function Probe() {
  const { state, alreadyHere } = useEpisodeLinkState(episodePath)
  return (
    <div data-testid="out">
      {state ? state.backgroundLocation.pathname : 'none'}|{alreadyHere ? 'here' : 'elsewhere'}
    </div>
  )
}

function renderAt(entry: string | Partial<Location>) {
  render(
    <MemoryRouter initialEntries={[entry as string]}>
      <Probe />
    </MemoryRouter>,
  )
  return screen.getByTestId('out')
}

describe('useEpisodeLinkState', () => {
  it('carries the inbox as background location from /inbox', () => {
    expect(renderAt('/inbox')).toHaveTextContent('/inbox|elsewhere')
  })

  it('forwards an existing background location from inside an overlay', () => {
    const out = renderAt({
      pathname: episodePath,
      search: '',
      hash: '',
      key: 'k',
      state: { backgroundLocation: { pathname: '/inbox', search: '?f=1', hash: '', key: 'b', state: null } },
    })
    expect(out).toHaveTextContent('/inbox|here')
  })

  it('is a plain navigation from any other page', () => {
    expect(renderAt('/podcasts')).toHaveTextContent('none|elsewhere')
  })
})
