import { useId, useState } from 'react'
import Button from './Button'
import type { McpTokenScope } from '../api/types'
import { SCOPE_COPY } from '../constants/mcpScopes'

// The MCP scope checkboxes, shared by the Settings connector card (spec #78)
// and the OAuth consent screen (spec #109). `read` is always on and locked,
// `follows` is opt-in, `pipeline` is offered to admins only — the server
// applies the same rule, so this form only saves a refused round trip.

export default function McpScopeForm({
  isAdmin,
  initial,
  onSubmit,
  onCancel,
  busy,
  submitLabel,
  cancelLabel = 'Cancel',
  legend = 'What this connector may do',
  offer,
}: {
  isAdmin: boolean
  initial: McpTokenScope[]
  onSubmit: (scopes: McpTokenScope[]) => void
  onCancel?: () => void
  busy: boolean
  submitLabel: string
  cancelLabel?: string
  legend?: string
  // Scopes to show (the consent screen offers only what the app asked for).
  // `read` is always shown; `pipeline` only to admins.
  offer?: McpTokenScope[]
}) {
  const id = useId()
  const [follows, setFollows] = useState(initial.includes('follows'))
  const [pipeline, setPipeline] = useState(initial.includes('pipeline'))
  const wanted = new Set<McpTokenScope>(offer ?? ['read', 'follows', 'pipeline'])
  const offered = (['read', 'follows', 'pipeline'] as McpTokenScope[]).filter(
    (scope) => scope === 'read' || (wanted.has(scope) && (scope !== 'pipeline' || isAdmin)),
  )

  return (
    <form
      className="space-y-3"
      onSubmit={(e) => {
        e.preventDefault()
        const scopes: McpTokenScope[] = ['read']
        if (follows && offered.includes('follows')) scopes.push('follows')
        if (pipeline && offered.includes('pipeline')) scopes.push('pipeline')
        onSubmit(scopes)
      }}
    >
      <fieldset className="space-y-2">
        <legend className="text-sm font-medium text-gray-900">{legend}</legend>
        {offered.map((scope) => {
          const checked = scope === 'read' ? true : scope === 'follows' ? follows : pipeline
          const setter = scope === 'follows' ? setFollows : setPipeline
          return (
            <label key={scope} className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={checked}
                disabled={scope === 'read' || busy}
                onChange={(e) => setter(e.target.checked)}
                aria-labelledby={`${id}-${scope}-label`}
                aria-describedby={`${id}-${scope}-hint`}
              />
              <span>
                <span id={`${id}-${scope}-label`} className="font-medium text-gray-900">
                  {SCOPE_COPY[scope].label}
                </span>
                <span id={`${id}-${scope}-hint`} className="block text-xs text-gray-500">
                  {SCOPE_COPY[scope].hint}
                </span>
              </span>
            </label>
          )
        })}
      </fieldset>
      <div className="flex gap-2">
        <Button type="submit" size="sm" isLoading={busy}>
          {submitLabel}
        </Button>
        {onCancel && (
          <Button type="button" variant="secondary" size="sm" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </Button>
        )}
      </div>
    </form>
  )
}
