interface PipelineStatusProps {
  pipeline: {
    discovered: number
    downloaded: number
    downsampled: number
    transcribed: number
    cleaned: number
    summarized: number
  }
}

const stages = [
  { key: 'discovered', label: 'Discovered', color: 'bg-gray-400' },
  { key: 'downloaded', label: 'Downloaded', color: 'bg-blue-400' },
  { key: 'downsampled', label: 'Downsampled', color: 'bg-indigo-400' },
  { key: 'transcribed', label: 'Transcribed', color: 'bg-purple-400' },
  { key: 'cleaned', label: 'Cleaned', color: 'bg-amber-400' },
  { key: 'summarized', label: 'Summarized', color: 'bg-green-400' },
] as const

export default function PipelineStatus({ pipeline }: PipelineStatusProps) {
  const total = Object.values(pipeline).reduce((a, b) => a + b, 0)

  return (
    <div className="space-y-4">
      {/* Visual bar */}
      <div className="h-4 rounded-full bg-gray-100 overflow-hidden flex">
        {stages.map(({ key, color }) => {
          const count = pipeline[key]
          const width = total > 0 ? (count / total) * 100 : 0
          if (width === 0) return null
          return (
            <div
              key={key}
              className={`${color} transition-all duration-500`}
              style={{ width: `${width}%` }}
              title={`${key}: ${count}`}
            />
          )
        })}
      </div>

      {/* Legend */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        {stages.map(({ key, label, color }) => (
          <div key={key} className="flex items-center gap-2">
            <div className={`w-3 h-3 rounded-full ${color}`} />
            <span className="text-sm text-gray-600">
              {label}: <span className="font-medium">{pipeline[key]}</span>
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
