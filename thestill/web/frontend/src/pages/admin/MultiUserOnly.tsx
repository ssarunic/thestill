// The user and audit pages have nothing to show in single-user mode.
export default function MultiUserOnly({ title }: { title: string }) {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-ink">{title}</h1>
      <div className="bg-surface border border-hairline rounded-lg p-6 max-w-md text-center mx-auto">
        <h2 className="text-ink font-medium mb-2">Multi-user mode only</h2>
        <p className="text-muted text-sm">In single-user mode the local user is the only account.</p>
      </div>
    </div>
  )
}
