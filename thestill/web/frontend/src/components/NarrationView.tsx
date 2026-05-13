import { useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { useNarrateDigest, useNarration } from '../hooks/useApi'
import type { NarrationMode, NarrationSummary } from '../api/types'

const PRESETS = [
  { slug: 'short', label: 'Short', minutes: 3 },
  { slug: 'medium', label: 'Medium', minutes: 5 },
  { slug: 'long', label: 'Long', minutes: 10 },
] as const

function formatRuntime(seconds: number | null | undefined): string {
  if (!seconds || seconds <= 0) return '–'
  const total = Math.round(seconds)
  if (total < 60) return `${total}s`
  const mins = Math.floor(total / 60)
  const rem = total % 60
  return rem === 0 ? `${mins}m` : `${mins}m ${rem.toString().padStart(2, '0')}s`
}

function formatGeneratedAt(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.valueOf())) return ''
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function pickInitialNarration(narrations: NarrationSummary[]): NarrationSummary | null {
  if (narrations.length === 0) return null
  // Prefer the medium preset; otherwise the most recently generated.
  const medium = narrations.find((n) => n.slug === 'medium')
  if (medium) return medium
  const sorted = [...narrations].sort((a, b) =>
    (b.generated_at ?? '').localeCompare(a.generated_at ?? ''),
  )
  return sorted[0] ?? null
}

interface FallbackBannerProps {
  reason: string | null
}

function FallbackBanner({ reason }: FallbackBannerProps) {
  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
      <p className="font-medium">Narration unavailable for this briefing</p>
      <p className="mt-1 text-amber-700">
        Showing the link-index briefing instead{reason ? ` (reason: ${reason})` : ''}.
        Try regenerating with a different length.
      </p>
    </div>
  )
}

interface LengthSwitcherProps {
  digestId: string
  narrations: NarrationSummary[]
  selectedSlug: string | null
  onSelect: (narration: NarrationSummary) => void
  disabled: boolean
}

function LengthSwitcher({
  digestId,
  narrations,
  selectedSlug,
  onSelect,
  disabled,
}: LengthSwitcherProps) {
  const narrate = useNarrateDigest()
  const slugMap = useMemo(
    () => new Map(narrations.map((n) => [n.slug, n])),
    [narrations],
  )
  const isPending = narrate.isPending
  const isBusy = disabled || isPending

  const handleClick = async (preset: (typeof PRESETS)[number]) => {
    const existing = slugMap.get(preset.slug)
    if (existing) {
      onSelect(existing)
      return
    }
    try {
      const result = await narrate.mutateAsync({
        digestId,
        request: { target_duration: preset.slug },
      })
      // The cache invalidation in the hook will refetch the digest,
      // which surfaces the new variant via ``onSelect`` on next render.
      // Optimistically select by id so the reader switches immediately.
      onSelect({
        narration_id: result.narration_id,
        slug: result.slug,
        target_duration_seconds: result.target_duration_seconds,
        actual_duration_seconds: result.actual_duration_seconds,
        mode: result.mode,
        fallback_reason: result.fallback_reason,
        generated_at: new Date().toISOString(),
        schema_version: null,
        script_path: result.script_path ?? '',
        markdown_path: result.markdown_path,
      })
    } catch {
      // Error surfaces via narrate.error below.
    }
  }

  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Narration length">
        {PRESETS.map((preset) => {
          const isSelected = selectedSlug === preset.slug
          const exists = slugMap.has(preset.slug)
          return (
            <button
              key={preset.slug}
              onClick={() => handleClick(preset)}
              disabled={isBusy}
              aria-pressed={isSelected}
              className={`px-3 py-1.5 text-sm font-medium rounded-full border transition-colors ${
                isSelected
                  ? 'border-primary-600 bg-primary-50 text-primary-700'
                  : exists
                  ? 'border-gray-300 bg-white text-gray-700 hover:bg-gray-50'
                  : 'border-dashed border-gray-300 bg-white text-gray-500 hover:bg-gray-50'
              } disabled:opacity-50 disabled:cursor-not-allowed`}
            >
              {preset.label} · {preset.minutes}m{exists ? '' : ' +'}
            </button>
          )
        })}
        {isPending && <span className="text-xs text-gray-500">Generating…</span>}
      </div>
      {narrate.error && (
        <p className="text-xs text-red-600">
          Couldn't generate: {(narrate.error as Error).message}
        </p>
      )}
    </div>
  )
}

interface NarrationViewProps {
  digestId: string
  narrations: NarrationSummary[]
  linkIndexFallback: React.ReactNode
}

export default function NarrationView({
  digestId,
  narrations,
  linkIndexFallback,
}: NarrationViewProps) {
  const [showLinkIndex, setShowLinkIndex] = useState(false)
  const [selectedSlug, setSelectedSlug] = useState<string | null>(
    () => pickInitialNarration(narrations)?.slug ?? null,
  )

  const selected = useMemo(() => {
    if (selectedSlug) {
      const match = narrations.find((n) => n.slug === selectedSlug)
      if (match) return match
    }
    return pickInitialNarration(narrations)
  }, [narrations, selectedSlug])

  const { data: narrationDetail, isLoading } = useNarration(
    selected?.narration_id ?? null,
  )

  if (narrations.length === 0) {
    // No narration yet — render the link-index alone. The length
    // switcher can still queue one up.
    return (
      <div className="space-y-4">
        <LengthSwitcher
          digestId={digestId}
          narrations={[]}
          selectedSlug={null}
          onSelect={(n) => setSelectedSlug(n.slug)}
          disabled={false}
        />
        {linkIndexFallback}
      </div>
    )
  }

  const handleSelect = (n: NarrationSummary) => {
    setSelectedSlug(n.slug)
    setShowLinkIndex(false)
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <LengthSwitcher
          digestId={digestId}
          narrations={narrations}
          selectedSlug={selected?.slug ?? null}
          onSelect={handleSelect}
          disabled={false}
        />
        <button
          onClick={() => setShowLinkIndex((v) => !v)}
          className="text-sm text-primary-600 hover:underline"
        >
          {showLinkIndex ? 'Show narrated' : 'Show link-index'}
        </button>
      </div>

      {selected && (
        <p className="text-xs text-gray-500">
          {selected.mode === 'fallback' ? (
            <span className="text-amber-700">Narration fell back to link-index</span>
          ) : (
            <>
              Generated {formatGeneratedAt(selected.generated_at)} ·{' '}
              {formatRuntime(selected.actual_duration_seconds)} of{' '}
              {formatRuntime(selected.target_duration_seconds)} target
            </>
          )}
        </p>
      )}

      {showLinkIndex ? (
        linkIndexFallback
      ) : selected?.mode === 'fallback' ? (
        <>
          <FallbackBanner reason={selected.fallback_reason} />
          {linkIndexFallback}
        </>
      ) : isLoading ? (
        <div className="animate-pulse space-y-3">
          <div className="h-6 bg-gray-200 rounded w-1/3" />
          <div className="h-4 bg-gray-200 rounded w-full" />
          <div className="h-4 bg-gray-200 rounded w-5/6" />
          <div className="h-4 bg-gray-200 rounded w-4/5" />
        </div>
      ) : narrationDetail?.markdown ? (
        <article className="prose prose-gray max-w-none prose-blockquote:border-l-primary-500 prose-blockquote:bg-primary-50/40 prose-blockquote:py-1 prose-a:text-primary-600 prose-a:no-underline hover:prose-a:underline">
          <ReactMarkdown>{narrationDetail.markdown}</ReactMarkdown>
        </article>
      ) : (
        linkIndexFallback
      )}
    </div>
  )
}

export type { NarrationMode }
