// A JSON fetch that surfaces the server's reason (spec #104 refusals carry
// a readable ``message``) and sends a disabled session to /login.
import { apiErrorMessage, redirectIfAccountDisabled } from './errors'

export async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    credentials: 'include',
    ...init,
    headers: init?.body ? { 'Content-Type': 'application/json', ...init.headers } : init?.headers,
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    redirectIfAccountDisabled(body)
    throw new Error(apiErrorMessage(body, response.status))
  }
  return response.json()
}

/** ``?a=1&b=2`` from an object, skipping undefined and empty values. */
export function queryString(query: object): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value))
  }
  const text = params.toString()
  return text ? `?${text}` : ''
}
