import { createContext, useContext, useState, useCallback, ReactNode } from 'react'
import { abovePlayer } from '../constants/layers'

type ToastType = 'success' | 'error' | 'info'

/** A button inside the toast; pressing it runs ``onClick`` and closes the toast. */
export interface ToastAction {
  label: string
  onClick: () => void
}

interface ToastOptions {
  action?: ToastAction
  /** How long the toast stays up (default 5 s). */
  durationMs?: number
}

interface Toast {
  id: number
  message: string
  type: ToastType
  action?: ToastAction
}

interface ToastContextType {
  showToast: (message: string, type?: ToastType, options?: ToastOptions) => number
  dismissToast: (id: number) => void
}

const DEFAULT_DURATION_MS = 5000

const ToastContext = createContext<ToastContextType | null>(null)

let toastId = 0

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])

  const showToast = useCallback((message: string, type: ToastType = 'info', options: ToastOptions = {}) => {
    const id = ++toastId
    setToasts((prev) => [...prev, { id, message, type, action: options.action }])

    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id))
    }, options.durationMs ?? DEFAULT_DURATION_MS)

    return id
  }, [])

  const dismissToast = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  return (
    <ToastContext.Provider value={{ showToast, dismissToast }}>
      {children}
      {/* Toast container */}
      {/* Phones: inset 16 px from both edges, so a long message wraps
          instead of running off the left side (max-w-sm is wider than a
          320 px screen). */}
      <div className="fixed left-4 right-4 z-[70] flex flex-col items-end gap-2 sm:left-auto" style={{ bottom: abovePlayer('1rem') }}>
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={`
              flex items-center gap-3 px-4 py-3 rounded-lg shadow-lg max-w-sm
              animate-slide-in
              ${toast.type === 'success' ? 'bg-green-600 text-white' : ''}
              ${toast.type === 'error' ? 'bg-red-600 text-white' : ''}
              ${toast.type === 'info' ? 'bg-gray-800 text-white' : ''}
            `}
          >
            {/* Icon */}
            {toast.type === 'success' && (
              <svg className="w-5 h-5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
            )}
            {toast.type === 'error' && (
              <svg className="w-5 h-5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            )}
            {toast.type === 'info' && (
              <svg className="w-5 h-5 flex-shrink-0 animate-spin" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path
                  className="opacity-75"
                  fill="currentColor"
                  d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                />
              </svg>
            )}
            {/* Message */}
            <span className="text-sm font-medium">{toast.message}</span>
            {toast.action && (
              <button
                type="button"
                onClick={() => {
                  dismissToast(toast.id)
                  toast.action?.onClick()
                }}
                className="shrink-0 rounded px-2 py-1 text-sm font-semibold underline underline-offset-2 hover:bg-white/20"
              >
                {toast.action.label}
              </button>
            )}
            {/* Close button */}
            <button
              onClick={() => dismissToast(toast.id)}
              className="ml-auto p-1 hover:bg-white/20 rounded transition-colors"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast() {
  const context = useContext(ToastContext)
  if (!context) {
    throw new Error('useToast must be used within a ToastProvider')
  }
  return context
}
