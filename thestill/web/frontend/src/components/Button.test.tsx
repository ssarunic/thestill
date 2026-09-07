import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import Button, { PlayIcon } from './Button'

describe('Button sizes (spec #76 §5.2)', () => {
  it('icon size is a 44 px circle with a screen-reader label', () => {
    render(
      <Button size="icon" variant="secondary" icon={<PlayIcon />}>
        <span className="sr-only">Play</span>
      </Button>,
    )
    const button = screen.getByRole('button', { name: 'Play' })
    expect(button.className).toContain('w-11')
    expect(button.className).toContain('h-11')
    expect(button.className).toContain('rounded-full')
    expect(button.className).not.toContain('rounded-lg')
  })

  it('iconSm keeps the 44 px hit area and paints a 36 px disc', () => {
    render(
      <Button size="iconSm" icon={<PlayIcon />}>
        <span className="sr-only">Play</span>
      </Button>,
    )
    const button = screen.getByRole('button', { name: 'Play' })
    expect(button.className).toContain('w-11')
    expect(button.className).toContain('border-4')
    expect(button.className).toContain('bg-clip-padding')
  })

  it('pill swaps the text radius for rounded-full', () => {
    render(<Button size="lg" pill>58 min</Button>)
    expect(screen.getByRole('button', { name: '58 min' }).className).toContain('rounded-full')
    render(<Button size="lg">Follow</Button>)
    expect(screen.getByRole('button', { name: 'Follow' }).className).not.toContain('rounded-full')
  })
})
