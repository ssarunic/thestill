import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import DefinitionList from './DefinitionList'

describe('DefinitionList (spec #76 §5.4)', () => {
  it('renders a labelled dl and omits rows with empty values', () => {
    render(
      <DefinitionList
        heading="Information"
        rows={[
          { label: 'Show', value: <a href="/podcasts/x">Prof G</a> },
          { label: 'Author', value: null },
          { label: 'Length', value: '58 min 25 s', numeric: true },
          { label: 'Type', value: undefined },
          { label: 'Explicit', value: '' },
        ]}
      />,
    )
    expect(screen.getByRole('heading', { name: 'Information' })).toBeInTheDocument()
    const terms = screen.getAllByRole('term').map((el) => el.textContent)
    expect(terms).toEqual(['Show', 'Length'])
    expect(screen.getByRole('link', { name: 'Prof G' })).toBeInTheDocument()
    expect(screen.getByText('58 min 25 s').className).toContain('tabular-nums')
  })

  it('renders nothing when every row is empty', () => {
    const { container } = render(<DefinitionList heading="Information" rows={[{ label: 'A', value: null }]} />)
    expect(container).toBeEmptyDOMElement()
  })
})
