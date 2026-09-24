import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

// A text filter that lives in the URL. ``value`` is what the user is typing;
// ``debouncedValue`` is the trimmed, settled value read back from ``?param=``
// and is what queries should key on. The URL is the source of truth so the
// browser Back button restores the filter (and, via the app-wide scroll
// restoration, the scroll position). ``replace`` keeps typing from spamming
// history. Behaviour-identical to the inline block on the Podcasts page;
// extracted for spec #85.
export function useDebouncedSearchParam(param: string, delayMs = 250) {
  const [searchParams, setSearchParams] = useSearchParams()
  const debouncedValue = searchParams.get(param) ?? ''
  const [value, setValue] = useState(debouncedValue)

  useEffect(() => {
    const trimmed = value.trim()
    if (trimmed === debouncedValue) return
    const t = setTimeout(() => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          if (trimmed) next.set(param, trimmed)
          else next.delete(param)
          return next
        },
        { replace: true },
      )
    }, delayMs)
    return () => clearTimeout(t)
  }, [value, debouncedValue, param, delayMs, setSearchParams])

  return { value, debouncedValue, setValue }
}
