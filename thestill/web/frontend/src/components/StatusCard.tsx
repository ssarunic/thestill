interface StatusCardProps {
  label: string
  value: number | string
  icon?: React.ReactNode
  color?: 'primary' | 'secondary' | 'success' | 'warning' | 'error'
  subtitle?: string
}

const colorClasses = {
  primary: 'bg-primary-50 text-primary-700 border-primary-200',
  secondary: 'bg-secondary-50 text-secondary-700 border-secondary-200',
  success: 'bg-green-50 text-green-700 border-green-200',
  warning: 'bg-amber-50 text-amber-700 border-amber-200',
  error: 'bg-red-50 text-red-700 border-red-200',
}

export default function StatusCard({
  label,
  value,
  icon,
  color = 'primary',
  subtitle,
}: StatusCardProps) {
  return (
    <div className={`rounded-lg border p-4 sm:p-6 ${colorClasses[color]}`}>
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs sm:text-sm font-medium opacity-80">{label}</p>
          <p className="text-2xl sm:text-3xl font-bold mt-1">{value}</p>
          {subtitle && <p className="text-xs sm:text-sm opacity-60 mt-1">{subtitle}</p>}
        </div>
        {icon && (
          <div className="opacity-50 [&>svg]:w-6 [&>svg]:h-6 sm:[&>svg]:w-8 sm:[&>svg]:h-8">
            {icon}
          </div>
        )}
      </div>
    </div>
  )
}
