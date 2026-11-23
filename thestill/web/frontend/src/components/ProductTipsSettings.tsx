import { useState } from 'react'
import { useAuth } from '../contexts/AuthContext'

/**
 * Spec #118 — "Product tips by email": the welcome email and a few tips in
 * a reader's first weeks. Separate from usage events (spec #95) on purpose:
 * agreeing to be measured is not agreeing to marketing email, and the other
 * way round. Shown only where these emails can be sent (multi-user, an
 * email provider configured).
 */
export default function ProductTipsSettings() {
  const { user, isMultiUser, productTipsEmailAvailable, updateProductTipsEmail } = useAuth()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!user || !isMultiUser || !productTipsEmailAvailable) return null
  const enabled = user.product_tips_email_enabled === true

  async function toggle() {
    setError(null)
    setSaving(true)
    try {
      await updateProductTipsEmail(!enabled)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save')
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="bg-white border border-gray-200 rounded-lg p-6 space-y-3" aria-labelledby="product-tips-heading">
      <h2 id="product-tips-heading" className="text-lg font-semibold text-gray-900">
        Product tips by email
      </h2>
      <label className="flex items-start gap-3">
        <input
          type="checkbox"
          className="mt-1 h-4 w-4"
          checked={enabled}
          disabled={saving}
          onChange={toggle}
        />
        <span className="text-sm text-gray-700">
          A welcome email and a few tips in your first weeks, such as how to follow a show or connect Claude. At most
          one every couple of days, and never on a day your briefing email went out.
        </span>
      </label>
      <p className="text-xs text-gray-500">
        Briefing emails have their own setting above. Every tips email also has a one-click unsubscribe link.
      </p>
      {error && <p className="text-sm text-red-600">{error}</p>}
    </section>
  )
}
