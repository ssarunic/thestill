import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import SegmentedTranscriptViewer from './SegmentedTranscriptViewer'
import type { AnnotatedSegment, AnnotatedTranscriptDump, SegmentKind } from '../api/types'

function seg(overrides: Partial<AnnotatedSegment> & { id: number }): AnnotatedSegment {
  return {
    id: overrides.id,
    start: overrides.start ?? 0,
    end: overrides.end ?? 1,
    speaker: overrides.speaker ?? 'Host',
    text: overrides.text ?? 'content text',
    kind: (overrides.kind ?? 'content') as SegmentKind,
    sponsor: overrides.sponsor ?? null,
    source_segment_ids: overrides.source_segment_ids ?? [overrides.id],
    source_word_span: overrides.source_word_span ?? null,
    user_segment_id: overrides.user_segment_id ?? null,
    metadata: overrides.metadata ?? {},
  }
}

function makeTranscript(segments: AnnotatedSegment[]): AnnotatedTranscriptDump {
  return {
    episode_id: 'ep1',
    segments,
    playback_time_offset_seconds: 0,
    algorithm_version: 'v1',
  }
}

const STORAGE_KEY = 'thestill:transcriptViewer:hiddenKinds'

describe('SegmentedTranscriptViewer', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it('renders content segments by default', () => {
    render(
      <SegmentedTranscriptViewer
        transcript={makeTranscript([
          seg({ id: 0, text: 'first line', speaker: 'Alice' }),
          seg({ id: 1, text: 'second line', speaker: 'Bob' }),
        ])}
      />,
    )

    expect(screen.getByText('first line')).toBeInTheDocument()
    expect(screen.getByText('second line')).toBeInTheDocument()
  })

  it('never renders filler segments', () => {
    render(
      <SegmentedTranscriptViewer
        transcript={makeTranscript([
          seg({ id: 0, text: 'real content' }),
          seg({ id: 1, text: 'um', kind: 'filler' }),
        ])}
      />,
    )

    expect(screen.getByText('real content')).toBeInTheDocument()
    expect(screen.queryByText('um')).not.toBeInTheDocument()
  })

  it('shows ad break with full cleaned text by default', () => {
    render(
      <SegmentedTranscriptViewer
        transcript={makeTranscript([
          seg({
            id: 0,
            kind: 'ad_break',
            sponsor: 'Acme',
            text: 'Support for the show comes from Acme. Visit acme.com.',
          }),
        ])}
      />,
    )

    expect(screen.getByText(/Ad break/)).toBeInTheDocument()
    expect(screen.getAllByText(/Acme/).length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/acme\.com/)).toBeInTheDocument()
  })

  it('hides ads when the Ads toggle is clicked', async () => {
    const user = userEvent.setup()
    render(
      <SegmentedTranscriptViewer
        transcript={makeTranscript([
          seg({ id: 0, text: 'before ad', speaker: 'Alice' }),
          seg({ id: 1, kind: 'ad_break', sponsor: 'Acme', text: 'visit acme.com' }),
          seg({ id: 2, text: 'after ad', speaker: 'Alice' }),
        ])}
      />,
    )

    expect(screen.getByText(/visit acme\.com/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Ads' }))

    expect(screen.queryByText(/visit acme\.com/)).not.toBeInTheDocument()
    expect(screen.getByText('before ad')).toBeInTheDocument()
    expect(screen.getByText('after ad')).toBeInTheDocument()
  })

  it('only shows toggles for kinds actually present', () => {
    render(
      <SegmentedTranscriptViewer
        transcript={makeTranscript([
          seg({ id: 0, text: 'pure content' }),
          seg({ id: 1, kind: 'ad_break', text: 'ad text' }),
        ])}
      />,
    )

    expect(screen.getByRole('button', { name: 'Ads' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Music' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Intro' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Outro' })).not.toBeInTheDocument()
  })

  it('renders toggles for every present non-content kind', () => {
    render(
      <SegmentedTranscriptViewer
        transcript={makeTranscript([
          seg({ id: 0, kind: 'intro', text: 'welcome' }),
          seg({ id: 1, kind: 'music', text: 'theme plays' }),
          seg({ id: 2, text: 'main discussion' }),
          seg({ id: 3, kind: 'ad_break', text: 'ad copy', sponsor: 'Acme' }),
          seg({ id: 4, kind: 'outro', text: 'thanks for listening' }),
        ])}
      />,
    )

    expect(screen.getByRole('button', { name: 'Ads' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Music' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Intro' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Outro' })).toBeInTheDocument()
  })

  it('loads the hidden-kinds preference from localStorage', () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(['ad_break']))

    render(
      <SegmentedTranscriptViewer
        transcript={makeTranscript([
          seg({ id: 0, text: 'content' }),
          seg({ id: 1, kind: 'ad_break', text: 'hidden ad', sponsor: 'Acme' }),
        ])}
      />,
    )

    expect(screen.getByText('content')).toBeInTheDocument()
    expect(screen.queryByText('hidden ad')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Ads' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('persists the hidden-kinds preference to localStorage', async () => {
    const user = userEvent.setup()
    render(
      <SegmentedTranscriptViewer
        transcript={makeTranscript([
          seg({ id: 0, text: 'content' }),
          seg({ id: 1, kind: 'ad_break', text: 'ad copy' }),
        ])}
      />,
    )

    await user.click(screen.getByRole('button', { name: 'Ads' }))

    const stored = window.localStorage.getItem(STORAGE_KEY)
    expect(stored).not.toBeNull()
    expect(JSON.parse(stored!)).toEqual(['ad_break'])
  })

  it('falls back to defaults when localStorage has bogus values', () => {
    window.localStorage.setItem(STORAGE_KEY, '{"not": "an array"}')

    render(
      <SegmentedTranscriptViewer
        transcript={makeTranscript([
          seg({ id: 0, kind: 'ad_break', text: 'ad visible' }),
        ])}
      />,
    )

    expect(screen.getByText('ad visible')).toBeInTheDocument()
  })

  it('shows a placeholder when every visible kind is toggled off', async () => {
    const user = userEvent.setup()
    render(
      <SegmentedTranscriptViewer
        transcript={makeTranscript([
          seg({ id: 0, kind: 'ad_break', text: 'ad copy' }),
        ])}
      />,
    )

    await user.click(screen.getByRole('button', { name: 'Ads' }))

    expect(screen.getByText(/All segment kinds are hidden/i)).toBeInTheDocument()
  })

  it('renders music, intro and outro with their own block style', () => {
    render(
      <SegmentedTranscriptViewer
        transcript={makeTranscript([
          seg({ id: 0, kind: 'music', text: 'theme' }),
          seg({ id: 1, kind: 'intro', text: 'welcome folks' }),
          seg({ id: 2, kind: 'outro', text: 'goodbye' }),
        ])}
      />,
    )

    expect(screen.getByTestId('segment-music-0')).toBeInTheDocument()
    expect(screen.getByTestId('segment-intro-1')).toBeInTheDocument()
    expect(screen.getByTestId('segment-outro-2')).toBeInTheDocument()
  })

  it('handles an empty segment list with a helpful placeholder', () => {
    render(<SegmentedTranscriptViewer transcript={makeTranscript([])} />)

    expect(screen.getByText(/No segments available/i)).toBeInTheDocument()
  })
})
