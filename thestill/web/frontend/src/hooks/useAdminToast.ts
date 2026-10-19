import { useToast } from '../components/Toast'

// The admin console's success / failure toasts for a mutation's callbacks.
export function useAdminToast() {
  const { showToast } = useToast()
  return {
    ok: (message: string) => showToast(message, 'success'),
    fail: (error: Error) => showToast(error.message, 'error'),
  }
}
