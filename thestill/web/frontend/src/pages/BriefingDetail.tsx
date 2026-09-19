import { useParams } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import NarrationView from '../components/NarrationView'
import {
  useBriefing,
  useBriefingEpisodes,
  useBriefingScript,
  useMarkBriefingListened,
} from '../hooks/useApi'
import Button from '../components/Button'
import PageHero from '../components/PageHero'
import ActionRow from '../components/ActionRow'
import MetaEyebrow from '../components/MetaEyebrow'
import Panel from '../components/Panel'
import BriefingCover from '../components/BriefingCover'
import BriefingIndex, { BriefingIndexSkeleton } from '../components/BriefingIndex'
import { artworkFrameClass } from '../components/artworkRoles'
import { describeShows } from '../utils/briefingFormat'

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

export default function BriefingDetail() {
  const { briefingId } = useParams<{ briefingId: string }>()
  const briefingQuery = useBriefing(briefingId ?? null)
  const episodesQuery = useBriefingEpisodes(briefingId ?? null)
  const markListened = useMarkBriefingListened()

  const podcasts = episodesQuery.data?.podcasts ?? []
  const hasIndex = podcasts.length > 0
  // The text-only script is the fallback: briefings whose episodes have
  // since been deleted, or an index request that failed. Only fetched then.
  const needsScript = episodesQuery.isError || (episodesQuery.isSuccess && !hasIndex)
  const scriptQuery = useBriefingScript(needsScript ? (briefingId ?? null) : null)

  if (briefingQuery.isLoading) {
    return (
      <div className="space-y-4">
        <div className="animate-pulse h-4 w-72 bg-gray-100 rounded" />
        <div className="animate-pulse h-8 w-48 bg-gray-100 rounded" />
        <div className="animate-pulse h-12 w-36 bg-gray-100 rounded-full" />
        <div className="animate-pulse h-64 bg-surface border border-hairline rounded-lg" />
      </div>
    )
  }

  if (briefingQuery.error || !briefingQuery.data) {
    return (
      <div className="text-center py-12">
        <div className="bg-red-50 border border-red-200 rounded-lg p-6 max-w-md mx-auto">
          <h2 className="text-red-700 font-medium mb-2">Briefing not found</h2>
          <p className="text-red-600 text-sm">
            {briefingQuery.error?.message ?? 'No briefing matches that id.'}
          </p>
        </div>
      </div>
    )
  }

  const briefing = briefingQuery.data
  const isListened = !!briefing.listened_at
  const showsLine = describeShows(podcasts)

  return (
    <div className="space-y-6 max-w-3xl">
      {/* Spec #76 §5 — same hero anatomy as the episode page. The artwork
          slot holds the covered shows' artwork as one cover tile; the one
          action sits under the title instead of below the script. */}
      <PageHero
        artwork={
          hasIndex ? (
            <BriefingCover podcasts={podcasts} />
          ) : episodesQuery.isLoading ? (
            <div aria-hidden="true" className={`${artworkFrameClass('collage')} animate-pulse bg-gray-200`} />
          ) : undefined
        }
        backdropSources={hasIndex ? [podcasts[0].image_url] : undefined}
        eyebrow={
          <MetaEyebrow
            items={[
              `${briefing.episode_count} episode${briefing.episode_count === 1 ? '' : 's'}`,
              `Generated ${formatDateTime(briefing.created_at)}`,
              isListened ? `Listened ${formatDateTime(briefing.listened_at!)}` : null,
            ]}
          />
        }
        title="Today's briefing"
        identity={showsLine ? <p className="text-sm text-muted">{showsLine}</p> : undefined}
      >
        <ActionRow
          primary={
            <Button
              type="button"
              size="lg"
              pill
              onClick={() => markListened.mutate(briefing.id)}
              disabled={isListened || markListened.isPending}
              isLoading={markListened.isPending}
            >
              {isListened ? 'Marked listened' : markListened.isPending ? 'Saving…' : 'Mark listened'}
            </Button>
          }
        />
      </PageHero>

      <Panel className="p-4 sm:p-6">
        <NarrationView
          briefingId={briefing.id}
          narrations={briefing.narrations ?? []}
          linkIndexFallback={
            <>
              {episodesQuery.isLoading && <BriefingIndexSkeleton />}
              {hasIndex && <BriefingIndex podcasts={podcasts} />}
              {needsScript && scriptQuery.isLoading && (
                <div className="space-y-2">
                  <div className="animate-pulse h-4 w-3/4 bg-gray-100 rounded" />
                  <div className="animate-pulse h-4 w-2/3 bg-gray-100 rounded" />
                  <div className="animate-pulse h-4 w-5/6 bg-gray-100 rounded" />
                </div>
              )}
              {needsScript && scriptQuery.error && (
                <p className="text-muted italic">
                  Briefing script not available yet.
                </p>
              )}
              {needsScript && scriptQuery.data && (
                <div className="prose prose-sm max-w-none">
                  <ReactMarkdown>{scriptQuery.data.markdown}</ReactMarkdown>
                </div>
              )}
            </>
          }
        />
      </Panel>
    </div>
  )
}
