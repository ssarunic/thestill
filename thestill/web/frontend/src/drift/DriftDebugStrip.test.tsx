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
  blocks: null,
}

describe('DriftDebugStrip', () => {
  it('says so when the transcript has no sync track', () => {
    render(<DriftDebugStrip state={{ status: 'idle', progress: null, report: null, check: null }} hasTrack={false} onPlayAt={vi.fn()} />)
    expect(screen.getByText(/No sync track for this transcript/)).toBeInTheDocument()
  })

  it('waits for playback, then shows progress', () => {
    const { rerender } = render(
      <DriftDebugStrip state={{ status: 'idle', progress: null, report: null, check: null }} hasTrack onPlayAt={vi.fn()} />,
    )
    expect(screen.getByText(/Waiting for playback/)).toBeInTheDocument()
    rerender(
      <DriftDebugStrip
        state={{ status: 'running', progress: { fetchedBytes: 10 * 1048576, totalBytes: 40 * 1048576 }, report: null, check: null }}
        hasTrack
        onPlayAt={vi.fn()}
      />,
    )
    expect(screen.getByText(/10\.0 MB of 40\.0 MB/)).toBeInTheDocument()
  })

  it('shows the map and that it is not applied, and plays a span start both ways', () => {
    const onPlayAt = vi.fn()
    render(<DriftDebugStrip state={{ status: 'done', progress: null, report, check: null }} hasTrack onPlayAt={onPlayAt} />)

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
        state={{ status: 'done', progress: null, report: { ...report, outcome: 'no_cors', spans: [], gaps: [] }, check: null }}
        hasTrack
        onPlayAt={vi.fn()}
      />,
    )
    expect(screen.getByText(/does not allow the page to read the file/)).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('says whether the playing audio was shown to be the aligned copy', () => {
    const show = (check: Parameters<typeof DriftDebugStrip>[0]['state']['check']) =>
      render(<DriftDebugStrip state={{ status: 'done', progress: null, report, check }} hasTrack onPlayAt={vi.fn()} />)

    const pending = show(null)
    expect(screen.getByText(/Check against the playing audio: pending/)).toBeInTheDocument()
    pending.unmount()

    const first = show({ swState: 'element_not_cors', verification: null })
    expect(screen.getByText(/not possible — the audio loaded without crossorigin/)).toBeInTheDocument()
    first.unmount()

    const ok = show({
      swState: 'ok',
      verification: { lengthOk: true, rangesChecked: 4, rangesMatched: 4, verdict: 'verified' },
    })
    expect(screen.getByText(/the element is playing the aligned copy \(same length, 4 of 4 ranges match\)/)).toBeInTheDocument()
    ok.unmount()

    show({ swState: 'ok', verification: { lengthOk: false, rangesChecked: 2, rangesMatched: 1, verdict: 'mismatch' } })
    expect(screen.getByText(/playing a DIFFERENT copy; this map does not apply/)).toBeInTheDocument()
  })
})
