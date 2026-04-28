import { useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

export function parentPathOf(pathname: string): string | null {
  const trimmed = pathname.replace(/\/+$/, '')
  if (trimmed === '' || trimmed === '/') return null

  const episodeDetail = trimmed.match(/^(\/podcasts\/[^/]+)\/episodes\/[^/]+$/)
  if (episodeDetail) return episodeDetail[1]

  const segments = trimmed.split('/').filter(Boolean)
  if (segments.length === 1) return '/'
  if (segments.length === 2) return '/' + segments[0]
  return null
}

function isEditableTarget(el: Element | null): boolean {
  if (!(el instanceof HTMLElement)) return false
  if (el.isContentEditable) return true
  const tag = el.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
  return false
}

function hasOpenDialog(): boolean {
  return document.querySelector('[role="dialog"], [aria-modal="true"]') !== null
}

export function useEscapeUp(): void {
  const navigate = useNavigate()
  const location = useLocation()

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      if (e.defaultPrevented) return
      if (e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) return
      if (isEditableTarget(document.activeElement)) return
      if (hasOpenDialog()) return

      const parent = parentPathOf(location.pathname)
      if (!parent) return

      e.preventDefault()
      navigate(parent)
    }

    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [navigate, location.pathname])
}
