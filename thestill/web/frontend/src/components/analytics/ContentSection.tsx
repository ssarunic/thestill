import { useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type { AnalyticsPodcastStat, AnalyticsReport } from '../../api/types'
import Panel from '../Panel'
import Bar from './Bar'
import PctCell from './PctCell'
import { SMALL_GROUP, isSmallGroup } from '../../utils/analyticsFormat'

/** Rows per ranked table; mirrors CONTENT_LIMIT in thestill/services/analytics_service.py. */
const TOP = 20

type PodcastSort = 'readers' | 'followers'

const SORTS: Record<PodcastSort, { label: string; keys: [PodcastSort, PodcastSort] }> = {
  readers: { label: 'Readers', keys: ['readers', 'followers'] },
  followers: { label: 'Followers', keys: ['followers', 'readers'] },
}

function compare(sort: PodcastSort) {
  const [first, second] = SORTS[sort].keys
  return (a: AnalyticsPodcastStat, b: AnalyticsPodcastStat) =>
    b[first] - a[first] || b[second] - a[second] || a.title.localeCompare(b.title)
}

const peak = (rows: { readers: number }[]) => Math.max(0, ...rows.map((r) => r.readers))

const NUM = 'py-2 pr-4 text-right tabular-nums text-ink'

function Th({ children, right = false }: { children: ReactNode; right?: boolean }) {
  return (
    <th scope="col" className={`py-2 pr-4 font-medium whitespace-nowrap ${right ? 'text-right' : ''}`}>
      {children}
    </th>
  )
}

/**
 * A row under SMALL_GROUP readers: its figures are greyed, never hidden
 * (spec #95 D1); the title stays at full contrast and says why.
 */
function Row({ readers, title, children }: { readers: number; title: ReactNode; children: ReactNode }) {
  const small = isSmallGroup(readers)
  return (
    <tr className={`border-t border-hairline align-top ${small ? '[&>td]:opacity-60' : ''}`} data-small={small || undefined}>
      <th scope="row" className="py-2 pr-4 text-left font-normal text-ink min-w-48">
        {title}
        {small && <span className="sr-only"> (fewer than {SMALL_GROUP} readers: read with care)</span>}
      </th>
      {children}
    </tr>
  )
}

function Readers({ readers, max }: { readers: number; max: number }) {
  return (
    <td className="py-2 pr-4 min-w-24">
      <span className="tabular-nums text-ink font-medium">{readers}</span>
      <div className="mt-1">
        <Bar value={max ? readers / max : 0} />
      </div>
    </td>
  )
}

function SortHeader({ sort, current, onSort }: { sort: PodcastSort; current: PodcastSort; onSort: (s: PodcastSort) => void }) {
  const active = sort === current
  return (
    <th scope="col" className="py-2 pr-4 font-medium whitespace-nowrap" aria-sort={active ? 'descending' : 'none'}>
      <button type="button" onClick={() => onSort(sort)} className={`font-medium hover:text-ink ${active ? 'text-ink' : ''}`}>
        {SORTS[sort].label}
        <span aria-hidden="true">{active ? ' ↓' : ''}</span>
      </button>
    </th>
  )
}

/** One titled table, or a line saying it is empty. */
function Group({
  id,
  title,
  caption,
  empty,
  head,
  children,
}: {
  id: string
  title: string
  caption: string
  empty: string | null
  head: ReactNode
  children: ReactNode
}) {
  return (
    <div className="space-y-2" role="group" aria-labelledby={id}>
      <h3 id={id} className="text-sm font-semibold text-ink">
        {title}
      </h3>
      {empty ? (
        <p className="text-sm text-muted">{empty}</p>
      ) : (
        <div className="overflow-x-auto -mx-4 px-4">
          <table className="w-full text-sm">
            <caption className="sr-only">{caption}</caption>
            <thead>
              <tr className="text-left text-muted">{head}</tr>
            </thead>
            <tbody>{children}</tbody>
          </table>
        </div>
      )}
    </div>
  )
}

/**
 * Spec #103 — which podcasts, episodes and categories readers read in the
 * window, against the previous window of the same length. Aggregates only:
 * no row leads to the people behind it.
 */
export default function ContentSection({ content }: { content: AnalyticsReport['content'] }) {
  const [sort, setSort] = useState<PodcastSort>('readers')
  // The server sends the union of both top lists, so either sort is an exact top N.
  const podcasts = [...content.podcasts].sort(compare(sort)).slice(0, TOP)
  const nothing = 'No reading or follows in this window.'

  return (
    <Panel as="section" aria-labelledby="analytics-content" className="p-4 space-y-5">
      <div>
        <h2 id="analytics-content" className="text-section text-ink">
          Content
        </h2>
        <p className="text-sm text-muted">
          Readers opened or listened to an episode in this window. Change is against the previous window of the same
          length. Public views are signed-out views of a show's public pages (its own and its episodes').
        </p>
      </div>

      <Group
        id="analytics-content-podcasts"
        title="Podcasts"
        caption={`Top podcasts by ${sort}`}
        empty={podcasts.length ? null : nothing}
        head={
          <>
            <Th>Podcast</Th>
            <SortHeader sort="readers" current={sort} onSort={setSort} />
            <Th right>Change</Th>
            <SortHeader sort="followers" current={sort} onSort={setSort} />
            <Th right>New follows</Th>
            <Th>Followers reading</Th>
            <Th right>Listeners</Th>
            <Th right>Completed</Th>
            <Th right>Public views</Th>
          </>
        }
      >
        {podcasts.map((p) => (
          <Row
            key={p.podcast_id}
            readers={p.readers}
            title={
              <>
                <Link to={`/podcasts/${p.slug || p.podcast_id}`} className="hover:underline">
                  {p.title}
                </Link>
                <div className="text-xs text-muted">{p.category ?? 'Uncategorised'}</div>
              </>
            }
          >
            <Readers readers={p.readers} max={peak(content.podcasts)} />
            <td className={`${NUM} text-muted`}>{p.change}</td>
            <td className={NUM}>{p.followers}</td>
            <td className={NUM}>{p.new_follows}</td>
            <td className="py-2 pr-4">
              <PctCell
                numerator={p.follower_readers}
                denominator={p.followers}
                pct={p.followers ? p.follower_readers / p.followers : null}
              />
            </td>
            <td className={NUM}>{p.listeners}</td>
            <td className={NUM}>{p.completions}</td>
            <td className={NUM}>{p.public_views}</td>
          </Row>
        ))}
      </Group>

      <Group
        id="analytics-content-episodes"
        title="Episodes"
        caption="Top episodes by readers"
        empty={content.episodes.length ? null : 'No episodes read in this window.'}
        head={
          <>
            <Th>Episode</Th>
            <Th>Readers</Th>
            <Th right>Listeners</Th>
            <Th right>Completed</Th>
            <Th right>Public views</Th>
          </>
        }
      >
        {content.episodes.map((e) => (
          <Row
            key={e.episode_id}
            readers={e.readers}
            title={
              <>
                <Link to={`/podcasts/${e.podcast_slug}/episodes/${e.slug || e.episode_id}`} className="hover:underline">
                  {e.title}
                </Link>
                <div className="text-xs text-muted">
                  {e.podcast_title}
                  {e.imported && <span className="italic"> · imported</span>}
                </div>
              </>
            }
          >
            <Readers readers={e.readers} max={peak(content.episodes)} />
            <td className={NUM}>{e.listeners}</td>
            <td className={NUM}>{e.completions}</td>
            <td className={NUM}>{e.public_views}</td>
          </Row>
        ))}
      </Group>

      <Group
        id="analytics-content-categories"
        title="Categories"
        caption="Categories by readers; each show counts under its primary category"
        empty={content.categories.length ? null : nothing}
        head={
          <>
            <Th>Category</Th>
            <Th>Readers</Th>
            <Th right>Change</Th>
            <Th right>Followers</Th>
            <Th right>Shows followed</Th>
            <Th right>Shows read</Th>
            <Th right>Public views</Th>
          </>
        }
      >
        {content.categories.map((c) => (
          <Row key={c.category} readers={c.readers} title={c.category}>
            <Readers readers={c.readers} max={peak(content.categories)} />
            <td className={`${NUM} text-muted`}>{c.change}</td>
            <td className={NUM}>{c.followers}</td>
            <td className={NUM}>{c.shows}</td>
            <td className={NUM}>{c.shows_read}</td>
            <td className={NUM}>{c.public_views}</td>
          </Row>
        ))}
      </Group>

      {content.notes.length > 0 && (
        <ul className="text-xs text-muted space-y-1 list-disc pl-4">
          {content.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
    </Panel>
  )
}
