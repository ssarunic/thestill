/** Inline horizontal bar for a share in [0, 1]; decoration only. */
export default function Bar({ value }: { value: number | null }) {
  const width = Math.max(0, Math.min(1, value ?? 0)) * 100
  return (
    <div className="h-1.5 w-full rounded-full bg-hairline" aria-hidden="true">
      <div className="h-1.5 rounded-full bg-accent" style={{ width: `${width}%` }} />
    </div>
  )
}
