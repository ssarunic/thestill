// A tiny bar chart of daily counts (oldest first), labelled for screen readers.
export default function Sparkline({ values, label }: { values: number[]; label: string }) {
  const max = Math.max(1, ...values)
  const width = values.length * 6
  return (
    <svg
      width={width}
      height={20}
      viewBox={`0 0 ${width} 20`}
      role="img"
      aria-label={`${label}: ${values.join(', ')} (last ${values.length} days)`}
      className="text-accent"
    >
      {values.map((value, i) => {
        const height = value ? Math.max(2, (value / max) * 20) : 1
        return <rect key={i} x={i * 6} y={20 - height} width={4} height={height} rx={1} fill="currentColor" opacity={value ? 1 : 0.25} />
      })}
    </svg>
  )
}
