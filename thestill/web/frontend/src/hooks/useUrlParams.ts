import { useSearchParams } from 'react-router-dom'

// A list page's filters, sort and page, kept in the URL so Back restores
// them (docs/code-guidelines.md "Navigation invariants"). Changing any
// filter returns to page one.
export function useUrlParams() {
  const [searchParams, setSearchParams] = useSearchParams()
  const get = (key: string, fallback = '') => searchParams.get(key) ?? fallback
  const page = Math.max(0, Number(searchParams.get('page') ?? '0') || 0)
  const set = (key: string, value: string, fallback = '') =>
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (value && value !== fallback) next.set(key, value)
        else next.delete(key)
        if (key !== 'page') next.delete('page')
        return next
      },
      { replace: true },
    )
  const setPage = (next: number) => set('page', next > 0 ? String(next) : '')
  return { get, set, page, setPage }
}
