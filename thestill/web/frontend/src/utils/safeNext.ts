// Spec #94 — the sign-in return path, validated before it is used.
/**
 * ``value`` if it is a same-origin path that is safe to return to after
 * sign-in (spec #94): starts with one "/", not "//" or "/\\", no control
 * characters, never /login or /api/. Mirrors ``safe_next_path`` on the server.
 */
export function safeNextPath(value: string | null | undefined): string | null {
  if (!value || value.length > 512) return null
  if (!value.startsWith('/') || value.startsWith('//') || value.startsWith('/\\')) return null
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\\]/.test(value)) return null
  const path = value.split('?')[0].split('#')[0]
  if (path === '/login' || path.startsWith('/login/') || path.startsWith('/api/')) return null
  return value
}
