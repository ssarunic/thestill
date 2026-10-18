// A machine code such as ``feature_paused``, as opposed to readable text.
const ERROR_CODE = /^[a-z][a-z0-9_]*$/

// The text to show for a failed API call. FastAPI puts the reason under
// ``detail``: a string, a list (422 validation), or an object. An object's
// ``error`` is readable text on older routes (409 "Refresh already in
// progress", whose ``message`` is progress text) but a machine code on spec
// #104 refusals, whose readable text is ``message``.
export function apiErrorMessage(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const msgs = detail.map((d) => (d as { msg?: unknown })?.msg).filter((m): m is string => typeof m === 'string')
    if (msgs.length) return msgs.join('; ')
  } else if (detail && typeof detail === 'object') {
    const d = detail as { message?: unknown; error?: unknown; msg?: unknown }
    const errorIsCode = typeof d.error === 'string' && ERROR_CODE.test(d.error)
    const order = errorIsCode ? [d.message, d.msg, d.error] : [d.error, d.message, d.msg]
    for (const value of order) {
      if (typeof value === 'string' && value) return value
    }
  }
  return `API error: ${status}`
}

// Spec #104 — the stable code of a refusal (``account_disabled``,
// ``feature_paused``, ``read_only``), or null.
export function apiErrorCode(body: unknown): string | null {
  const detail = (body as { detail?: unknown } | null)?.detail
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const code = (detail as { error?: unknown }).error
    return typeof code === 'string' ? code : null
  }
  return null
}

// A suspended or banned account mid-session: every call now 403s, so send
// the reader to /login, which explains it, instead of failing piecemeal.
export function redirectIfAccountDisabled(body: unknown): void {
  if (apiErrorCode(body) === 'account_disabled' && window.location.pathname !== '/login') {
    window.location.assign('/login?error=account_disabled')
  }
}
