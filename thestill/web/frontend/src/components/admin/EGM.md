# components/admin

## Overview

Presentational and mutation-wiring pieces of the spec #104 admin console (user list paging, account actions, quota overrides, settings banner). Every write goes through the `hooks/useAdmin.ts` mutations, which call `/api/admin/*` via `api/admin.ts`.

## Interface

- `NonDefaultSettingsBanner` (default) — banner shown on admin pages while a runtime switch is off its default (e.g. `read_only`, closed signup) [src/components/admin/NonDefaultSettingsBanner.tsx:NonDefaultSettingsBanner]
- `Pager`, `PagerProps` — previous/next paging for admin lists [src/components/admin/Pager.tsx:Pager]
- `QuotaOverridesForm`, `QuotaOverridesFormProps` — per-user quota overrides; empty field means the default [src/components/admin/QuotaOverridesForm.tsx:QuotaOverridesForm]
- `ReasonDialog`, `ReasonDialogProps` — confirm dialog that collects an audit reason for suspend / ban / reinstate [src/components/admin/ReasonDialog.tsx:ReasonDialog]
- `Sparkline` — tiny bar chart of daily counts (oldest first), labelled for screen readers [src/components/admin/Sparkline.tsx:Sparkline]
- `StatusBadge` — badge for an `AccountStatus` [src/components/admin/StatusBadge.tsx:StatusBadge]
- `UserActions` — suspend / reinstate / ban / sign-out-everywhere / remove-introduced-feeds / admin-role actions, each behind a confirm dialog [src/components/admin/UserActions.tsx:UserActions]
- `UsageTable`, `FollowsList`, `HistoryList` — sections of the admin user detail page [src/components/admin/UserSections.tsx:UsageTable]

## Invariants

- An admin cannot change their own status or admin role: Suspend, Ban and the admin-role button are disabled when `isSelf` [src/components/admin/UserActions.tsx:UserActions]
- `remove_introduced_feeds` is sent only when the new status is `banned` and the checkbox is ticked [src/components/admin/UserActions.tsx:changeStatus]
- Quota overrides must be non-negative integers (0 = unlimited); blank fields are omitted so they revert to the default [src/components/admin/QuotaOverridesForm.tsx:submit]
- Reasons are kept in the audit log and shown only to admins [src/components/admin/ReasonDialog.tsx:ReasonDialogProps]

## Side Effects

- Account mutations via `useSetUserStatus`, `useLogoutEverywhere`, `useRemoveIntroducedFeeds`, `useSetAdminRole` → `POST /api/admin/users/{id}/status|logout-everywhere|remove-introduced-feeds|admin` [src/hooks/useAdmin.ts:useAdminMutation]
- `PUT /api/admin/users/{id}/quotas` via `useSetQuotaOverrides` [src/components/admin/QuotaOverridesForm.tsx:QuotaOverridesForm]
- Reads `GET /api/admin/settings` via `useAdminSettings` [src/components/admin/NonDefaultSettingsBanner.tsx:NonDefaultSettingsBanner]
- Every admin mutation invalidates the whole `['admin']` query key [src/hooks/useAdmin.ts:useAdminMutation]
- Success/failure surfaced through `useAdminToast` [src/hooks/useAdminToast.ts:useAdminToast]

## Verification

- Run: `cd thestill/web/frontend && npm run test:run && npm run lint`
