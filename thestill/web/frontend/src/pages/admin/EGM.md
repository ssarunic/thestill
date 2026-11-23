# pages/admin

## Overview

The spec #104 admin console pages: user list, user detail, runtime settings / signup gate / alerts, and the audit log. Data comes from `hooks/useAdmin.ts` (`/api/admin/*`); the routes are wrapped in `AdminRoute` by `App.tsx`.

## Interface

- `AdminUsers` (default) — every account with activity; search, status filter, sort, paging [src/pages/admin/AdminUsers.tsx:AdminUsers]
- `AdminUserDetailPage` (default) — one account: identity, usage, follows, actions, history [src/pages/admin/AdminUserDetail.tsx:AdminUserDetailPage]
- `AdminSettings` (default) — signup gate (`signup_mode` plus a one-per-line `signup_allowlist` editor), runtime switches (`new_feeds_enabled`, `imports_enabled`, `translations_enabled`, `narration_enabled`), read-only mode, test alerts, and an alert-threshold overrides JSON editor saved as the `alerts` setting [src/pages/admin/AdminSettings.tsx:SWITCHES]
- `AdminAudit` (default) — every admin action, newest first, filterable by action and target [src/pages/admin/AdminAudit.tsx:AdminAudit]
- `MultiUserOnly` (default) — placeholder for pages with nothing to show in single-user mode [src/pages/admin/MultiUserOnly.tsx:MultiUserOnly]

## Invariants

- Users, user detail and audit render `MultiUserOnly` and disable their queries when `isMultiUser` is false [src/pages/admin/AdminUsers.tsx:AdminUsers]
- Filters, sort and page live in the URL so Back restores them [src/pages/admin/AdminUsers.tsx:AdminUsers]
- Audit filters live in the URL [src/pages/admin/AdminAudit.tsx:AdminAudit]
- The detail page is keyed by user so drafts (quota form, dialogs) never carry over [src/pages/admin/AdminUserDetail.tsx:AdminUserDetailPage]
- Turning read-only (panic) mode on requires a confirm dialog; turning it off does not [src/pages/admin/AdminSettings.tsx:AdminSettings]
- A runtime-set value shows "reset to <default>"; an env default shows "env default" [src/pages/admin/AdminSettings.tsx:SourceNote]
- The alerts editor saves only valid JSON (invalid JSON is refused with a toast, nothing is sent) [src/pages/admin/AdminSettings.tsx:AdminSettings]
- Pages carry no visible title; each keeps an `sr-only` `<h1>` (also `MultiUserOnly`) [src/pages/admin/AdminSettings.tsx:AdminSettings]

## Side Effects

- `GET /api/admin/users`, `/api/admin/users/{id}`, `/api/admin/audit`, `/api/admin/settings` [src/hooks/useAdmin.ts:useAdminUsers]
- `PUT` / `DELETE /api/admin/settings/{key}` and `POST /api/admin/alerts/test` [src/pages/admin/AdminSettings.tsx:AdminSettings]
- Admin writes invalidate the whole `['admin']` query key [src/hooks/useAdmin.ts:useAdminMutation]

## Verification

- Run: `cd thestill/web/frontend && npm run test:run && npm run lint`
