import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import BriefingCover from './BriefingCover'
import { describeShows } from '../utils/briefingFormat'
import type { BriefingPodcastGroup } from '../api/types'

function show(id: string, title: string, image: string | null = `https://img/${id}.jpg`): BriefingPodcastGroup {
  return { id, title, slug: id, image_url: image, episodes: [] }
}

describe('BriefingCover', () => {
  it('renders nothing without shows', () => {
    const { container } = render(<BriefingCover podcasts={[]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders one show as a single collage-sized artwork', () => {
    render(<BriefingCover podcasts={[show('a', 'Show A')]} />)
    const img = screen.getByRole('img', { name: 'Show A artwork' })
    expect(img).toHaveAttribute('src', 'https://img/a.jpg')
    expect(img.className).toContain('w-28')
    expect(img.className).toContain('rounded-xl')
  })

  it('composes two to four shows into one labelled mosaic', () => {
    const { container } = render(
      <BriefingCover podcasts={[show('a', 'Show A'), show('b', 'Show B'), show('c', 'Show C')]} />,
    )
    const tile = screen.getByRole('img', { name: 'Artwork from Show A, Show B, Show C' })
    expect(tile.className).toContain('grid-cols-2')
    const imgs = container.querySelectorAll('img')
    expect(imgs).toHaveLength(3)
    // Three shows: the first takes the left half.
    expect(imgs[0].className).toContain('row-span-2')
    expect(imgs[1].className).not.toContain('row-span-2')
    imgs.forEach((img) => expect(img).toHaveAttribute('alt', ''))
  })

  it('caps the mosaic at four shows and falls back per tile', () => {
    const { container } = render(
      <BriefingCover
        podcasts={[show('a', 'A'), show('b', 'B', null), show('c', 'C'), show('d', 'D'), show('e', 'E')]}
      />,
    )
    screen.getByRole('img', { name: 'Artwork from A, B, C, D' })
    expect(container.querySelectorAll('img')).toHaveLength(3)
    expect(container.querySelectorAll('.bg-gradient-to-br')).toHaveLength(1)
  })
})

describe('describeShows', () => {
  it('names one, two, three and many shows', () => {
    expect(describeShows([])).toBeNull()
    expect(describeShows([show('a', 'A')])).toBe('From A')
    expect(describeShows([show('a', 'A'), show('b', 'B')])).toBe('From A and B')
    expect(describeShows([show('a', 'A'), show('b', 'B'), show('c', 'C')])).toBe('From A, B and C')
    expect(describeShows([show('a', 'A'), show('b', 'B'), show('c', 'C'), show('d', 'D'), show('e', 'E')])).toBe(
      'From A, B and 3 more',
    )
  })
})
