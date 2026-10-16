import { describe, it, expect } from 'vitest'
import { ADMIN_NAV_ITEMS, MAIN_NAV_ITEMS, visibleNavItems } from './navigation'

describe('admin navigation (spec #95)', () => {
  it('shows Analytics only in multi-user mode', () => {
    const multi = visibleNavItems(ADMIN_NAV_ITEMS, true).map((item) => item.to)
    const single = visibleNavItems(ADMIN_NAV_ITEMS, false).map((item) => item.to)
    expect(multi).toContain('/analytics')
    expect(single).not.toContain('/analytics')
    expect(single).toEqual(['/episodes', '/status', '/queue', '/failed'])
  })

  it('keeps the corpus-wide Episodes browser out of the main nav', () => {
    expect(MAIN_NAV_ITEMS.map((item) => item.to)).toEqual(['/inbox', '/podcasts', '/top'])
  })
})
