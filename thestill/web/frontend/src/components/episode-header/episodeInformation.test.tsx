import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { buildEpisodeInformationRows } from './episodeInformation'
import type { EpisodeDetail } from '../../api/types'

function episode(overrides: Partial<EpisodeDetail> = {}): EpisodeDetail {
  return {
    id: 'ep-1',
    podcast_id: 'p-1',
    podcast_slug: 'prof-g',
    podcast_title: 'Prof G Markets',
    podcast_author: 'Prof G Media',
    podcast_language: 'en',
    origin: 'feed',
    import_kind: null,
    title: 'T',
    description: '',
    slug: 't',
    pub_date: '2026-09-06T07:00:00Z',
    audio_url: 'https://example.com/a.mp3',
    duration: 3505,
    duration_formatted: '58:25',
    external_id: 'x',
    state: 'summarized',
    has_transcript: true,
    has_summary: true,
    image_url: null,
    podcast_image_url: null,
    ...overrides,
  }
}

function values(rows: ReturnType<typeof buildEpisodeInformationRows>) {
  return Object.fromEntries(rows.map((r) => [r.label, r.value]))
}

describe('buildEpisodeInformationRows (spec #76 §3.6)', () => {
  it('builds the full row set in order with formatted values', () => {
    const rows = buildEpisodeInformationRows(
      episode({ episode_type: 'bonus', explicit: true, website_url: 'https://www.profgmedia.com/x', origin: 'import', import_kind: 'bare_audio' }),
    )
    expect(rows.map((r) => r.label)).toEqual(['Show', 'Author', 'Published', 'Length', 'Language', 'Type', 'Explicit', 'Show notes', 'Listen on', 'Source'])
    const v = values(rows)
    expect(v.Author).toBe('Prof G Media')
    expect(v.Length).toBe('58 min 25 s')
    expect(v.Language).toBe('English')
    expect(v.Type).toBe('Bonus')
    expect(v.Explicit).toBe('Yes')
    expect(v.Source).toBe('Imported (audio file)')
    const { container } = render(<MemoryRouter>{v.Show}{v['Show notes']}</MemoryRouter>)
    expect(container.querySelector('a[href="/podcasts/prof-g"]')).toHaveTextContent('Prof G Markets')
    expect(container.querySelector('a[href="https://www.profgmedia.com/x"]')).toHaveTextContent('profgmedia.com')
  })

  it('leaves optional rows empty so DefinitionList omits them', () => {
    const v = values(buildEpisodeInformationRows(episode({ podcast_author: null, duration: null, episode_type: 'full', explicit: null, website_url: null })))
    expect(v.Author).toBeNull()
    expect(v.Length).toBeNull()
    expect(v.Type).toBeNull()
    expect(v.Explicit).toBeNull()
    expect(v['Show notes']).toBeNull()
    expect(v['Listen on']).toBeNull()
    expect(v.Source).toBeNull()
  })

  it('renders one external link per resolved platform (spec #87)', () => {
    const v = values(
      buildEpisodeInformationRows(
        episode({
          platform_links: [
            { platform: 'apple', url: 'https://podcasts.apple.com/us/podcast/x/id1?i=2' },
            { platform: 'youtube', url: 'https://www.youtube.com/watch?v=abc' },
          ],
        }),
      ),
    )
    const { container } = render(<MemoryRouter>{v['Listen on']}</MemoryRouter>)
    const anchors = Array.from(container.querySelectorAll('a'))
    expect(anchors.map((a) => a.textContent)).toEqual(['Apple Podcasts', 'YouTube'])
    expect(anchors[0]).toHaveAttribute('href', 'https://podcasts.apple.com/us/podcast/x/id1?i=2')
    expect(anchors[0]).toHaveAttribute('target', '_blank')
  })

  it('omits the Listen on row when no platform is resolved or the platform is unknown', () => {
    expect(values(buildEpisodeInformationRows(episode({ platform_links: [] })))['Listen on']).toBeNull()
    const unknown = [{ platform: 'mixcloud' as never, url: 'https://example.com' }]
    expect(values(buildEpisodeInformationRows(episode({ platform_links: unknown })))['Listen on']).toBeNull()
  })

  it('labels imports without a known kind plainly', () => {
    expect(values(buildEpisodeInformationRows(episode({ origin: 'import', import_kind: null }))).Source).toBe('Imported')
    expect(values(buildEpisodeInformationRows(episode({ origin: 'import', import_kind: 'youtube' }))).Source).toBe('Imported (YouTube)')
    expect(values(buildEpisodeInformationRows(episode({ origin: 'import', import_kind: 'spotify_episode' }))).Source).toBe('Imported (Spotify)')
  })
})
