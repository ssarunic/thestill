// Spec #104 Phase 4 — the admin console's API calls (/api/admin, admin only).
import { fetchJson, queryString } from './http'
import type {
  AccountStatus,
  AdminAuditResponse,
  AdminSetting,
  AdminSettingsResponse,
  AdminUser,
  AdminUserDetail,
  AdminUserSort,
  AdminUsersResponse,
} from './types'

const BASE = '/api/admin'

const json = (method: string, body?: unknown): RequestInit => ({
  method,
  body: body === undefined ? undefined : JSON.stringify(body),
})

export interface AdminUsersQuery {
  q?: string
  status?: AccountStatus
  sort?: AdminUserSort
  limit?: number
  offset?: number
}

export interface AdminAuditQuery {
  action?: string
  target_user_id?: string
  limit?: number
  offset?: number
}

export const getAdminUsers = (query: AdminUsersQuery) =>
  fetchJson<AdminUsersResponse>(`${BASE}/users${queryString(query)}`)

export const getAdminUser = (id: string) => fetchJson<AdminUserDetail>(`${BASE}/users/${id}`)

export const setUserStatus = (
  id: string,
  body: { status: AccountStatus; reason?: string; remove_introduced_feeds?: boolean },
) => fetchJson<{ user: AdminUser }>(`${BASE}/users/${id}/status`, json('POST', body))

export const logoutEverywhere = (id: string) =>
  fetchJson<{ user: AdminUser }>(`${BASE}/users/${id}/logout-everywhere`, json('POST'))

export const removeIntroducedFeeds = (id: string) =>
  fetchJson<{ feeds_removed: string[]; tasks_cancelled: number }>(
    `${BASE}/users/${id}/remove-introduced-feeds`,
    json('POST'),
  )

export const setQuotaOverrides = (id: string, overrides: Record<string, number>) =>
  fetchJson<{ quota_overrides: Record<string, number> }>(`${BASE}/users/${id}/quotas`, json('PUT', overrides))

export const setAdminRole = (id: string, isAdmin: boolean) =>
  fetchJson<{ user: AdminUser }>(`${BASE}/users/${id}/admin`, json('POST', { is_admin: isAdmin }))

export const getAdminSettings = () => fetchJson<AdminSettingsResponse>(`${BASE}/settings`)

export const putAdminSetting = (key: string, value: unknown) =>
  fetchJson<{ setting: AdminSetting }>(`${BASE}/settings/${key}`, json('PUT', { value }))

export const resetAdminSetting = (key: string) =>
  fetchJson<{ setting: AdminSetting }>(`${BASE}/settings/${key}`, json('DELETE'))

export const sendTestAlert = () => fetchJson<{ results: Record<string, boolean> }>(`${BASE}/alerts/test`, json('POST'))

export const getAdminAudit = (query: AdminAuditQuery) =>
  fetchJson<AdminAuditResponse>(`${BASE}/audit${queryString(query)}`)
