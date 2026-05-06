import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import EntityBranchProgress from './EntityBranchProgress'

// Mock the useEpisodeTasks hook so we can drive the four-stage status
// row from fixture data instead of hitting the network.
vi.mock('../hooks/useApi', () => ({
  useEpisodeTasks: vi.fn(),
}))

import { useEpisodeTasks } from '../hooks/useApi'

const mockedUseEpisodeTasks = vi.mocked(useEpisodeTasks)

function task(stage: string, status: string, createdAt = '2026-05-05T20:00:00Z') {
  return {
    id: `${stage}-1`,
    stage,
    status,
    created_at: createdAt,
    completed_at: status === 'completed' ? '2026-05-05T20:01:00Z' : null,
    metadata: null,
  }
}

function withTasks(tasks: Array<ReturnType<typeof task>>) {
  // Cast to whatever the real return type is — we only consume `data.tasks`
  // inside the component.
  mockedUseEpisodeTasks.mockReturnValue({ data: { tasks } } as any)
}

describe('EntityBranchProgress', () => {
  beforeEach(() => {
    mockedUseEpisodeTasks.mockReset()
  })

  it('renders nothing when no entity-branch tasks exist for the episode', () => {
    withTasks([
      task('clean', 'completed'),
      task('summarize', 'completed'),
    ])
    const { container } = render(<EntityBranchProgress episodeId="ep1" />)
    expect(container.firstChild).toBeNull()
  })

  it('renders nothing when episodeId is null', () => {
    withTasks([])
    const { container } = render(<EntityBranchProgress episodeId={null} />)
    expect(container.firstChild).toBeNull()
  })

  it('renders the four entity-branch stages with the section header', () => {
    withTasks([task('extract-entities', 'processing')])
    render(<EntityBranchProgress episodeId="ep1" />)
    expect(screen.getByText('Search indexing')).toBeInTheDocument()
    expect(screen.getByText('Extracting')).toBeInTheDocument()
    expect(screen.getByText('Resolving')).toBeInTheDocument()
    expect(screen.getByText('Indexing')).toBeInTheDocument()
  })

  it('shows the "Indexing incomplete" amber pill when any entity stage failed', () => {
    withTasks([
      task('extract-entities', 'completed'),
      task('resolve-entities', 'failed'),
    ])
    render(<EntityBranchProgress episodeId="ep1" />)
    expect(screen.getByText('Indexing incomplete')).toBeInTheDocument()
    // Episode is NOT marked as failed (spec failure-isolation rule);
    // we just surface a soft amber warning for the indexing chain.
    expect(screen.queryByText(/Failed/i)).toBeNull()
  })

  it('collapses to the compact "Indexed" pill when every stage is complete (default)', () => {
    withTasks([
      task('extract-entities', 'completed'),
      task('resolve-entities', 'completed'),
      task('reindex', 'completed'),
    ])
    render(<EntityBranchProgress episodeId="ep1" />)
    expect(screen.getByTestId('entity-branch-indexed-pill')).toBeInTheDocument()
    expect(screen.getByText('Indexed')).toBeInTheDocument()
    // The full strip should NOT render in collapsed mode.
    expect(screen.queryByTestId('entity-branch-progress')).toBeNull()
  })

  it('keeps the full strip visible when collapseWhenIdle is false', () => {
    withTasks([
      task('extract-entities', 'completed'),
      task('resolve-entities', 'completed'),
      task('reindex', 'completed'),
    ])
    render(<EntityBranchProgress episodeId="ep1" collapseWhenIdle={false} />)
    expect(screen.getByTestId('entity-branch-progress')).toBeInTheDocument()
    expect(screen.queryByTestId('entity-branch-indexed-pill')).toBeNull()
  })

  it('uses the most-recent task per stage when multiple exist (retry history)', () => {
    withTasks([
      task('extract-entities', 'failed', '2026-05-05T19:00:00Z'),  // older
      task('extract-entities', 'completed', '2026-05-05T20:00:00Z'),  // newer
    ])
    render(<EntityBranchProgress episodeId="ep1" />)
    // The Extracting stage should NOT render as failed because the
    // newer task succeeded. No "Indexing incomplete" pill.
    expect(screen.queryByText('Indexing incomplete')).toBeNull()
  })
})
