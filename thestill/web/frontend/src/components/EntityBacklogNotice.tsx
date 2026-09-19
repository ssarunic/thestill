// Spec #66 — a host without the `entities` extra skips entity extraction for
// every new episode and reports success, so nothing else on this page would
// ever show it. Entity search and the entity pages are blind to those
// episodes; an empty result there reads as "nobody mentioned it".
export default function EntityBacklogNotice({
  entityExtraction,
}: {
  entityExtraction?: { available: boolean; skipped_unavailable: number }
}) {
  if (!entityExtraction || entityExtraction.skipped_unavailable === 0) return null
  const count = entityExtraction.skipped_unavailable
  return (
    <div role="status" className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
      <p className="font-medium">
        {count.toLocaleString()} {count === 1 ? 'episode has' : 'episodes have'} no entity data
      </p>
      <p className="mt-1 text-amber-800">
        {entityExtraction.available
          ? 'Entity extraction was unavailable when these were processed. Run a backfill to include them in entity search.'
          : 'Entity extraction is not installed on this server, so every new episode is skipped. Entity search and entity pages do not cover these episodes until they are backfilled elsewhere.'}
      </p>
    </div>
  )
}
