// Spec #94 — "Thestill Editorial" components for the signed-out pages.
// A typed port of the design system's bundle (same class names, styles in
// src/styles/public.css). Everything here renders under .public-theme.
import { useState, type ReactNode } from 'react'
import { Link, type To } from 'react-router-dom'
import {
  entityPath,
  episodePath,
  formatDate,
  formatTimestamp,
  type PublicEntityType,
  type PublicEpisodeListItem,
  type PublicQuote,
} from '../../api/public'
import type { SignInIntent } from '../../utils/signIn'
import { quoteUrl, useSignIn } from './helpers'

function cx(...parts: (string | false | null | undefined)[]) {
  return parts.filter(Boolean).join(' ')
}

export function GoogleG() {
  return (
    <svg className="ts-btn-icon" viewBox="0 0 18 18" width={18} height={18} aria-hidden="true">
      <path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.57 2.68-3.88 2.68-6.62z" />
      <path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.8.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18z" />
      <path fill="#FBBC05" d="M3.97 10.72a5.4 5.4 0 0 1 0-3.44V4.95H.96a9 9 0 0 0 0 8.1l3.01-2.33z" />
      <path fill="#EA4335" d="M9 3.58c1.32 0 2.5.46 3.44 1.35l2.58-2.58A9 9 0 0 0 .96 4.95l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58z" />
    </svg>
  )
}

export function Button({
  variant = 'primary',
  google = false,
  onClick,
  to,
  state,
  rel,
  children,
  type = 'button',
}: {
  variant?: 'primary' | 'secondary' | 'text'
  google?: boolean
  onClick?: () => void
  /** Navigation (e.g. to /login): rendered as a link styled as a button. */
  to?: string
  /** Router state the link carries (a sign-in link's ``next`` and ``intent``). */
  state?: unknown
  rel?: string
  children: ReactNode
  type?: 'button' | 'submit'
}) {
  const className = cx('ts-btn', `ts-btn-${variant}`)
  const content = (
    <>
      {google && <GoogleG />}
      <span>{children}</span>
    </>
  )
  if (to) {
    return (
      <Link to={to} state={state} rel={rel} className={className}>
        {content}
      </Link>
    )
  }
  return (
    <button type={type} className={className} onClick={onClick}>
      {content}
    </button>
  )
}

export function EntityDot({ type }: { type: PublicEntityType }) {
  return <span className={cx('ts-dot', `ts-dot-${type}`)} aria-hidden="true" />
}

export function FilterChip({
  selected,
  type,
  to,
  onClick,
  children,
}: {
  selected?: boolean
  type?: PublicEntityType
  to?: To
  onClick?: () => void
  children: ReactNode
}) {
  const cls = cx('ts-chip', selected && 'ts-chip-selected')
  const inner = (
    <>
      {type && <EntityDot type={type} />}
      <span>{children}</span>
    </>
  )
  if (to) {
    return (
      <Link to={to} className={cls} aria-current={selected ? 'page' : undefined} replace>
        {inner}
      </Link>
    )
  }
  return (
    <button type="button" className={cls} aria-pressed={!!selected} onClick={onClick}>
      {inner}
    </button>
  )
}

function LinkIcon() {
  return (
    <svg viewBox="0 0 24 24" width={18} height={18} aria-hidden="true" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <path d="M10 13a5 5 0 0 0 7.07 0l3-3a5 5 0 0 0-7.07-7.07l-1.5 1.5" />
      <path d="M14 11a5 5 0 0 0-7.07 0l-3 3a5 5 0 0 0 7.07 7.07l1.5-1.5" />
    </svg>
  )
}

function CheckIcon() {
  return (
    <svg viewBox="0 0 24 24" width={18} height={18} aria-hidden="true" fill="none" stroke="currentColor" strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round">
      <path d="M5 12l5 5L20 7" />
    </svg>
  )
}

function spokenTime(seconds: number) {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  return `${h ? `${h} hours ` : ''}${m} minutes ${s} seconds`
}

/**
 * One attributed, timed quote. ``variant``: stream (lists), featured (the
 * page's one large quote), linked (reached via a ?q= link).
 */
export function QuoteCard({
  quote,
  variant = 'stream',
  showSpeaker = true,
  showShow = true,
  showDate = true,
  youtubeUrl,
  label,
  id,
  className,
}: {
  quote: PublicQuote
  variant?: 'stream' | 'featured' | 'linked'
  showSpeaker?: boolean
  showShow?: boolean
  showDate?: boolean
  youtubeUrl?: string | null
  label?: string
  id?: string
  className?: string
}) {
  const [copied, setCopied] = useState(false)
  const href = quoteUrl(quote)
  const copy = async () => {
    const absolute = `${window.location.origin}${href}`
    try {
      if (navigator.share && window.matchMedia?.('(hover: none)').matches) {
        await navigator.share({ title: `${quote.speaker.name} on ${quote.podcast.title}`, url: absolute })
        return
      }
      await navigator.clipboard.writeText(absolute)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      // Clipboard refused (permissions, old browser): nothing to recover.
    }
  }
  const meta: ReactNode[] = []
  const sep = (k: string) => (
    <span key={k} className="ts-sep" aria-hidden="true">
      ·
    </span>
  )
  if (showSpeaker) {
    meta.push(
      <Link key="s" to={entityPath({ type: 'person', slug: quote.speaker.slug })} className="ts-quote-speaker">
        {quote.speaker.name}
      </Link>,
    )
  }
  if (showShow) {
    if (meta.length) meta.push(sep('s1'))
    meta.push(
      <Link key="sh" to={`/podcasts/${quote.podcast.slug}`} className="ts-quote-show">
        {quote.podcast.title}
      </Link>,
    )
  }
  if (showDate && quote.episode.pub_date) {
    if (meta.length) meta.push(sep('s2'))
    meta.push(<span key="d">{formatDate(quote.episode.pub_date)}</span>)
  }
  if (meta.length) meta.push(sep('s3'))
  meta.push(
    <Link key="t" to={href} className="ts-quote-time" aria-label={`at ${spokenTime(quote.start_seconds)}`}>
      at {formatTimestamp(quote.start_seconds)}
    </Link>,
  )
  if (youtubeUrl) {
    meta.push(sep('s4'))
    meta.push(
      <a key="y" href={`${youtubeUrl}${youtubeUrl.includes('?') ? '&' : '?'}t=${quote.start_seconds}`} target="_blank" rel="noopener noreferrer">
        Hear it on YouTube
      </a>,
    )
  }
  return (
    <figure className={cx('ts-quote', `ts-quote-${variant}`, className)} id={id}>
      {variant === 'featured' && (
        <span className="ts-quote-mark" aria-hidden="true">
          {'“'}
        </span>
      )}
      {label && <div className="ts-quote-label">{label}</div>}
      <div className="ts-quote-row">
        <blockquote className="ts-quote-text">
          {variant === 'featured' ? quote.text : `“${quote.text}”`}
        </blockquote>
        <button type="button" className="ts-quote-copy" aria-label={copied ? 'Link copied' : 'Copy link to this quote'} onClick={copy}>
          {copied ? <CheckIcon /> : <LinkIcon />}
        </button>
      </div>
      <figcaption className="ts-quote-meta">{meta}</figcaption>
    </figure>
  )
}

export function SignInCard({
  title,
  children,
  buttonLabel = 'Sign in',
  intent,
  next,
}: {
  title: string
  children?: ReactNode
  /** The action, as signed-in users know it (Follow, Read the transcript). */
  buttonLabel?: string
  intent?: SignInIntent
  next?: string
}) {
  const signIn = useSignIn(intent, next)
  return (
    <section className="ts-signin">
      <h3 className="ts-signin-title">{title}</h3>
      {children && <p className="ts-signin-body">{children}</p>}
      <div className="ts-signin-action">
        <Button to={signIn.to} state={signIn.state} rel="nofollow">
          {buttonLabel}
        </Button>
      </div>
    </section>
  )
}

function initials(name: string) {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0])
    .join('')
    .toUpperCase()
}

export function Avatar({
  src,
  name,
  size,
  shape = 'round',
  eager = false,
  logo = false,
}: {
  src?: string | null
  name: string
  size: number
  shape?: 'round' | 'square'
  eager?: boolean
  /** Fit the whole image inside the tile (logos of any aspect ratio) instead of cropping to fill it. */
  logo?: boolean
}) {
  const [failed, setFailed] = useState(false)
  const cls = cx('ts-avatar', shape === 'square' ? 'ts-avatar-square' : 'ts-avatar-round', logo && 'ts-avatar-logo')
  const style = { width: size, height: size, fontSize: Math.round(size * 0.36) }
  if (src && !failed) {
    return (
      <img
        className={cls}
        src={src}
        alt=""
        width={size}
        height={size}
        style={style}
        loading={eager ? 'eager' : 'lazy'}
        onError={() => setFailed(true)}
      />
    )
  }
  return (
    <span className={cls} style={style} aria-hidden="true">
      {shape === 'square' ? '' : initials(name)}
    </span>
  )
}

/** One episode in a list: artwork (episode, else show), then date, title,
 * gist and guests. ``podcast`` adds the show name for cross-show lists. */
export function EpisodeRow({
  episode,
  podcastSlug,
  podcastTitle,
  showPodcast = false,
}: {
  episode: PublicEpisodeListItem
  podcastSlug: string
  podcastTitle: string
  showPodcast?: boolean
}) {
  const href = episodePath(podcastSlug, episode.slug)
  return (
    <article className="ts-eprow ts-eprow-media">
      <Link to={href} className="ts-eprow-art" tabIndex={-1} aria-hidden="true">
        <Avatar src={episode.image_url ?? episode.podcast?.image_url} name={podcastTitle} size={72} shape="square" />
      </Link>
      <div className="ts-eprow-body">
        <div className="ts-meta">
          {showPodcast && (
            <>
              <Link to={`/podcasts/${podcastSlug}`} className="ts-link">
                {podcastTitle}
              </Link>
              <span className="ts-sep" aria-hidden="true">
                ·
              </span>
            </>
          )}
          <span>{formatDate(episode.pub_date)}</span>
          {episode.duration_formatted && (
            <>
              <span className="ts-sep" aria-hidden="true">
                ·
              </span>
              <span>{episode.duration_formatted}</span>
            </>
          )}
        </div>
        <Link to={href} className="ts-eprow-title">
          {episode.title}
        </Link>
        {episode.summary_preview && <p className="ts-eprow-desc">{episode.summary_preview}</p>}
        <GuestLine guests={episode.guests} />
      </div>
    </article>
  )
}

const GROUP_ORDER: PublicEntityType[] = ['person', 'company', 'product', 'topic']
const GROUP_VISIBLE = 8

/** People, companies, products and topics as separate titled lists, in the
 * signed-in reader's order (EntityRail); each keeps the server's order and
 * folds past eight behind "Show all". */
export function EntityGroups<T extends { name: string; type: PublicEntityType; slug: string }>({
  items,
  titles,
  count,
}: {
  items: T[]
  titles: Record<PublicEntityType, string>
  count: (item: T) => string
}) {
  const [open, setOpen] = useState<ReadonlySet<PublicEntityType>>(new Set())
  return (
    <>
      {GROUP_ORDER.map((type) => {
        const group = items.filter((item) => item.type === type)
        if (group.length === 0) return null
        const shown = open.has(type) ? group : group.slice(0, GROUP_VISIBLE)
        return (
          <section key={type} className="ts-rail-module">
            <h3 className="ts-rail-label">{titles[type]}</h3>
            <ul className="ts-related">
              {shown.map((item) => (
                <li key={item.slug}>
                  <Link to={entityPath(item)}>
                    <EntityDot type={item.type} />
                    <span>{item.name}</span>
                  </Link>
                  <span className="ts-related-count">{count(item)}</span>
                </li>
              ))}
            </ul>
            {group.length > shown.length && (
              <button
                type="button"
                className="ts-link ts-link-button"
                style={{ fontSize: 13 }}
                onClick={() => setOpen((prev) => new Set(prev).add(type))}
              >
                Show all ({group.length})
              </button>
            )}
          </section>
        )
      })}
    </>
  )
}

function GuestLine({ guests }: { guests: { name: string; slug: string; type: PublicEntityType }[] }) {
  if (!guests.length) return null
  return (
    <div className="ts-eprow-guests">
      <span>With </span>
      {guests.map((g, i) => (
        <span key={g.slug}>
          {i > 0 && (i === guests.length - 1 ? ' and ' : ', ')}
          <Link to={entityPath(g)}>{g.name}</Link>
        </span>
      ))}
    </div>
  )
}

export function FactList({ title, source, items }: { title: string; source?: string; items: { label: string; value: ReactNode; href?: string | null }[] }) {
  if (!items.length) return null
  return (
    <section className="ts-rail-module">
      <h3 className="ts-rail-label">
        {title}
        {source && <span className="ts-rail-source"> ({source})</span>}
      </h3>
      <dl className="ts-facts-list">
        {items.map((it) => (
          <div key={it.label} className="ts-facts-row">
            <dt>{it.label}</dt>
            <dd>
              {it.href ? (
                <a href={it.href} target="_blank" rel="noopener noreferrer">
                  {it.value}
                </a>
              ) : (
                it.value
              )}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  )
}

const PLATFORM_ORDER = ['apple', 'spotify', 'youtube', 'rss']
const PLATFORM_LABEL: Record<string, string> = { apple: 'Apple Podcasts', spotify: 'Spotify', youtube: 'YouTube', rss: 'RSS' }

export function ListenOn({ links, label = 'Listen on' }: { links: { platform: string; url: string | null | undefined }[]; label?: string | false }) {
  const present = links
    .filter((l): l is { platform: string; url: string } => !!l.url)
    .sort((a, b) => PLATFORM_ORDER.indexOf(a.platform) - PLATFORM_ORDER.indexOf(b.platform))
  const seen = new Set<string>()
  const unique = present.filter((l) => (seen.has(l.platform) ? false : (seen.add(l.platform), true)))
  if (!unique.length) return null
  return (
    <div className="ts-listen">
      {label !== false && <span className="ts-rail-label">{label}</span>}
      <div className="ts-listen-row">
        {unique.map((l) => (
          <a key={l.platform} href={l.url} className="ts-listen-link" target="_blank" rel="noopener noreferrer">
            {PLATFORM_LABEL[l.platform] ?? l.platform}
          </a>
        ))}
      </div>
    </div>
  )
}

export function ActivitySparkline({ weeks, start }: { weeks: number[]; start: string }) {
  const total = weeks.reduce((a, b) => a + b, 0)
  const active = weeks.filter((n) => n > 0).length
  if (active < 8 || total === 0) return null
  const max = Math.max(1, ...weeks)
  const last = weeks.length - 1
  const startDate = new Date(start)
  const label = (i: number) => {
    const d = new Date(startDate)
    d.setDate(d.getDate() + i * 7)
    return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
  }
  return (
    <section className="ts-rail-module">
      <h3 className="ts-rail-label">Activity</h3>
      <figure className="ts-spark">
        <div className="ts-spark-bars" role="img" aria-label={`${total} episodes over the last ${weeks.length} weeks; ${weeks[last]} this week`}>
          {weeks.map((n, i) => (
            <span
              key={i}
              className={cx('ts-spark-bar', i === last && 'ts-spark-bar-now')}
              style={{ height: `${Math.max(4, Math.round((n / max) * 100))}%` }}
              title={`Week of ${label(i)}: ${n} episode${n === 1 ? '' : 's'}`}
            />
          ))}
        </div>
        <figcaption className="ts-spark-caption">Episodes per week, last 6 months</figcaption>
      </figure>
    </section>
  )
}

export function Skeleton({ width, height, round = false }: { width: number | string; height: number; round?: boolean }) {
  return <span className="ts-skeleton" style={{ display: 'block', width, height, borderRadius: round ? 9999 : undefined }} />
}

export function QuoteSkeleton() {
  return (
    <div className="ts-quote" aria-hidden="true">
      <Skeleton width="92%" height={18} />
      <div style={{ height: 8 }} />
      <Skeleton width="70%" height={18} />
      <div style={{ height: 12 }} />
      <Skeleton width={180} height={12} />
    </div>
  )
}

export function EpisodeGroupHead({ quote }: { quote: PublicQuote }) {
  return (
    <Link to={episodePath(quote.podcast.slug, quote.episode.slug)} className="ts-ep-head">
      <Avatar src={quote.episode.image_url} name={quote.podcast.title} size={48} shape="square" />
      <div style={{ minWidth: 0 }}>
        <div className="ts-meta">
          <span style={{ color: 'var(--primary)', fontWeight: 600 }}>{quote.podcast.title}</span>
          {quote.episode.pub_date && (
            <>
              <span className="ts-sep" aria-hidden="true">
                ·
              </span>
              <span>{formatDate(quote.episode.pub_date)}</span>
            </>
          )}
        </div>
        <div className="ts-ep-head-title">{quote.episode.title}</div>
      </div>
    </Link>
  )
}
