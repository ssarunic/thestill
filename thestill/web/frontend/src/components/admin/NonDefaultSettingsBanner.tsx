import { Link } from 'react-router-dom'
import { useAdminSettings } from '../../hooks/useAdmin'

// Spec #104 §4.3 — on every admin page while a runtime switch is off its
// default, so a forgotten read_only or closed signup gets noticed.
export default function NonDefaultSettingsBanner() {
  const { data } = useAdminSettings()
  const changed = Object.entries(data?.non_default ?? {})
  if (changed.length === 0) return null
  const readOnly = data?.non_default.read_only === true
  return (
    <div
      role="status"
      className={`mb-4 rounded-lg border px-4 py-2 text-sm ${
        readOnly ? 'border-red-300 bg-red-50 text-red-800' : 'border-amber-300 bg-amber-50 text-amber-900'
      }`}
    >
      {readOnly ? 'Read-only mode is on. ' : 'Not at their defaults: '}
      {changed.map(([key, value]) => `${key} = ${JSON.stringify(value)}`).join(', ')}.{' '}
      <Link to="/admin/settings" className="underline">
        Admin settings
      </Link>
    </div>
  )
}
