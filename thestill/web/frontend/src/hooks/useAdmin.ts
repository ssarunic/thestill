// Spec #104 Phase 4 — react-query hooks for the admin console.
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  getAdminAudit,
  getAdminSettings,
  getAdminUser,
  getAdminUsers,
  logoutEverywhere,
  putAdminSetting,
  removeIntroducedFeeds,
  resetAdminSetting,
  sendTestAlert,
  setAdminRole,
  setQuotaOverrides,
  setUserStatus,
  type AdminAuditQuery,
  type AdminUsersQuery,
} from '../api/admin'
import type { AccountStatus } from '../api/types'

const ADMIN = ['admin'] as const

export function useAdminUsers(query: AdminUsersQuery, enabled = true) {
  return useQuery({
    queryKey: [...ADMIN, 'users', query],
    queryFn: () => getAdminUsers(query),
    placeholderData: keepPreviousData,
    staleTime: 15_000,
    enabled,
  })
}

export function useAdminUser(id: string, enabled = true) {
  return useQuery({ queryKey: [...ADMIN, 'user', id], queryFn: () => getAdminUser(id), staleTime: 15_000, enabled })
}

export function useAdminSettings(enabled = true) {
  return useQuery({ queryKey: [...ADMIN, 'settings'], queryFn: getAdminSettings, staleTime: 30_000, enabled })
}

export function useAdminAudit(query: AdminAuditQuery, enabled = true) {
  return useQuery({
    queryKey: [...ADMIN, 'audit', query],
    queryFn: () => getAdminAudit(query),
    placeholderData: keepPreviousData,
    staleTime: 15_000,
    enabled,
  })
}

/** Every admin write invalidates the whole console: lists, the user, the log. */
function useAdminMutation<TArgs, TResult>(fn: (args: TArgs) => Promise<TResult>) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ADMIN }),
  })
}

export const useSetUserStatus = () =>
  useAdminMutation(
    ({ id, ...body }: { id: string; status: AccountStatus; reason?: string; remove_introduced_feeds?: boolean }) =>
      setUserStatus(id, body),
  )
export const useLogoutEverywhere = () => useAdminMutation((id: string) => logoutEverywhere(id))
export const useRemoveIntroducedFeeds = () => useAdminMutation((id: string) => removeIntroducedFeeds(id))
export const useSetQuotaOverrides = () =>
  useAdminMutation((args: { id: string; overrides: Record<string, number> }) => setQuotaOverrides(args.id, args.overrides))
export const useSetAdminRole = () =>
  useAdminMutation((args: { id: string; isAdmin: boolean }) => setAdminRole(args.id, args.isAdmin))
export const usePutAdminSetting = () =>
  useAdminMutation((args: { key: string; value: unknown }) => putAdminSetting(args.key, args.value))
export const useResetAdminSetting = () => useAdminMutation((key: string) => resetAdminSetting(key))
export const useSendTestAlert = () => useMutation({ mutationFn: sendTestAlert })
