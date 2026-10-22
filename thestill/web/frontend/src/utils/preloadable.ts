// Spec #105 — React.lazy with a preload. ``lazy`` suspends on its first
// render even when the chunk is already in memory, so a page the server
// wrote would flash its Suspense fallback (a blank frame) before React drew
// it again. Preloaded first, the component renders on the first commit.
import { createElement, lazy, useState, type ComponentType } from 'react'

export type Preloadable<P extends object> = ComponentType<P> & { preload: () => Promise<unknown> }

export function preloadable<P extends object>(factory: () => Promise<{ default: ComponentType<P> }>): Preloadable<P> {
  let loaded: ComponentType<P> | null = null
  let pending: Promise<{ default: ComponentType<P> }> | null = null
  const load = () =>
    (pending ??= factory().then(
      (module) => {
        loaded = module.default
        return module
      },
      (error: unknown) => {
        pending = null // a failed chunk load may succeed on the next try
        throw error
      },
    ))
  const Lazy = lazy(load)
  function Component(props: P) {
    // Chosen once per mount: switching from Lazy to the loaded component
    // later would be a new element type, and React would remount the page.
    const [Type] = useState<ComponentType<P>>(() => loaded ?? Lazy)
    return createElement(Type, props)
  }
  Component.preload = load
  return Component
}
