import { describe, it, expect } from 'vitest'
import { abovePlayer, MEDIA_HOST_ATTR, mediaLayerZIndex } from './layers'

function slotInside(wrap: (slot: HTMLElement) => HTMLElement): HTMLElement {
  const slot = document.createElement('div')
  document.body.appendChild(wrap(slot))
  return slot
}

describe('layers (spec #71 / #72)', () => {
  it('abovePlayer reads the published height with a 0px fallback', () => {
    expect(abovePlayer()).toBe('var(--player-h, 0px)')
    expect(abovePlayer('1rem')).toBe('calc(var(--player-h, 0px) + 1rem)')
  })

  it('mediaLayerZIndex picks one rung above the hosting surface', () => {
    const inPage = slotInside((s) => s)
    expect(mediaLayerZIndex(inPage)).toBe(40)

    const inReader = slotInside((s) => {
      const dialog = document.createElement('div')
      dialog.setAttribute('role', 'dialog')
      dialog.appendChild(s)
      return dialog
    })
    expect(mediaLayerZIndex(inReader)).toBe(60)

    // The Now Playing sheet is also a dialog; its marker must win.
    const inSheet = slotInside((s) => {
      const sheet = document.createElement('div')
      sheet.setAttribute('role', 'dialog')
      sheet.setAttribute(MEDIA_HOST_ATTR, 'now-playing')
      sheet.appendChild(s)
      return sheet
    })
    expect(mediaLayerZIndex(inSheet)).toBe(71)
  })
})
