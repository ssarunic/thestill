import type { ReactElement } from 'react'
import type { PipelineStage } from '../api/types'
import { useEpisodeTasks } from '../hooks/useApi'

// Spec #28 §"Failure isolation rule" + Phase 5.2 — the four entity-
// branch stages get their own status row, separate from the user
// pipeline. A failure here does NOT mark the episode failed; it just
// means the search index is incomplete. The visual language matches
// PipelineStepper but with a different palette (indigo for active,
// amber for failure) so the reader clocks "this is the indexing
// chain, not the processing chain" at a glance.

const ENTITY_STAGES: { key: PipelineStage; label: string; tooltip: string; icon: ReactElement }[] = [
  {
    key: 'extract-entities',
    label: 'Extracting',
    tooltip: 'Finding people, companies, products, topics',
    icon: (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-4.35-4.35M10.5 18a7.5 7.5 0 100-15 7.5 7.5 0 000 15z" />
      </svg>
    ),
  },
  {
    key: 'resolve-entities',
    label: 'Resolving',
    tooltip: 'Linking surface forms to Wikidata entities',
    icon: (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
      </svg>
    ),
  },
  {
    key: 'reindex',
    label: 'Indexing',
    tooltip: 'Building the search index',
    icon: (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19.428 15.428a2 2 0 00-1.022-.547l-2.387-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z" />
      </svg>
    ),
  },
]

const ENTITY_STAGE_KEYS = new Set<string>(ENTITY_STAGES.map((s) => s.key))

type StageStatus = 'completed' | 'processing' | 'pending' | 'failed' | 'queued'

interface EntityBranchProgressProps {
  episodeId: string | null
  // When set, the strip collapses to a tiny "Indexed" pill once every
  // stage is complete. Defaults to true — this matches the spec's
  // "minimise visual weight when nothing's wrong" intent.
  collapseWhenIdle?: boolean
}

function CheckIcon() {
  return (
    <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
      <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
    </svg>
  )
}

function SpinnerIcon() {
  return (
    <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
    </svg>
  )
}

function QueueIcon() {
  return (
    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  )
}

function WarnIcon() {
  return (
    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
    </svg>
  )
}

export default function EntityBranchProgress({
  episodeId,
  collapseWhenIdle = true,
}: EntityBranchProgressProps) {
  const { data: tasksData } = useEpisodeTasks(episodeId)

  if (!episodeId) return null

  // Build a per-stage status map from the most-recent task at each
  // entity-branch stage. The queue can have multiple historical tasks
  // (retries, manual re-runs) — we care about the latest one for each
  // stage to drive the dot color.
  const tasks = tasksData?.tasks ?? []
  const latestByStage = new Map<string, { status: string; failedAt?: string | null }>()
  for (const t of tasks) {
    if (!ENTITY_STAGE_KEYS.has(t.stage)) continue
    const existing = latestByStage.get(t.stage)
    // ``created_at`` ordering is good enough — the queue assigns
    // monotonically increasing UUIDs but not all stages do; sort by
    // the timestamp the row carries.
    if (!existing || (t.created_at ?? '') > (existing as any).created_at) {
      latestByStage.set(t.stage, t as any)
    }
  }

  // No entity-branch tasks at all — episode hasn't reached the entity
  // chain yet. Show nothing rather than a row of empty dots.
  if (latestByStage.size === 0) {
    return null
  }

  const stageStatuses: { key: PipelineStage; label: string; tooltip: string; icon: ReactElement; status: StageStatus }[] = ENTITY_STAGES.map((stage) => {
    const t = latestByStage.get(stage.key) as any
    let status: StageStatus = 'pending'
    if (t) {
      switch (t.status) {
        case 'completed':
          status = 'completed'
          break
        case 'processing':
          status = 'processing'
          break
        case 'failed':
        case 'dead':
          status = 'failed'
          break
        case 'pending':
        case 'retry':
        case 'retry_scheduled':
          status = 'queued'
          break
        default:
          status = 'pending'
      }
    }
    return { ...stage, status }
  })

  const allComplete = stageStatuses.every((s) => s.status === 'completed')
  const anyFailed = stageStatuses.some((s) => s.status === 'failed')
  const anyActive = stageStatuses.some((s) => s.status === 'processing' || s.status === 'queued')

  // Collapsed "Indexed" pill — when every stage is green and the
  // caller asked for the compact form. One subtle row in the page
  // chrome instead of four big circles.
  if (allComplete && collapseWhenIdle && !anyActive && !anyFailed) {
    return (
      <div
        className="inline-flex items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-0.5 text-xs font-medium text-emerald-700"
        data-testid="entity-branch-indexed-pill"
      >
        <CheckIcon />
        <span>Indexed</span>
      </div>
    )
  }

  return (
    <div
      className="rounded-lg border border-gray-200 bg-gray-50/40 px-3 py-2"
      data-testid="entity-branch-progress"
    >
      <div className="mb-1 flex items-center gap-2 text-[10px] uppercase tracking-wide text-gray-500">
        <span>Search indexing</span>
        {anyFailed && (
          <span className="inline-flex items-center gap-1 rounded-full border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-700">
            <WarnIcon />
            Indexing incomplete
          </span>
        )}
        {!anyFailed && allComplete && (
          <span className="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700">
            <CheckIcon />
            Done
          </span>
        )}
      </div>
      <div className="flex items-center">
        {stageStatuses.map((stage, idx) => {
          const isLast = idx === stageStatuses.length - 1
          const colorByStatus: Record<StageStatus, string> = {
            completed: 'bg-emerald-500 text-white',
            processing: 'bg-indigo-500 text-white ring-4 ring-indigo-200',
            queued: 'bg-gray-400 text-white ring-4 ring-gray-200',
            failed: 'bg-amber-500 text-white',
            pending: 'bg-gray-200 text-gray-400',
          }
          const labelColor: Record<StageStatus, string> = {
            completed: 'text-emerald-600',
            processing: 'text-indigo-600',
            queued: 'text-gray-500',
            failed: 'text-amber-700',
            pending: 'text-gray-400',
          }
          const connectorColor: Record<StageStatus, string> = {
            completed: 'bg-emerald-400',
            processing: 'bg-gradient-to-r from-indigo-400 to-gray-200',
            queued: 'bg-gradient-to-r from-gray-400 to-gray-200',
            failed: 'bg-amber-400',
            pending: 'bg-gray-200',
          }
          const titleSuffix =
            stage.status === 'completed' ? ' (completed)'
            : stage.status === 'processing' ? ' (processing)'
            : stage.status === 'queued' ? ' (queued)'
            : stage.status === 'failed' ? ' (failed)'
            : ''
          return (
            <div key={stage.key} className="flex items-center">
              <div className="flex flex-col items-center">
                <div
                  className={`w-7 h-7 rounded-full flex items-center justify-center transition-all duration-300 ${colorByStatus[stage.status]}`}
                  title={`${stage.label}: ${stage.tooltip}${titleSuffix}`}
                >
                  {stage.status === 'completed' ? (
                    <CheckIcon />
                  ) : stage.status === 'processing' ? (
                    <SpinnerIcon />
                  ) : stage.status === 'queued' ? (
                    <QueueIcon />
                  ) : stage.status === 'failed' ? (
                    <WarnIcon />
                  ) : (
                    stage.icon
                  )}
                </div>
                <span className={`text-[10px] mt-1 font-medium whitespace-nowrap ${labelColor[stage.status]}`}>
                  {stage.label}
                </span>
              </div>
              {!isLast && (
                <div
                  className={`w-6 h-1 mx-1 rounded transition-all duration-300 ${connectorColor[stage.status]}`}
                  style={{ marginTop: '-1.25rem' }}
                />
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
