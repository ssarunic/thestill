import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import DriftDebugStrip from './DriftDebugStrip'
import type { DriftReport } from './run'

const report: DriftReport = {
  outcome: 'aligned',
  generation: 'gen1',
  spans: [
    { srcStartS: 0, srcEndS: 2623, offsetS: 0 },
    { srcStartS: 2688.7, srcEndS: 3282, offsetS: 40.124 },
  ],
  gaps: [{ srcStartS: 2626.6, srcEndS: 2687, kind: 'confirmed' }],
  gapsConfirmedS: 60.4,
  unresolvedS: 5.3,
  listenerAdsS: 100.5,
  coverage: 0.998,
  anchorsTotal: 3372,
  anchorsHit: 3301,
  anchorsInlier: 3299,
  audioHost: 'stitcher2.acast.com',
  audioBytes: 54_000_000,
  fetchedBytes: 54_720_000,
  fetchMs: 2100,
  fitMs: 35,
  error: null,
}

describe('DriftDebugStrip', () => {
  it('says so when the transcript has no sync track', () => {
    render(<DriftDebugStrip state={{ status: 'idle', progress: null, report: null }} hasTrack={false} onPlayAt={vi.fn()} />)
    expect(screen.getByText(/No sync track for this transcript/)).toBeInTheDocument()
  })

  it('waits for playback, then shows progress', () => {
    const { rerender } = render(
      <DriftDebugStrip state={{ status: 'idle', progress: null, report: null }} hasTrack onPlayAt={vi.fn()} />,
    )
    expect(screen.getByText(/Waiting for playback/)).toBeInTheDocument()
    rerender(
      <DriftDebugStrip
        state={{ status: 'running', progress: { fetchedBytes: 10 * 1048576, totalBytes: 40 * 1048576 }, report: null }}
        hasTrack
        onPlayAt={vi.fn()}
      />,
    )
    expect(screen.getByText(/10\.0 MB of 40\.0 MB/)).toBeInTheDocument()
  })

  it('shows the map and that it is not applied, and plays a span start both ways', () => {
    const onPlayAt = vi.fn()
    render(<DriftDebugStrip state={{ status: 'done', progress: null, report }} hasTrack onPlayAt={onPlayAt} />)

    expect(screen.getByText(/Aligned/)).toBeInTheDocument()
    expect(screen.getByText(/Seeks and highlighting are not corrected yet/)).toBeInTheDocument()
    expect(screen.getByText('stitcher2.acast.com')).toBeInTheDocument()
    expect(screen.getByText('+40.124 s')).toBeInTheDocument()
    expect(screen.getByText(/3301 of 3372 \(2 outside a span\)/)).toBeInTheDocument()

    fireEvent.click(screen.getAllByRole('button', { name: 'Play span start, corrected' })[1])
    expect(onPlayAt).toHaveBeenLastCalledWith(2688.7 + 40.124)
    fireEvent.click(screen.getAllByRole('button', { name: 'Play uncorrected' })[1])
    expect(onPlayAt).toHaveBeenLastCalledWith(2688.7)
  })

  it('explains an outcome without a map', () => {
    render(
      <DriftDebugStrip
        state={{ status: 'done', progress: null, report: { ...report, outcome: 'no_cors', spans: [], gaps: [] } }}
        hasTrack
        onPlayAt={vi.fn()}
      />,
    )
    expect(screen.getByText(/does not allow the page to read the file/)).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })
})
