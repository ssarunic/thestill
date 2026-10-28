import type { AdminAuditEntry } from '../api/types'

// Every timestamp on the admin pages: local date and time, minutes precision.
export function formatWhen(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' }) : '—'
}

// Spec #104 — one readable line per admin audit entry.
export function describeAuditEntry(entry: AdminAuditEntry): string {
  const d = entry.details as Record<string, unknown>
  const reason = typeof d.reason === 'string' && d.reason ? ` — “${d.reason}”` : ''
  switch (entry.action) {
    case 'user.ban':
      return `Banned${reason} (${d.follows_moved ?? 0} follows moved aside${
        d.introduced_feeds_removed ? `, ${d.introduced_feeds_removed} feeds they brought in removed` : ''
      }, ${d.tasks_cancelled ?? 0} tasks cancelled)`
    case 'user.suspend':
      return `Suspended${reason}`
    case 'user.reinstate':
      return `Reinstated${reason} (${d.follows_restored ?? 0} follows restored)`
    case 'user.logout_everywhere':
      return 'Signed out everywhere'
    case 'user.remove_introduced_feeds':
      return `Removed ${d.feeds_removed ?? 0} feeds they brought in`
    case 'user.quota_override':
      // The CLI records one quota; the console records the whole map.
      return typeof d.quota === 'string'
        ? `${d.quota}: ${JSON.stringify(d.old ?? 'default')} → ${JSON.stringify(d.new ?? 'default')}`
        : `Quota overrides: ${JSON.stringify(d.old ?? {})} → ${JSON.stringify(d.new ?? {})}`
    case 'admin.grant':
      return 'Made an admin'
    case 'admin.revoke':
      return 'Admin role removed'
    case 'setting.set':
      return `${String(d.key)}: ${JSON.stringify(d.old)} → ${JSON.stringify(d.new)}`
    case 'entity.merge':
      return `Merged ${String(d.loser)} into ${String(d.keeper)}${reason}`
    case 'entity.move_mentions':
      return `Moved ${String(d.moved ?? 0)} mentions ${String(d.from_entity_id)} → ${String(d.to_entity_id)}${reason}`
    case 'entity.split':
      return `Split ${String(d.entity_id)} out of ${String(d.from_entity_id)} (${String(d.moved ?? 0)} mentions)${reason}`
    case 'entity.relink':
      return `${String(d.entity_id)}: ${String(d.old ?? '—')} → ${String(d.new ?? '—')}${reason}`
    case 'entity.set_type':
      return `${String(d.entity_id)}: ${String(d.old)} → ${String(d.new)}${reason}`
    case 'entity.blacklist':
      return `Blacklisted “${String(d.surface_form)}” ↛ ${String(d.wrong_qid)}${reason}`
    case 'entity.prune_orphans':
      return `Deleted ${String(d.deleted ?? 0)} orphan entities`
    default:
      return entry.action
  }
}
